"""
Charger log file parser.

Decodes a single log file into sections (``Items``, ``Data``, ``End``,
``Error``), the data table as numpy arrays and metadata extracted from
per-cycle file names.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Pattern, Tuple

import numpy as np

_log = logging.getLogger(__name__)


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

    NUMERIC_RE: ClassVar[Pattern] = re.compile(r'^(-?\d+)([A-Za-z%]*)$')

    _key: str = None
    _raw_value: str = None

    _is_numeric: bool = None
    _value: Optional[int] = None
    _units: str = None

    def __init__(self, key: str, raw_value: str):
        self._key = key
        self._raw_value = raw_value
        match = self.NUMERIC_RE.match(raw_value)
        self._is_numeric = match is not None
        self._value = int(match.group(1)) if match else None
        self._units = match.group(2) if match else ''

    @property
    def key(self) -> str:
        return self._key

    @property
    def value(self) -> Optional[int]:
        return self._value

    def display_value(self) -> str:
        """
        Value formatted for the report table.

        Numeric parameters get a space between the value and units.
        """

        if self._is_numeric and self._units:
            return '{} {}'.format(self._value, self._units)
        return self._raw_value

    def __eq__(self, other: object):
        if not isinstance(other, ItemParam):
            return NotImplemented
        return ((self._key, self._raw_value)
                == (other._key, other._raw_value))

    def __repr__(self):
        return 'ItemParam({}:{})'.format(self._key, self._raw_value)


class LogFile:
    """
    Parser and container for a single charger log file.

    Splits the file into sections, decodes the data table into numpy
    arrays and extracts metadata from per-cycle file names when
    present. Duplicated ``End``/``Error`` sections (a firmware bug)
    are collapsed into one.
    """

    SECTION_RE: ClassVar[Pattern] = re.compile(r'^==(\w+)==\s*$')
    NAME_RE: ClassVar[Pattern] = re.compile(
        r'^ch(?P<ch>\d+)_(?P<type>.+)_(?P<cells>\d+)s_(?P<pass>\d+)$',
        re.IGNORECASE)
    DATA_COLUMNS: ClassVar[Tuple[str, ...]] = (
        'Vin', 'Iin', 'PowerIn', 'Vout', 'Iout', 'PowerCh',
        'Capa', 'InTmp', 'ExtTmp',
        'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8')

    _path: Path = None
    _channel: Optional[int] = None
    _batt_type: Optional[str] = None
    _cell_count: Optional[int] = None
    _pass_num: Optional[int] = None

    _items: Dict[str, ItemParam] = None
    _end_params: Dict[str, ItemParam] = None
    _int_res: List[str] = None
    _errors: List[str] = None
    _has_end: bool = None
    _time: np.ndarray = None
    _data: Dict[str, np.ndarray] = None
    _interval: int = None

    def __init__(self, path: Path):
        self._path = path
        self._items = {}
        self._end_params = {}
        self._int_res = []
        self._errors = []
        self._has_end = False
        self._data = {}
        self._interval = 1
        self._parse_name()
        self._parse_file()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def channel(self) -> Optional[int]:
        return self._channel

    @property
    def batt_type(self) -> Optional[str]:
        return self._batt_type

    @property
    def cell_count(self) -> Optional[int]:
        return self._cell_count

    @property
    def pass_num(self) -> Optional[int]:
        return self._pass_num

    @property
    def items(self) -> Dict[str, ItemParam]:
        return self._items

    @property
    def int_res(self) -> List[str]:
        return self._int_res

    @property
    def errors(self) -> List[str]:
        return self._errors

    @property
    def has_end(self) -> bool:
        return self._has_end

    @property
    def time(self) -> np.ndarray:
        return self._time

    @property
    def data(self) -> Dict[str, np.ndarray]:
        return self._data

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def is_per_cycle(self) -> bool:
        """
        True when the file follows the per-cycle naming scheme.
        """

        return self._pass_num is not None

    @property
    def name_key(self) -> Tuple:
        """
        Battery parameters encoded in the file name, used for grouping.
        """

        return (self._channel, self._batt_type, self._cell_count)

    def items_equal(self, other: LogFile) -> bool:
        """
        Compare test settings with another file (program-split rule).
        """

        return self._items == other._items

    def _parse_name(self) -> None:
        match = self.NAME_RE.match(self._path.stem)
        if match:
            self._channel = int(match.group('ch'))
            self._batt_type = match.group('type')
            self._cell_count = int(match.group('cells'))
            self._pass_num = int(match.group('pass'))

    def _parse_file(self) -> None:
        section = None
        seen_end = False
        seen_error = False
        skip_dup = False
        rows: List[List[int]] = []
        with open(self._path, 'r', errors='replace') as stream:
            for line in stream:
                line = line.rstrip('\n')
                match = self.SECTION_RE.match(line)
                if match:
                    section = match.group(1)
                    if section == 'End':
                        skip_dup = seen_end
                        seen_end = True
                        self._has_end = True
                    elif section == 'Error':
                        skip_dup = seen_error
                        seen_error = True
                    else:
                        skip_dup = False
                    if skip_dup:
                        _log.debug(
                            '%s: duplicated %s section skipped',
                            self._path.name, section)
                    continue
                if not line.strip() or skip_dup:
                    continue
                if section == 'Items':
                    self._parse_items_line(line, self._items)
                elif section == 'Data':
                    self._parse_data_line(line, rows)
                elif section == 'End':
                    self._parse_end_line(line)
                elif section == 'Error':
                    self._errors.append(line.strip())
        if not rows:
            raise LogParseError('no data rows in {}'.format(self._path))
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
            self._int_res = [key] + values
            return
        self._parse_items_line(line, self._end_params)

    def _parse_data_line(self, line: str,
                         rows: List[List[int]]) -> None:
        fields = line.split()
        if not fields or ':' not in fields[0]:
            return  # column header line
        try:
            row = [self.parse_time(fields[0])]
            row += [int(f) for f in fields[1:]]
        except (ValueError, LogParseError):
            _log.debug('%s: malformed data line ignored: %r',
                       self._path.name, line)
            return
        expected = len(self.DATA_COLUMNS) + 1
        if len(row) < expected:
            row += [0] * (expected - len(row))
        rows.append(row[:expected])

    def _build_arrays(self, rows: List[List[int]]) -> None:
        table = np.array(rows, dtype=np.int64)
        self._time = table[:, 0]
        for idx, name in enumerate(self.DATA_COLUMNS):
            self._data[name] = table[:, idx + 1]
        diffs = np.diff(self._time)
        positive = diffs[(diffs > 0) & (diffs <= 60)]
        if positive.size:
            self._interval = int(np.median(positive))
        _log.debug('%s: %d data rows, log interval %d s',
                   self._path.name, len(rows), self._interval)

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
