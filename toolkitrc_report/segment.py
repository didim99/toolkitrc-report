"""
Data segment model.

A segment is a continuous run of data rows between two time-scale
discontinuities: either a working charge/discharge cycle (possibly a
fragment of one, when the charger clock glitches mid-cycle) or an
idle (rest) period.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List

import numpy as np

from toolkitrc_report.parser import LogFile


class Segment:
    """
    Continuous run of data rows between two time discontinuities.

    Derived series (computed power, integrated energy, relative time)
    are calculated once at construction; the segment kind is assigned
    afterwards via :meth:`finalize_kind` because classification needs
    file-scope context (the strongest segment of the same file).
    """

    KIND_CHARGE: ClassVar[str] = 'charge'
    KIND_DISCHARGE: ClassVar[str] = 'discharge'
    KIND_IDLE: ClassVar[str] = 'idle'

    #: Minimum capacity change (mAh) for a segment to count as working.
    MIN_WORK_CAPA: ClassVar[int] = 5
    #: Working current threshold, fraction of the file's peak current.
    MIN_WORK_CURRENT: ClassVar[float] = 0.25

    _source: LogFile = None
    _interval: int = None
    _start_time: int = None

    _kind: str = None
    _is_candidate: bool = None

    _rel_time: np.ndarray = None
    _data: Dict[str, np.ndarray] = None
    _vout: np.ndarray = None
    _iout: np.ndarray = None
    _power: np.ndarray = None
    _energy: np.ndarray = None
    _capa: np.ndarray = None
    _duration: int = None
    _mean_abs_i: float = None

    def __init__(self, source: LogFile, rel_time: np.ndarray,
                 data: Dict[str, np.ndarray], start_time: int):
        self._source = source
        self._rel_time = rel_time
        self._start_time = start_time
        self._duration = int(rel_time[-1]) + source.interval
        self._interval = source.interval
        self._data = data
        self._vout = data['Vout'] / 1000.0
        self._iout = data['Iout'] / 1000.0
        self._power = self._vout * self._iout
        self._energy = self._integrate(np.abs(self._power))
        self._capa = data['Capa'].astype(np.float64)
        self._mean_abs_i = float(np.mean(np.abs(self._iout)))
        self._is_candidate = (rel_time.size >= 2
                              and self.cap_mah >= self.MIN_WORK_CAPA)
        self._kind = self.KIND_IDLE

    @property
    def source(self) -> LogFile:
        return self._source

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def start_time(self) -> int:
        return self._start_time

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def is_candidate(self) -> bool:
        return self._is_candidate

    @property
    def rel_time(self) -> np.ndarray:
        return self._rel_time

    @property
    def data(self) -> Dict[str, np.ndarray]:
        return self._data

    @property
    def vout(self) -> np.ndarray:
        return self._vout

    @property
    def iout(self) -> np.ndarray:
        return self._iout

    @property
    def power(self) -> np.ndarray:
        return self._power

    @property
    def energy(self) -> np.ndarray:
        return self._energy

    @property
    def capa(self) -> np.ndarray:
        return self._capa

    @property
    def duration(self) -> int:
        return self._duration

    @property
    def mean_abs_i(self) -> float:
        return self._mean_abs_i

    @property
    def is_working(self) -> bool:
        return self._kind != self.KIND_IDLE

    @property
    def cap_mah(self) -> float:
        return float(self._capa[-1] - self._capa[0])

    @property
    def energy_wh(self) -> float:
        return float(self._energy[-1])

    @property
    def v_start(self) -> float:
        return float(self._vout[0])

    @property
    def v_end(self) -> float:
        return float(self._vout[-1])

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

        if not self._is_candidate:
            return
        working = (self._start_time == 0 or peak_current <= 0
                   or self._mean_abs_i
                   >= peak_current * self.MIN_WORK_CURRENT)
        if working:
            self._kind = (self.KIND_CHARGE
                          if float(np.sum(self._iout)) >= 0
                          else self.KIND_DISCHARGE)

    def is_full(self, v_min: float, v_max: float) -> bool:
        """
        Check whether this working cycle is a full charge/discharge.

        A full cycle starts within 10% of one voltage limit and ends
        within 2% of the opposite one.
        """

        if self._kind == self.KIND_CHARGE:
            start_ref, end_ref = v_min, v_max
        elif self._kind == self.KIND_DISCHARGE:
            start_ref, end_ref = v_max, v_min
        else:
            return False
        return (abs(self.v_start - start_ref) <= 0.10 * start_ref
                and abs(self.v_end - end_ref) <= 0.02 * end_ref)

    def _integrate(self, series: np.ndarray) -> np.ndarray:
        """
        Cumulative trapezoidal integral over time, in watt-hours.
        """

        result = np.zeros_like(series)
        if series.size > 1:
            steps = np.diff(self._rel_time)
            mean = (series[1:] + series[:-1]) / 2.0
            result[1:] = np.cumsum(mean * steps) / 3600.0
        return result

    @classmethod
    def from_slice(cls, source: LogFile, start: int,
                   stop: int) -> Segment:
        """
        Build a segment from a row range of the source file.
        """

        time = source.time[start:stop]
        data = {name: arr[start:stop]
                for name, arr in source.data.items()}
        rel = (time - time[0]).astype(np.float64)
        return cls(source, rel, data, int(time[0]))

    @classmethod
    def merge(cls, parts: List[Segment]) -> Segment:
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
        merged._kind = first.kind
        return merged
