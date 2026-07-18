"""
Directory analyzer for the per-cycle (firmware 3.03+) layout.

Groups files by channel and battery parameters, orders them by the
pass counter and splits the sequence into test programs. Subdirectories
containing log files are scanned as well, with report names derived
from the directory names.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import LogFile, LogParseError

_log = logging.getLogger(__name__)


class DirectoryScanner:
    """
    Splits a directory of per-cycle log files into test programs.

    A program ends when a file contains the ``End`` section, when the
    battery parameters in the file name change, when the ``Items``
    settings change, or when the pass numbering breaks (interrupted
    programs and counter resets after a charger reboot). Files that
    don't follow the per-cycle naming scheme are treated as standalone
    single-file tests.

    The scanned directory itself and every subdirectory that
    contains ``.xls`` files are treated as candidates, and the
    directory name drives the report naming: a single test is named
    after the directory itself; several tests with identical settings
    form a test sequence for the same battery, named ``dirname-NN``
    in pass-number order; tests with differing settings keep the
    regular channel/battery naming.
    """

    _directory: Path = None

    def __init__(self, directory: Path):
        self._directory = directory

    def scan(self) -> List[BatteryTest]:
        """
        Analyze the directory tree and build the list of programs.
        """

        tests = self._scan_files(self._directory)
        self._apply_dir_naming(self._directory, tests,
                               prefix_mixed=False)
        for subdir in self._sub_candidates():
            sub_tests = self._scan_files(subdir)
            self._apply_dir_naming(subdir, sub_tests,
                                   prefix_mixed=True)
            tests.extend(sub_tests)
        return tests

    def _sub_candidates(self) -> List[Path]:
        """
        Subdirectories containing at least one ``.xls`` log file.
        """

        result = []
        for path in sorted(self._directory.iterdir()):
            if not path.is_dir():
                continue
            if any(entry.is_file() and entry.suffix.lower() == '.xls'
                   for entry in path.iterdir()):
                _log.debug('scan candidate: %s', path)
                result.append(path)
        return result

    def _scan_files(self, directory: Path) -> List[BatteryTest]:
        per_cycle: Dict[Tuple, List[LogFile]] = {}
        standalone: List[LogFile] = []
        files = [path for path in sorted(directory.iterdir())
                 if path.is_file()]
        progress = tqdm(files, unit='file', leave=False,
                        desc='Parsing {}'.format(
                            directory.resolve().name),
                        disable=len(files) < 2)
        for path in progress:
            try:
                log = LogFile(path)
            except LogParseError as exc:
                _log.warning('skipped %s: %s', path.name, exc)
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
                reason = self._boundary_reason(current[-1], log)
                if reason is not None:
                    _log.info('%s: new program starts (%s)',
                              log.path.name, reason)
                    programs.append(current)
                    current = []
            current.append(log)
        if current:
            programs.append(current)
        return [self._make_test(files) for files in programs]

    @staticmethod
    def _boundary_reason(prev: LogFile,
                         log: LogFile) -> Optional[str]:
        """
        Why a new program starts at this file, or None to continue.
        """

        if prev.has_end:
            return 'End section in {}'.format(prev.path.name)
        if log.pass_num != prev.pass_num + 1:
            return 'pass numbering break ({} -> {})'.format(
                prev.pass_num, log.pass_num)
        if not log.items_equal(prev):
            return 'test settings changed'
        return None

    @staticmethod
    def _make_test(files: List[LogFile]) -> BatteryTest:
        first, last = files[0], files[-1]
        passes = (str(first.pass_num) if first is last else
                  '{}-{}'.format(first.pass_num, last.pass_num))
        title = 'CH{}_{}_{}S_{}'.format(
            first.channel, first.batt_type, first.cell_count, passes)
        return BatteryTest(files, title)

    @staticmethod
    def _apply_dir_naming(directory: Path, tests: List[BatteryTest],
                          prefix_mixed: bool) -> None:
        """
        Rename the tests of a directory candidate.

        A single test takes the plain directory name; several tests
        with identical settings become a ``dirname-NN`` sequence
        ordered by the pass numbers; otherwise the regular names from
        :meth:`_make_test` are kept — prefixed with the directory
        name for subdirectory candidates (``prefix_mixed``), since
        equally named programs from different subdirectories would
        otherwise overwrite each other's reports.
        """

        if not tests:
            return
        name = directory.resolve().name
        if not name:
            _log.debug('%s: no usable directory name, regular '
                       'naming kept', directory)
            return
        if len(tests) == 1:
            _log.info('%s: single test, report named %r',
                      directory, name)
            tests[0].set_title(name)
            return
        first_items = tests[0].items
        if not all(test.items == first_items for test in tests[1:]):
            if prefix_mixed:
                _log.info(
                    '%s: %d tests with differing settings, regular '
                    'naming kept with %r prefix', directory,
                    len(tests), name)
                for test in tests:
                    test.set_title(
                        '{}_{}'.format(name, test.title))
            else:
                _log.info('%s: %d tests with differing settings, '
                          'regular naming kept', directory,
                          len(tests))
            return
        _log.info('%s: test sequence of %d runs with identical '
                  'settings, reports named %s-NN', directory,
                  len(tests), name)
        ordered = sorted(
            tests, key=lambda t: (t.first_pass is None,
                                  t.first_pass or 0))
        for number, test in enumerate(ordered, 1):
            test.set_title('{}-{:02d}'.format(name, number))
