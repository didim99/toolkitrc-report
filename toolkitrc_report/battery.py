"""
Battery test program model.

Combines one or more log files into a complete test program for a
single battery: splits the data stream into segments, detects full
cycles and computes the summary statistics required by the report.
"""

from __future__ import annotations

import logging
from typing import Callable, ClassVar, Dict, List, Optional, Tuple

import numpy as np

from toolkitrc_report.parser import ItemParam, LogFile
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration, format_number

_log = logging.getLogger(__name__)

#: Working cycle with its 1-based number and full-cycle flag.
CycleInfo = Tuple[int, Segment, bool]
#: Summary row: name, charge value, discharge value. A ``None``
#: discharge value means the charge value spans both mode columns.
SummaryRow = Tuple[str, str, Optional[str]]


class BatteryTest:
    """
    One complete test program for a single battery.

    Also builds the normalized global timeline with all time
    discontinuities collapsed to a single log interval.
    """

    #: Value shown when a statistic cannot be computed.
    NO_VALUE: ClassVar[str] = 'n/a'
    #: Maximum relative deviation of a full cycle's duration or
    #: energy from the median of its kind before it is demoted.
    MAX_FULL_DEVIATION: ClassVar[float] = 0.25

    _files: List[LogFile] = None
    _title: str = None
    _status: str = None
    _items: Dict[str, ItemParam] = None
    _errors: List[str] = None
    _int_res: List[str] = None

    _segments: List[Segment] = None
    _full_flags: List[bool] = None
    _global_time: List[np.ndarray] = None

    def __init__(self, files: List[LogFile], title: str):
        self._files = files
        self._title = title
        self._items = files[0].items
        self._errors = self._collect_errors()
        self._int_res = self._collect_int_res()
        self._status = self._default_status()
        self._segments = []
        for log in files:
            segments = self._split_segments(log)
            self._mark_file_error(log, segments)
            self._segments.extend(segments)
        self._detect_full_cycles()
        self._build_timeline()

    @property
    def title(self) -> str:
        return self._title

    @property
    def files(self) -> List[LogFile]:
        """
        Source log files, in the order they were combined.
        """

        return self._files

    @property
    def status(self) -> str:
        """
        Human-readable test completion status for the report.
        """

        return self._status

    @property
    def first_pass(self) -> Optional[int]:
        """
        Pass number of the first log file, if per-cycle named.
        """

        return self._files[0].pass_num

    @property
    def items(self) -> Dict[str, ItemParam]:
        return self._items

    @property
    def segments(self) -> List[Segment]:
        return self._segments

    @property
    def global_time(self) -> List[np.ndarray]:
        return self._global_time

    def set_status(self, status: str) -> None:
        """
        Override the completion status (used by the scanner, which
        knows the expected file count of a test).
        """

        _log.info('%s: test status: %s', self._title, status)
        self._status = status

    def set_title(self, title: str) -> None:
        """
        Rename the test (used by directory-based report naming).
        """

        self._title = title

    def working_cycles(self) -> List[CycleInfo]:
        """
        Working segments with their 1-based index and full-cycle flag.
        """

        result = []
        number = 0
        for seg, full in zip(self._segments, self._full_flags):
            if seg.is_working:
                number += 1
                result.append((number, seg, full))
        return result

    def summary(self) -> List[SummaryRow]:
        """
        Compute the summary table rows for the report.

        Median and spread statistics are taken over full cycles only
        when at least one full cycle of that mode exists, otherwise
        over all cycles of the mode; the spread is omitted when the
        statistic is based on a single cycle. The "Battery
        efficiency" row is included only when at least one adjacent
        full discharge+charge pair exists (see
        :meth:`_efficiency_row`).
        """

        chg, chg_full = self._kind_segments(Segment.KIND_CHARGE)
        dis, dis_full = self._kind_segments(Segment.KIND_DISCHARGE)
        use_chg = chg_full if chg_full else chg
        use_dis = dis_full if dis_full else dis
        total = sum(seg.duration for seg in self._segments)
        rows: List[SummaryRow] = [
            ('Test status', self._status, None),
            ('Capacity',
             self._stat(use_chg, lambda s: s.cap_mah, 'mAh'),
             self._stat(use_dis, lambda s: s.cap_mah, 'mAh')),
            ('Energy',
             self._stat(use_chg,
                        lambda s: s.energy_wh * 1000.0, 'mWh'),
             self._stat(use_dis,
                        lambda s: s.energy_wh * 1000.0, 'mWh')),
        ]
        efficiency = self._efficiency_row()
        if efficiency is not None:
            rows.append(efficiency)
        rows += [
            ('Cycle time',
             self._stat(use_chg, lambda s: float(s.duration),
                        '', as_time=True),
             self._stat(use_dis, lambda s: float(s.duration),
                        '', as_time=True)),
            ('Cycles', str(len(chg)), str(len(dis))),
            ('Full cycles', str(len(chg_full)), str(len(dis_full))),
            ('Total time', format_duration(total), None),
        ]
        return rows

    def _default_status(self) -> str:
        """
        Completion status derived from the last file alone.

        The scanner refines it with the expected-file-count check;
        for standalone files this is the final status.
        """

        last = self._files[-1]
        if last.ends_interrupted:
            return 'error: {}'.format(last.real_errors[0])
        if not last.has_end:
            return 'incomplete (log truncated)'
        status = 'completed'
        if last.real_errors:
            status += '; warning: {}'.format(last.real_errors[0])
        return status

    def _mark_file_error(self, log: LogFile,
                         segments: List[Segment]) -> None:
        """
        Flag the last working cycle of a file that reports an error.
        """

        if not log.real_errors:
            return
        for segment in reversed(segments):
            if segment.is_working:
                segment.set_error(True)
                break

    def _kind_segments(self, kind: str
                       ) -> Tuple[List[Segment], List[Segment]]:
        """
        All working segments of a kind and the full-cycle subset.
        """

        segments = [s for s in self._segments if s.kind == kind]
        full = [s for s, f in zip(self._segments, self._full_flags)
                if f and s.kind == kind]
        return segments, full

    def _collect_errors(self) -> List[str]:
        errors: List[str] = []
        for log in self._files:
            for err in log.errors:
                if err not in errors:
                    errors.append(err)
        return errors

    def _collect_int_res(self) -> List[str]:
        for log in self._files:
            if log.int_res:
                return log.int_res
        return []

    def _cell_count(self) -> int:
        cells = self._items.get('Cells')
        if cells is not None and cells.value:
            return cells.value
        for log in self._files:
            if log.cell_count:
                return log.cell_count
        return 1

    def _detect_full_cycles(self) -> None:
        """
        Mark full cycles by voltage limits, then demote outliers.

        A cycle that reaches both voltage limits may still be bogus
        (e.g. logged after a state reset mid-cycle), so when at least
        three cycles of a kind pass the voltage test, any of them
        deviating from the median duration or energy by more than
        ``MAX_FULL_DEVIATION`` is marked as not full.
        """

        cells = self._cell_count()
        dv = self._items.get('DV')
        cv = self._items.get('CV')
        v_min = (dv.value if dv and dv.value else 0) * cells / 1000.0
        v_max = (cv.value if cv and cv.value else 0) * cells / 1000.0
        self._full_flags = [
            bool(v_min and v_max and seg.is_full(v_min, v_max))
            for seg in self._segments]
        _log.debug(
            '%s: voltage limits %.2f..%.2f V, %d of %d working '
            'cycles marked full', self._title, v_min, v_max,
            sum(self._full_flags),
            sum(1 for s in self._segments if s.is_working))
        self._demote_outliers(Segment.KIND_CHARGE)
        self._demote_outliers(Segment.KIND_DISCHARGE)

    def _demote_outliers(self, kind: str) -> None:
        indices = [i for i, seg in enumerate(self._segments)
                   if self._full_flags[i] and seg.kind == kind]
        if len(indices) < 3:
            return
        med_time = float(np.median(
            [self._segments[i].duration for i in indices]))
        med_energy = float(np.median(
            [self._segments[i].energy_wh for i in indices]))
        for i in indices:
            seg = self._segments[i]
            dev_time = abs(seg.duration - med_time) / med_time
            dev_energy = abs(seg.energy_wh - med_energy) / med_energy
            if max(dev_time, dev_energy) > self.MAX_FULL_DEVIATION:
                self._full_flags[i] = False
                _log.info(
                    '%s: %s cycle demoted from full (duration '
                    'deviates %.0f%%, energy %.0f%% from the median; '
                    'limit %.0f%%)', self._title, kind,
                    dev_time * 100, dev_energy * 100,
                    self.MAX_FULL_DEVIATION * 100)

    def _build_timeline(self) -> None:
        offset = 0.0
        self._global_time = []
        for seg in self._segments:
            self._global_time.append(seg.rel_time + offset)
            offset += seg.rel_time[-1] + seg.interval

    def _efficiency_row(self) -> Optional[SummaryRow]:
        """
        "Battery efficiency" row, or None when it can't be computed.

        Efficiency is the discharge/charge energy ratio of each
        adjacent full discharge+charge pair (in either order),
        reported as one value spanning both columns: the median
        across pairs, with the spread shown only when 2+ pairs exist.
        """

        pairs = self._efficiency_pairs()
        if not pairs:
            return None
        median, spread = self._median_spread(pairs)
        text = '{} %'.format(format_number(median))
        if spread is not None:
            text += ' (\u00b1{:.1f} %)'.format(spread)
        return 'Battery efficiency', text, None

    def _efficiency_pairs(self) -> List[float]:
        """
        Discharge/charge energy ratios (percent) of adjacent full
        cycle pairs of opposite kind, in working-cycle order.

        Full cycles are consumed two at a time: an adjacent pair of
        different kinds forms one ratio; a pair of the same kind (not
        expected in practice) yields no ratio and only the first of
        the two is consumed, so a following cycle still gets a chance
        to pair up.
        """

        full_cycles = [seg for seg, full
                      in zip(self._segments, self._full_flags)
                      if full and seg.is_working]
        ratios: List[float] = []
        index = 0
        while index + 1 < len(full_cycles):
            first, second = full_cycles[index], full_cycles[index + 1]
            if first.kind == second.kind:
                index += 1
                continue
            charge = (first if first.kind == Segment.KIND_CHARGE
                      else second)
            discharge = (first if first.kind == Segment.KIND_DISCHARGE
                        else second)
            if charge.energy_wh > 0:
                ratios.append(
                    discharge.energy_wh / charge.energy_wh * 100.0)
            index += 2
        return ratios

    def _stat(self, segments: List[Segment],
              getter: Callable[[Segment], float], units: str,
              as_time: bool = False) -> str:
        if not segments:
            return self.NO_VALUE
        median, spread = self._median_spread(
            [getter(s) for s in segments])
        if as_time:
            text = format_duration(median)
        else:
            text = '{} {}'.format(format_number(median), units)
        if spread is not None:
            text += ' (\u00b1{:.1f} %)'.format(spread)
        return text

    @staticmethod
    def _split_segments(log: LogFile) -> List[Segment]:
        """
        Split a file's data on sharp time-scale jumps and classify.

        Small positive gaps (skipped log records) stay within one
        segment; a reset to zero, any negative jump or a gap far above
        the log interval starts a new segment. Segments are classified
        relative to the strongest one of the same file, and for
        per-cycle files consecutive working fragments of the same kind
        (one cycle chopped by clock glitches) are merged back into a
        single cycle.
        """

        max_gap = log.interval * 3 + 2
        diffs = np.diff(log.time)
        breaks = np.nonzero((diffs <= 0) | (diffs > max_gap))[0] + 1
        bounds = [0] + breaks.tolist() + [log.time.size]
        segments = [Segment.from_slice(log, bounds[i], bounds[i + 1])
                    for i in range(len(bounds) - 1)]
        peak = max((s.mean_abs_i for s in segments if s.is_candidate),
                   default=0.0)
        for seg in segments:
            seg.finalize_kind(peak)
            if seg.is_candidate and not seg.is_working:
                _log.info(
                    '%s: segment demoted to idle (mean current '
                    '%.2f A below %.0f%% of file peak %.2f A)',
                    log.path.name, seg.mean_abs_i,
                    Segment.MIN_WORK_CURRENT * 100, peak)
        _log.debug('%s: split into %d segments', log.path.name,
                   len(segments))
        if log.is_per_cycle:
            segments = BatteryTest._merge_fragments(segments)
        return segments

    @staticmethod
    def _merge_fragments(segments: List[Segment]) -> List[Segment]:
        result: List[Segment] = []
        parts: List[Segment] = []
        for seg in segments:
            if parts and (not seg.is_working
                          or seg.kind != parts[-1].kind):
                result.append(Segment.merge(parts))
                parts = []
            if seg.is_working:
                parts.append(seg)
            else:
                result.append(seg)
        if parts:
            result.append(Segment.merge(parts))
        return result

    @staticmethod
    def _median_spread(values: List[float]
                       ) -> Tuple[float, Optional[float]]:
        """
        Median and half-range deviation in percent of the median.
        """

        median = float(np.median(values))
        if len(values) < 2 or not median:
            return median, None
        half_range = (max(values) - min(values)) / 2.0
        return median, half_range / median * 100.0
