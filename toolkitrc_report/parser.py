"""
Charger log file parser.

Decodes a single log file into sections (``Items``, ``Data``, ``End``,
``Error``), the data table as numpy arrays and metadata extracted from
per-cycle file names.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple

import numpy as np


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

    NUMERIC_RE: Pattern = re.compile(r'^(-?\d+)([A-Za-z%]*)$')

    key: str = None
    raw_value: str = None

    is_numeric: bool = None
    value: Optional[int] = None
    units: str = None

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

    def __eq__(self, other: object):
        if not isinstance(other, ItemParam):
            return NotImplemented
        return (self.key, self.raw_value) == (other.key, other.raw_value)

    def __repr__(self):
        return 'ItemParam({}:{})'.format(self.key, self.raw_value)


class LogFile:
    """
    Parser and container for a single charger log file.

    Splits the file into sections, decodes the data table into numpy
    arrays and extracts metadata from per-cycle file names when
    present. Duplicated ``End``/``Error`` sections (a firmware bug)
    are collapsed into one.
    """

    SECTION_RE: Pattern = re.compile(r'^==(\w+)==\s*$')
    NAME_RE: Pattern = re.compile(
        r'^ch(?P<ch>\d+)_(?P<type>.+)_(?P<cells>\d+)s_(?P<pass>\d+)$',
        re.IGNORECASE)
    DATA_COLUMNS: Tuple[str, ...] = (
        'Vin', 'Iin', 'PowerIn', 'Vout', 'Iout', 'PowerCh',
        'Capa', 'InTmp', 'ExtTmp',
        'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8')

    path: Path = None
    channel: Optional[int] = None
    batt_type: Optional[str] = None
    cell_count: Optional[int] = None
    pass_num: Optional[int] = None

    items: Dict[str, ItemParam] = None
    end_params: Dict[str, ItemParam] = None
    int_res: List[str] = None
    errors: List[str] = None
    has_end: bool = None
    time: np.ndarray = None
    data: Dict[str, np.ndarray] = None
    interval: int = None

    def __init__(self, path: Path):
        self.path = path
        self.items = {}
        self.end_params = {}
        self.int_res = []
        self.errors = []
        self.has_end = False
        self.data = {}
        self.interval = 1
        self._parse_name()
        self._parse_file()

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

    def items_equal(self, other: LogFile) -> bool:
        """
        Compare test settings with another file (program-split rule).
        """

        return self.items == other.items

    def _parse_name(self) -> None:
        match = self.NAME_RE.match(self.path.stem)
        if match:
            self.channel = int(match.group('ch'))
            self.batt_type = match.group('type')
            self.cell_count = int(match.group('cells'))
            self.pass_num = int(match.group('pass'))

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
                    self._parse_end_line(line)
                elif section == 'Error':
                    self.errors.append(line.strip())
        if not rows:
            raise LogParseError('no data rows in {}'.format(self.path))
        self._build_arrays(rows)

    def _parse_items_line(self, line: str,
                          target: Dict[str, ItemParam]) -> None:
        for token in line.split():
            if ':' not in token:
                continue
            key, _, value = token.partition(':')
            if key:
                target[key] = ItemParam(key, value)

    def _parse_end_line(self, line: str) -> None:
        key, sep, rest = line.partition(':')
        values = rest.split()
        if sep and len(values) > 1:
            # Full-line multi-value parameter (e.g. IntRes per cell).
            self.int_res = [key] + values
            return
        self._parse_items_line(line, self.end_params)

    def _parse_data_line(self, line: str,
                         rows: List[List[int]]) -> None:
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

    @staticmethod
    def parse_time(value: str) -> int:
        """
        Convert an ``h:m:s`` time stamp to seconds.
        """

        parts = value.split(':')
        if len(parts) != 3:
            raise LogParseError('bad time value: {!r}'.format(value))
        hours, minutes, seconds = (int(p) for p in parts)
        return hours * 3600 + minutes * 60 + seconds
