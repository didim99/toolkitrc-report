"""
Battery test program model.

Combines one or more log files into a complete test program for a
single battery: splits the data stream into segments, detects full
cycles and computes the summary statistics required by the report.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from toolkitrc_report.parser import ItemParam, LogFile
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration

#: Working cycle with its 1-based number and full-cycle flag.
CycleInfo = Tuple[int, Segment, bool]
#: Summary table rows as (parameter name, formatted value) pairs.
SummaryRows = List[Tuple[str, str]]


class BatteryTest:
    """
    One complete test program for a single battery.

    Also builds the normalized global timeline with all time
    discontinuities collapsed to a single log interval.
    """

    files: List[LogFile] = None
    title: str = None
    items: Dict[str, ItemParam] = None
    errors: List[str] = None
    int_res: List[str] = None

    segments: List[Segment] = None
    full_flags: List[bool] = None
    global_time: List[np.ndarray] = None

    def __init__(self, files: List[LogFile], title: str):
        self.files = files
        self.title = title
        self.items = files[0].items
        self.errors = self._collect_errors()
        self.int_res = self._collect_int_res()
        self.segments = []
        for log in files:
            self.segments.extend(self._split_segments(log))
        self._detect_full_cycles()
        self._build_timeline()

    def working_cycles(self) -> List[CycleInfo]:
        """
        Working segments with their 1-based index and full-cycle flag.
        """

        result = []
        number = 0
        for seg, full in zip(self.segments, self.full_flags):
            if seg.is_working:
                number += 1
                result.append((number, seg, full))
        return result

    def summary(self) -> SummaryRows:
        """
        Compute the report summary table as (name, value) rows.
        """

        charge = [s for s in self.segments
                  if s.kind == Segment.KIND_CHARGE]
        discharge = [s for s in self.segments
                     if s.kind == Segment.KIND_DISCHARGE]
        full_chg = [s for s, f in zip(self.segments, self.full_flags)
                    if f and s.kind == Segment.KIND_CHARGE]
        full_dsc = [s for s, f in zip(self.segments, self.full_flags)
                    if f and s.kind == Segment.KIND_DISCHARGE]
        rows: SummaryRows = []
        self._add_stat(rows, 'CapDsc', discharge,
                       lambda s: s.cap_mah, 'mAh')
        self._add_stat(rows, 'CapChg', charge,
                       lambda s: s.cap_mah, 'mAh')
        self._add_stat(rows, 'EneDsc', discharge,
                       lambda s: s.energy_wh * 1000.0, 'mWh')
        self._add_stat(rows, 'EneChg', charge,
                       lambda s: s.energy_wh * 1000.0, 'mWh')
        self._add_stat(rows, 'TimeChg', full_chg,
                       lambda s: float(s.duration), 's', as_time=True)
        rows.append(('CycChg', str(len(charge))))
        rows.append(('CycChgFull', str(len(full_chg))))
        self._add_stat(rows, 'TimeDsc', full_dsc,
                       lambda s: float(s.duration), 's', as_time=True)
        rows.append(('CycDsc', str(len(discharge))))
        rows.append(('CycDscFull', str(len(full_dsc))))
        total = sum(seg.duration for seg in self.segments)
        rows.append(('TimeTotal', format_duration(total)))
        return rows

    def _collect_errors(self) -> List[str]:
        errors: List[str] = []
        for log in self.files:
            for err in log.errors:
                if err not in errors:
                    errors.append(err)
        return errors

    def _collect_int_res(self) -> List[str]:
        for log in self.files:
            if log.int_res:
                return log.int_res
        return []

    def _cell_count(self) -> int:
        cells = self.items.get('Cells')
        if cells is not None and cells.value:
            return cells.value
        for log in self.files:
            if log.cell_count:
                return log.cell_count
        return 1

    def _detect_full_cycles(self) -> None:
        cells = self._cell_count()
        dv = self.items.get('DV')
        cv = self.items.get('CV')
        v_min = (dv.value if dv and dv.value else 0) * cells / 1000.0
        v_max = (cv.value if cv and cv.value else 0) * cells / 1000.0
        self.full_flags = [
            bool(v_min and v_max and seg.is_full(v_min, v_max))
            for seg in self.segments]

    def _build_timeline(self) -> None:
        offset = 0.0
        self.global_time = []
        for seg in self.segments:
            self.global_time.append(seg.rel_time + offset)
            offset += seg.rel_time[-1] + seg.interval

    def _add_stat(self, rows: SummaryRows, name: str,
                  segments: List[Segment],
                  getter: Callable[[Segment], float], units: str,
                  as_time: bool = False) -> None:
        if not segments:
            rows.append((name, 'n/a'))
            return
        avg, spread = self._avg_spread([getter(s) for s in segments])
        if as_time:
            text = format_duration(int(round(avg)))
        else:
            text = '{:.1f} {}'.format(avg, units)
        if spread is not None:
            text += '  (spread {:.1f} %)'.format(spread)
        rows.append((name, text))

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
        avg = sum(values) / len(values)
        if len(values) < 2 or not avg:
            return avg, None
        return avg, (max(values) - min(values)) / avg * 100.0
