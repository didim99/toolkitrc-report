"""
Directory analyzer for the per-cycle (firmware 3.03+) layout.

Groups files by channel and battery parameters, orders them by the
pass counter and splits the sequence into test programs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import LogFile, LogParseError


class DirectoryScanner:
    """
    Splits a directory of per-cycle log files into test programs.

    A program ends when a file contains the ``End`` section, when the
    battery parameters in the file name change, when the ``Items``
    settings change, or when the pass numbering breaks (interrupted
    programs and counter resets after a charger reboot). Files that
    don't follow the per-cycle naming scheme are treated as standalone
    single-file tests.
    """

    _directory: Path = None

    def __init__(self, directory: Path):
        self._directory = directory

    def scan(self) -> List[BatteryTest]:
        """
        Analyze the directory and build the list of test programs.
        """

        per_cycle: Dict[Tuple, List[LogFile]] = {}
        standalone: List[LogFile] = []
        for path in sorted(self._directory.iterdir()):
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
