"""
Battery test program model.

Combines one or more log files into a complete test program for a
single battery: splits the data stream into segments, detects full
cycles and computes the summary statistics required by the report.
"""

from __future__ import annotations

from typing import Callable, ClassVar, Dict, List, Optional, Tuple

import numpy as np

from toolkitrc_report.parser import ItemParam, LogFile
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration, format_number

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

    _files: List[LogFile] = None
    _title: str = None
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
        self._segments = []
        for log in files:
            self._segments.extend(self._split_segments(log))
        self._detect_full_cycles()
        self._build_timeline()

    @property
    def files(self) -> List[LogFile]:
        return self._files

    @property
    def title(self) -> str:
        return self._title

    @property
    def items(self) -> Dict[str, ItemParam]:
        return self._items

    @property
    def errors(self) -> List[str]:
        return self._errors

    @property
    def int_res(self) -> List[str]:
        return self._int_res

    @property
    def segments(self) -> List[Segment]:
        return self._segments

    @property
    def full_flags(self) -> List[bool]:
        return self._full_flags

    @property
    def global_time(self) -> List[np.ndarray]:
        return self._global_time

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

        Average and spread statistics are taken over full cycles only
        when at least one full cycle of that mode exists, otherwise
        over all cycles of the mode; the spread is omitted when the
        statistic is based on a single cycle.
        """

        chg, chg_full = self._kind_segments(Segment.KIND_CHARGE)
        dis, dis_full = self._kind_segments(Segment.KIND_DISCHARGE)
        use_chg = chg_full if chg_full else chg
        use_dis = dis_full if dis_full else dis
        total = sum(seg.duration for seg in self._segments)
        rows: List[SummaryRow] = [
            ('Capacity',
             self._stat(use_chg, lambda s: s.cap_mah, 'mAh'),
             self._stat(use_dis, lambda s: s.cap_mah, 'mAh')),
            ('Energy',
             self._stat(use_chg,
                        lambda s: s.energy_wh * 1000.0, 'mWh'),
             self._stat(use_dis,
                        lambda s: s.energy_wh * 1000.0, 'mWh')),
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
        cells = self._cell_count()
        dv = self._items.get('DV')
        cv = self._items.get('CV')
        v_min = (dv.value if dv and dv.value else 0) * cells / 1000.0
        v_max = (cv.value if cv and cv.value else 0) * cells / 1000.0
        self._full_flags = [
            bool(v_min and v_max and seg.is_full(v_min, v_max))
            for seg in self._segments]

    def _build_timeline(self) -> None:
        offset = 0.0
        self._global_time = []
        for seg in self._segments:
            self._global_time.append(seg.rel_time + offset)
            offset += seg.rel_time[-1] + seg.interval

    def _stat(self, segments: List[Segment],
              getter: Callable[[Segment], float], units: str,
              as_time: bool = False) -> str:
        if not segments:
            return self.NO_VALUE
        avg, spread = self._avg_spread([getter(s) for s in segments])
        if as_time:
            text = format_duration(avg)
        else:
            text = '{} {}'.format(format_number(avg), units)
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
    def _avg_spread(values: List[float]
                    ) -> Tuple[float, Optional[float]]:
        """
        Average and half-range deviation in percent of the average.
        """

        avg = sum(values) / len(values)
        if len(values) < 2 or not avg:
            return avg, None
        half_range = (max(values) - min(values)) / 2.0
        return avg, half_range / avg * 100.0
