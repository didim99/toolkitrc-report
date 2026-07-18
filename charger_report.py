#!/usr/bin/env python3
"""
Battery charger log analyzer and PDF report generator.

Parses TSV-like log files produced by a "smart" battery charger and
builds a PDF report with test parameters, per-cycle statistics and
plots. Two input layouts are supported:

* single-file layout: one file holds every charge/discharge cycle of
  a test program (``-f`` / ``--file``);
* per-cycle layout (firmware 3.03+): each cycle is written to its own
  ``Ch{N}_{Type}_{Cells}S_{Pass}.xls`` file, and a whole directory is
  scanned to reassemble test programs (``-d`` / ``--dir``).
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec


class LogParseError(Exception):
    """
    Raised when a log file cannot be parsed.
    """


class ItemParam:
    """
    Single ``Key:ValueUnits`` parameter from the Items/End sections.

    Numeric parameters keep their integer value and unit string
    separately; text parameters store the raw value only.
    """

    NUMERIC_RE = re.compile(r'^(-?\d+)([A-Za-z%]*)$')

    def __init__(self, key: str, raw_value: str):
        self.key = key
        self.raw_value = raw_value
        match = self.NUMERIC_RE.match(raw_value)
        self.is_numeric = match is not None
        self.value = int(match.group(1)) if match else None
        self.units = match.group(2) if match else ''

    def display_value(self) -> str:
        """
        Value formatted for the report table.

        Numeric parameters get a space between the value and units.
        """
        if self.is_numeric and self.units:
            return '{} {}'.format(self.value, self.units)
        return self.raw_value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ItemParam):
            return NotImplemented
        return (self.key, self.raw_value) == (other.key, other.raw_value)

    def __repr__(self):
        return 'ItemParam({}:{})'.format(self.key, self.raw_value)


class LogFile:
    """
    Parser and container for a single charger log file.

    Splits the file into sections (``Items``, ``Data``, ``End``,
    ``Error``), decodes the data table into numpy arrays and extracts
    metadata from per-cycle file names when present. Duplicated
    ``End``/``Error`` sections (a firmware bug) are collapsed into one.
    """

    SECTION_RE = re.compile(r'^==(\w+)==\s*$')
    NAME_RE = re.compile(
        r'^ch(?P<ch>\d+)_(?P<type>.+)_(?P<cells>\d+)s_(?P<pass>\d+)$',
        re.IGNORECASE)
    DATA_COLUMNS = ('Vin', 'Iin', 'PowerIn', 'Vout', 'Iout', 'PowerCh',
                    'Capa', 'InTmp', 'ExtTmp',
                    'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8')

    def __init__(self, path: Path):
        self.path = path
        self.items: Dict[str, ItemParam] = {}
        self.end_params: Dict[str, ItemParam] = {}
        self.int_res: List[str] = []
        self.errors: List[str] = []
        self.has_end = False
        self.time: Optional[np.ndarray] = None
        self.data: Dict[str, np.ndarray] = {}
        self.interval = 1
        self._parse_name()
        self._parse_file()

    def _parse_name(self) -> None:
        match = self.NAME_RE.match(self.path.stem)
        if match:
            self.channel: Optional[int] = int(match.group('ch'))
            self.batt_type: Optional[str] = match.group('type')
            self.cell_count: Optional[int] = int(match.group('cells'))
            self.pass_num: Optional[int] = int(match.group('pass'))
        else:
            self.channel = None
            self.batt_type = None
            self.cell_count = None
            self.pass_num = None

    @property
    def is_per_cycle(self) -> bool:
        """
        True when the file follows the per-cycle naming scheme.
        """
        return self.pass_num is not None

    @property
    def name_key(self) -> Tuple:
        """
        Battery parameters encoded in the file name, used for grouping.
        """
        return (self.channel, self.batt_type, self.cell_count)

    @staticmethod
    def parse_time(value: str) -> int:
        parts = value.split(':')
        if len(parts) != 3:
            raise LogParseError('bad time value: {!r}'.format(value))
        hours, minutes, seconds = (int(p) for p in parts)
        return hours * 3600 + minutes * 60 + seconds

    def _parse_items_line(self, line: str, target: Dict[str, ItemParam]
                          ) -> None:
        for token in line.split():
            if ':' not in token:
                continue
            key, _, value = token.partition(':')
            if key:
                target[key] = ItemParam(key, value)

    def _parse_end_line(self, line: str, seen_end: bool) -> None:
        key, sep, rest = line.partition(':')
        values = rest.split()
        if sep and len(values) > 1:
            # Full-line multi-value parameter (e.g. IntRes per cell).
            if not seen_end:
                self.int_res = [key] + values
            return
        if not seen_end:
            self._parse_items_line(line, self.end_params)

    def _parse_file(self) -> None:
        section = None
        seen_end = False
        seen_error = False
        skip_dup = False
        rows: List[List[int]] = []
        with open(self.path, 'r', errors='replace') as stream:
            for line in stream:
                line = line.rstrip('\n')
                match = self.SECTION_RE.match(line)
                if match:
                    section = match.group(1)
                    if section == 'End':
                        skip_dup = seen_end
                        seen_end = True
                        self.has_end = True
                    elif section == 'Error':
                        skip_dup = seen_error
                        seen_error = True
                    else:
                        skip_dup = False
                    continue
                if not line.strip() or skip_dup:
                    continue
                if section == 'Items':
                    self._parse_items_line(line, self.items)
                elif section == 'Data':
                    self._parse_data_line(line, rows)
                elif section == 'End':
                    self._parse_end_line(line, False)
                elif section == 'Error':
                    self.errors.append(line.strip())
        if not rows:
            raise LogParseError('no data rows in {}'.format(self.path))
        self._build_arrays(rows)

    def _parse_data_line(self, line: str, rows: List[List[int]]) -> None:
        fields = line.split()
        if not fields or ':' not in fields[0]:
            return  # column header line
        try:
            row = [self.parse_time(fields[0])]
            row += [int(f) for f in fields[1:]]
        except (ValueError, LogParseError):
            return
        expected = len(self.DATA_COLUMNS) + 1
        if len(row) < expected:
            row += [0] * (expected - len(row))
        rows.append(row[:expected])

    def _build_arrays(self, rows: List[List[int]]) -> None:
        table = np.array(rows, dtype=np.int64)
        self.time = table[:, 0]
        for idx, name in enumerate(self.DATA_COLUMNS):
            self.data[name] = table[:, idx + 1]
        diffs = np.diff(self.time)
        positive = diffs[(diffs > 0) & (diffs <= 60)]
        if positive.size:
            self.interval = int(np.median(positive))

    def items_equal(self, other: 'LogFile') -> bool:
        """
        Compare test settings with another file (program-split rule).
        """
        return self.items == other.items


class Segment:
    """
    Continuous run of data rows between two time discontinuities.

    A segment is either a working charge/discharge cycle or an idle
    (rest) period. Derived series (computed power, integrated energy,
    relative time) are calculated once at construction.
    """

    KIND_CHARGE = 'charge'
    KIND_DISCHARGE = 'discharge'
    KIND_IDLE = 'idle'

    #: Minimum capacity change (mAh) for a segment to count as working.
    MIN_WORK_CAPA = 5
    #: Working current threshold, fraction of the file's peak current.
    MIN_WORK_CURRENT = 0.25

    def __init__(self, source: LogFile, rel_time: np.ndarray,
                 data: Dict[str, np.ndarray], start_time: int):
        self.source = source
        self.rel_time = rel_time
        self.start_time = start_time
        self.duration = int(rel_time[-1]) + source.interval
        self.interval = source.interval
        self.data = data
        self.vout = data['Vout'] / 1000.0
        self.iout = data['Iout'] / 1000.0
        self.power = self.vout * self.iout
        self.energy = self._integrate(np.abs(self.power))
        self.capa = data['Capa'].astype(np.float64)
        self.mean_abs_i = float(np.mean(np.abs(self.iout)))
        self.is_candidate = (rel_time.size >= 2
                             and self.cap_mah >= self.MIN_WORK_CAPA)
        self.kind = self.KIND_IDLE

    @classmethod
    def from_slice(cls, source: LogFile, start: int, stop: int
                   ) -> 'Segment':
        """
        Build a segment from a row range of the source file.
        """
        time = source.time[start:stop]
        data = {name: arr[start:stop]
                for name, arr in source.data.items()}
        rel = (time - time[0]).astype(np.float64)
        return cls(source, rel, data, int(time[0]))

    @classmethod
    def merge(cls, parts: List['Segment']) -> 'Segment':
        """
        Join several fragments of one cycle into a single segment.

        Used for per-cycle files where clock glitches chop one working
        cycle into pieces; the time gap between fragments is collapsed
        to a single log interval.
        """
        if len(parts) == 1:
            return parts[0]
        first = parts[0]
        chunks = []
        offset = 0.0
        for part in parts:
            chunks.append(part.rel_time + offset)
            offset += part.rel_time[-1] + part.interval
        rel = np.concatenate(chunks)
        data = {name: np.concatenate([p.data[name] for p in parts])
                for name in first.data}
        merged = cls(first.source, rel, data, first.start_time)
        merged.kind = first.kind
        return merged

    def _integrate(self, series: np.ndarray) -> np.ndarray:
        """
        Cumulative trapezoidal integral over time, in watt-hours.
        """
        result = np.zeros_like(series)
        if series.size > 1:
            steps = np.diff(self.rel_time)
            mean = (series[1:] + series[:-1]) / 2.0
            result[1:] = np.cumsum(mean * steps) / 3600.0
        return result

    def finalize_kind(self, peak_current: float) -> None:
        """
        Decide whether the segment is a working cycle or idle time.

        During rest states the charger keeps reporting a small residual
        current and the capacity counter may creep, so a segment counts
        as working only if its mean current is comparable with the
        strongest segment of the same file. A time scale reset to zero
        marks an explicit cycle start and always counts as working
        (interrupted cycles and tapering CV phases run at low current).
        """
        if not self.is_candidate:
            return
        working = (self.start_time == 0 or peak_current <= 0
                   or self.mean_abs_i
                   >= peak_current * self.MIN_WORK_CURRENT)
        if working:
            self.kind = (self.KIND_CHARGE
                         if float(np.sum(self.iout)) >= 0
                         else self.KIND_DISCHARGE)

    @property
    def is_working(self) -> bool:
        return self.kind != self.KIND_IDLE

    @property
    def cap_mah(self) -> float:
        return float(self.capa[-1] - self.capa[0])

    @property
    def energy_wh(self) -> float:
        return float(self.energy[-1])

    @property
    def v_start(self) -> float:
        return float(self.vout[0])

    @property
    def v_end(self) -> float:
        return float(self.vout[-1])

    def is_full(self, v_min: float, v_max: float) -> bool:
        """
        Check whether this working cycle is a full charge/discharge.

        A full cycle starts within 10% of one voltage limit and ends
        within 2% of the opposite one.
        """
        if self.kind == self.KIND_CHARGE:
            start_ref, end_ref = v_min, v_max
        elif self.kind == self.KIND_DISCHARGE:
            start_ref, end_ref = v_max, v_min
        else:
            return False
        return (abs(self.v_start - start_ref) <= 0.10 * start_ref
                and abs(self.v_end - end_ref) <= 0.02 * end_ref)


class BatteryTest:
    """
    One complete test program for a single battery.

    Combines one or more log files, splits the data stream into
    segments, detects full cycles and computes the summary statistics
    required by the report. Also builds the normalized global timeline
    with all time discontinuities collapsed to a single log interval.
    """

    def __init__(self, files: List[LogFile], title: str):
        self.files = files
        self.title = title
        self.items = files[0].items
        self.errors = self._collect_errors()
        self.int_res = self._collect_int_res()
        self.segments: List[Segment] = []
        for log in files:
            self.segments.extend(self._split_segments(log))
        self._detect_full_cycles()
        self._build_timeline()

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
        self.global_time: List[np.ndarray] = []
        for seg in self.segments:
            self.global_time.append(seg.rel_time + offset)
            offset += seg.rel_time[-1] + seg.interval

    def working_cycles(self) -> List[Tuple[int, Segment, bool]]:
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

    @staticmethod
    def _avg_spread(values: List[float]) -> Tuple[float, Optional[float]]:
        avg = sum(values) / len(values)
        if len(values) < 2 or not avg:
            return avg, None
        return avg, (max(values) - min(values)) / avg * 100.0

    def summary(self) -> List[Tuple[str, str]]:
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
        rows: List[Tuple[str, str]] = []
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

    def _add_stat(self, rows: List[Tuple[str, str]], name: str,
                  segments: List[Segment], getter, units: str,
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


class DirectoryScanner:
    """
    Directory analyzer for the per-cycle (firmware 3.03+) layout.

    Groups files by channel and battery parameters, orders them by the
    pass counter and splits the sequence into test programs. A program
    ends when a file contains the ``End`` section, when the battery
    parameters in the file name change, when the ``Items`` settings
    change, or when the pass numbering breaks (interrupted programs and
    counter resets after a charger reboot).
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def scan(self) -> List[BatteryTest]:
        per_cycle: Dict[Tuple, List[LogFile]] = {}
        standalone: List[LogFile] = []
        for path in sorted(self.directory.iterdir()):
            if not path.is_file():
                continue
            try:
                log = LogFile(path)
            except LogParseError as exc:
                print('warning: skipped {}: {}'.format(path.name, exc),
                      file=sys.stderr)
                continue
            if log.is_per_cycle:
                per_cycle.setdefault(log.name_key, []).append(log)
            else:
                standalone.append(log)
        tests: List[BatteryTest] = []
        for key in sorted(per_cycle, key=lambda k: (k[0], str(k))):
            group = sorted(per_cycle[key], key=lambda f: f.pass_num)
            tests.extend(self._split_programs(group))
        for log in standalone:
            tests.append(BatteryTest([log], log.path.stem))
        return tests

    def _split_programs(self, group: List[LogFile]
                        ) -> List[BatteryTest]:
        programs: List[List[LogFile]] = []
        current: List[LogFile] = []
        for log in group:
            if current:
                prev = current[-1]
                boundary = (prev.has_end
                            or log.pass_num != prev.pass_num + 1
                            or not log.items_equal(prev))
                if boundary:
                    programs.append(current)
                    current = []
            current.append(log)
        if current:
            programs.append(current)
        return [self._make_test(files) for files in programs]

    @staticmethod
    def _make_test(files: List[LogFile]) -> BatteryTest:
        first, last = files[0], files[-1]
        passes = (str(first.pass_num) if first is last else
                  '{}-{}'.format(first.pass_num, last.pass_num))
        title = 'CH{}_{}_{}S_{}'.format(
            first.channel, first.batt_type, first.cell_count, passes)
        return BatteryTest(files, title)


def format_duration(seconds: int) -> str:
    """
    Format a duration in seconds as H:MM:SS.
    """
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return '{}:{:02d}:{:02d}'.format(hours, minutes, secs)


class ReportGenerator:
    """
    PDF report builder for one battery test.

    Produces a summary page with the Items and results tables, a page
    with plots and statistics for every working cycle, and the overall
    plots on the normalized continuous timeline.
    """

    PAGE_SIZE = (8.27, 11.69)  # A4 portrait, inches
    CELL_COLORS = plt.rcParams['axes.prop_cycle'].by_key()['color']

    def __init__(self, test: BatteryTest, output: Path):
        self.test = test
        self.output = output

    def build(self) -> None:
        with PdfPages(self.output) as pdf:
            self._summary_page(pdf)
            for number, segment, full in self.test.working_cycles():
                self._cycle_page(pdf, number, segment, full)
            self._overall_pages(pdf)

    # -- summary -------------------------------------------------------

    def _summary_page(self, pdf: PdfPages) -> None:
        fig = plt.figure(figsize=self.PAGE_SIZE)
        fig.suptitle('Battery test report: {}'.format(self.test.title),
                     fontsize=14, fontweight='bold')
        grid = GridSpec(2, 1, figure=fig, height_ratios=[1, 2],
                        top=0.93, bottom=0.05, hspace=0.15)
        ax_items = fig.add_subplot(grid[0])
        ax_items.set_axis_off()
        ax_items.set_title('Test parameters (Items)', fontsize=11)
        self._items_table(ax_items)
        ax_res = fig.add_subplot(grid[1])
        ax_res.set_axis_off()
        ax_res.set_title('Test results', fontsize=11)
        self._results_table(ax_res)
        pdf.savefig(fig)
        plt.close(fig)

    def _items_table(self, ax: Axes) -> None:
        params = list(self.test.items.values())
        rows = []
        for i in range(0, len(params), 2):
            row = [params[i].key, params[i].display_value()]
            if i + 1 < len(params):
                row += [params[i + 1].key,
                        params[i + 1].display_value()]
            else:
                row += ['', '']
            rows.append(row)
        table = ax.table(cellText=rows, loc='upper center',
                         cellLoc='center',
                         colWidths=[0.2, 0.3, 0.2, 0.3])
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.0, 1.5)

    def _results_table(self, ax: Axes) -> None:
        rows = self.test.summary()
        table = ax.table(cellText=rows, loc='upper center',
                         cellLoc='left', colWidths=[0.25, 0.6])
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.0, 1.5)

    # -- per-cycle pages -----------------------------------------------

    def _cycle_page(self, pdf: PdfPages, number: int,
                    segment: Segment, full: bool) -> None:
        show_temp = bool(np.any(segment.data['ExtTmp'] > 0))
        cells = self._active_cells(segment)
        n_plots = 3 + int(show_temp) + int(bool(cells))
        fig = plt.figure(figsize=self.PAGE_SIZE)
        title = 'Cycle {} ({}{})'.format(
            number, segment.kind, ', full' if full else '')
        fig.suptitle(title, fontsize=13, fontweight='bold')
        grid = GridSpec(n_plots + 1, 1, figure=fig,
                        height_ratios=[0.7] + [1] * n_plots,
                        top=0.94, bottom=0.05, hspace=0.55)
        ax_table = fig.add_subplot(grid[0])
        ax_table.set_axis_off()
        self._cycle_table(ax_table, segment)
        hours = segment.rel_time / 3600.0
        idx = 1
        self._plot_v_i(fig.add_subplot(grid[idx]), hours,
                       segment.vout, np.abs(segment.iout))
        idx += 1
        self._plot_power(fig.add_subplot(grid[idx]), hours,
                         np.abs(segment.power))
        idx += 1
        self._plot_cap_ene(fig.add_subplot(grid[idx]), hours,
                           (segment.capa - segment.capa[0]) / 1000.0,
                           segment.energy)
        idx += 1
        if show_temp:
            ax = fig.add_subplot(grid[idx])
            ax.plot(hours, segment.data['ExtTmp'], color='tab:red')
            ax.set_ylabel('Battery temp, °C')
            self._style(ax)
            idx += 1
        if cells:
            self._plot_cells(fig.add_subplot(grid[idx]), hours,
                             segment, cells)
        pdf.savefig(fig)
        plt.close(fig)

    def _cycle_table(self, ax: Axes, segment: Segment) -> None:
        rows = [
            ['Duration', format_duration(segment.duration)],
            ['Start voltage', '{:.3f} V'.format(segment.v_start)],
            ['End voltage', '{:.3f} V'.format(segment.v_end)],
            ['Capacity', '{:.3f} Ah'.format(segment.cap_mah / 1000.0)],
            ['Energy', '{:.3f} Wh'.format(segment.energy_wh)],
        ]
        table = ax.table(cellText=rows, loc='center', cellLoc='left',
                         colWidths=[0.3, 0.3])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.0, 1.2)

    # -- overall pages -------------------------------------------------

    def _concat(self, getter) -> Tuple[np.ndarray, np.ndarray]:
        time = np.concatenate(self.test.global_time) / 3600.0
        series = np.concatenate(
            [getter(seg) for seg in self.test.segments])
        return time, series

    def _overall_pages(self, pdf: PdfPages) -> None:
        test = self.test
        any_temp = any(np.any(seg.data['ExtTmp'] > 0)
                       for seg in test.segments)
        cells = sorted(set().union(
            *[set(self._active_cells(seg)) for seg in test.segments]))
        time, vout = self._concat(lambda s: s.vout)
        _, iout = self._concat(lambda s: np.abs(s.iout))
        _, power = self._concat(lambda s: np.abs(s.power))
        _, capa = self._concat(
            lambda s: (s.capa - s.capa[0]) / 1000.0)
        _, energy = self._concat(lambda s: s.energy)

        page1 = 3 + int(bool(cells))
        fig = plt.figure(figsize=self.PAGE_SIZE)
        fig.suptitle('Whole test overview', fontsize=13,
                     fontweight='bold')
        grid = GridSpec(page1, 1, figure=fig, top=0.94, bottom=0.05,
                        hspace=0.5)
        self._plot_v_i(fig.add_subplot(grid[0]), time, vout, iout)
        self._plot_power(fig.add_subplot(grid[1]), time, power)
        self._plot_cap_ene(fig.add_subplot(grid[2]), time, capa, energy)
        if cells:
            self._plot_cells_concat(fig.add_subplot(grid[3]), cells)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=self.PAGE_SIZE)
        fig.suptitle('Whole test overview (continued)', fontsize=13,
                     fontweight='bold')
        grid = GridSpec(3, 1, figure=fig, top=0.94, bottom=0.05,
                        hspace=0.5)
        ax = fig.add_subplot(grid[0])
        _, in_tmp = self._concat(lambda s: s.data['InTmp'])
        ax.plot(time, in_tmp, color='tab:orange', label='Charger')
        if any_temp:
            _, ext = self._concat(lambda s: s.data['ExtTmp'])
            ax.plot(time, ext, color='tab:red', label='Battery')
        ax.set_ylabel('Temperature, °C')
        ax.legend(loc='best', fontsize=8)
        self._style(ax)
        _, vin = self._concat(lambda s: s.data['Vin'] / 1000.0)
        _, iin = self._concat(lambda s: s.data['Iin'] / 1000.0)
        ax = fig.add_subplot(grid[1])
        self._plot_v_i(ax, time, vin, iin, v_label='Input voltage, V',
                       i_label='Input current, A')
        ax = fig.add_subplot(grid[2])
        ax.plot(time, vin * iin, color='tab:green')
        ax.set_ylabel('Input power, W')
        ax.set_xlabel('Time, h')
        self._style(ax)
        pdf.savefig(fig)
        plt.close(fig)

    # -- plot helpers --------------------------------------------------

    @staticmethod
    def _style(ax: Axes) -> None:
        ax.grid(True, alpha=0.3)
        ax.margins(x=0.01)

    def _plot_v_i(self, ax: Axes, time: np.ndarray, volt: np.ndarray,
                  amps: np.ndarray, v_label: str = 'Voltage, V',
                  i_label: str = 'Current, A') -> None:
        ax.plot(time, volt, color='tab:blue')
        ax.set_ylabel(v_label, color='tab:blue')
        ax.tick_params(axis='y', labelcolor='tab:blue')
        twin = ax.twinx()
        twin.plot(time, amps, color='tab:red', linewidth=0.8)
        twin.set_ylabel(i_label, color='tab:red')
        twin.tick_params(axis='y', labelcolor='tab:red')
        ax.set_xlabel('Time, h')
        self._style(ax)

    def _plot_power(self, ax: Axes, time: np.ndarray,
                    power: np.ndarray) -> None:
        ax.plot(time, power, color='tab:green')
        ax.set_ylabel('Power, W')
        ax.set_xlabel('Time, h')
        self._style(ax)

    def _plot_cap_ene(self, ax: Axes, time: np.ndarray,
                      cap_ah: np.ndarray, ene_wh: np.ndarray) -> None:
        ax.plot(time, cap_ah, color='tab:blue')
        ax.set_ylabel('Capacity, Ah', color='tab:blue')
        ax.tick_params(axis='y', labelcolor='tab:blue')
        twin = ax.twinx()
        twin.plot(time, ene_wh, color='tab:red', linewidth=0.8)
        twin.set_ylabel('Energy, Wh', color='tab:red')
        twin.tick_params(axis='y', labelcolor='tab:red')
        ax.set_xlabel('Time, h')
        self._style(ax)

    @staticmethod
    def _active_cells(segment: Segment) -> List[str]:
        return ['B{}'.format(i) for i in range(1, 9)
                if np.any(segment.data['B{}'.format(i)] > 0)]

    def _plot_cells(self, ax: Axes, time: np.ndarray,
                    segment: Segment, cells: List[str]) -> None:
        for pos, name in enumerate(cells):
            ax.plot(time, segment.data[name] / 1000.0, linewidth=0.8,
                    label=name,
                    color=self.CELL_COLORS[pos % len(self.CELL_COLORS)])
        ax.set_ylabel('Cell voltage, V')
        ax.set_xlabel('Time, h')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)

    def _plot_cells_concat(self, ax: Axes, cells: List[str]) -> None:
        time = np.concatenate(self.test.global_time) / 3600.0
        for pos, name in enumerate(cells):
            series = np.concatenate(
                [seg.data[name] / 1000.0 for seg in self.test.segments])
            ax.plot(time, series, linewidth=0.8, label=name,
                    color=self.CELL_COLORS[pos % len(self.CELL_COLORS)])
        ax.set_ylabel('Cell voltage, V')
        ax.set_xlabel('Time, h')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)


class Application:
    """
    Command-line entry point: argument parsing and run orchestration.
    """

    def __init__(self, argv: Optional[List[str]] = None):
        self.args = self._parse_args(argv)

    @staticmethod
    def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description='Battery charger log analyzer: builds PDF '
                        'reports from charger log files.')
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument('-f', '--file', type=Path,
                            help='process a single log file')
        source.add_argument('-d', '--dir', type=Path,
                            help='process a directory with per-cycle '
                                 'log files')
        parser.add_argument('-o', '--output', type=Path, default=None,
                            help='output directory for PDF reports '
                                 '(default: next to the input)')
        return parser.parse_args(argv)

    def run(self) -> int:
        if self.args.file is not None:
            tests = self._load_single(self.args.file)
            default_out = self.args.file.parent
        else:
            tests = DirectoryScanner(self.args.dir).scan()
            default_out = self.args.dir
        if not tests:
            print('error: no valid log files found', file=sys.stderr)
            return 1
        out_dir = self.args.output or default_out
        out_dir.mkdir(parents=True, exist_ok=True)
        for test in tests:
            target = out_dir / '{}.pdf'.format(test.title)
            ReportGenerator(test, target).build()
            print('report: {}'.format(target))
        return 0

    @staticmethod
    def _load_single(path: Path) -> List[BatteryTest]:
        try:
            log = LogFile(path)
        except (LogParseError, OSError) as exc:
            print('error: {}'.format(exc), file=sys.stderr)
            return []
        return [BatteryTest([log], path.stem)]


if __name__ == '__main__':
    sys.exit(Application().run())
