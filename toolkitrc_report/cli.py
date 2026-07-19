"""
Command-line interface.

The :func:`cli` function is the ``console_scripts`` entry point
(``toolkitrc_report.cli:cli`` in ``setup.py``); the same code path is
used by ``python -m toolkitrc_report``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import LogFile, LogParseError
from toolkitrc_report.report import ReportGenerator
from toolkitrc_report.scanner import DirectoryScanner


class Application:
    """
    Command-line entry point: argument parsing and run orchestration.
    """

    #: Logging levels selected by the number of ``-v`` flags.
    LOG_LEVELS: ClassVar[Tuple[int, ...]] = (
        logging.WARNING, logging.INFO, logging.DEBUG)

    _args: argparse.Namespace = None

    def __init__(self, argv: Optional[List[str]] = None):
        self._args = self._parse_args(argv)
        self._setup_logging()

    def run(self) -> int:
        """
        Execute the selected mode; returns the process exit code.
        """

        if self._args.file is not None:
            tests = self._load_single(self._args.file)
            default_out = self._args.file.parent
        else:
            with logging_redirect_tqdm():
                tests = DirectoryScanner(self._args.dir).scan()
            default_out = self._args.dir
        if not tests:
            print('error: no valid log files found', file=sys.stderr)
            return 1
        out_dir = self._args.output or default_out
        out_dir.mkdir(parents=True, exist_ok=True)
        with logging_redirect_tqdm():
            progress = tqdm(tests, desc='Building reports',
                            unit='test', disable=len(tests) < 2)
            for test in progress:
                target = out_dir / '{}.pdf'.format(test.title)
                ReportGenerator(test, target).build()
                tqdm.write('report: {}'.format(target))
        return 0

    #: Third-party loggers that are always disabled entirely, since
    #: matplotlib floods INFO/DEBUG with font-cache and layout chatter
    #: unrelated to the report generation decisions -v is meant for.
    #: A level above CRITICAL is used (rather than the ``disabled``
    #: flag) because it is inherited by matplotlib's child loggers
    #: (``matplotlib.font_manager`` and similar), while ``disabled``
    #: only applies to the exact logger it is set on.
    DISABLED_LOGGERS: ClassVar[Tuple[str, ...]] = ('matplotlib',)

    def _setup_logging(self) -> None:
        level = self.LOG_LEVELS[
            min(self._args.verbose, len(self.LOG_LEVELS) - 1)]
        fmt = '%(levelname)s [%(name)s] %(message)s'
        handlers = [logging.StreamHandler(sys.stderr)]
        if self._args.log is not None:
            handlers.append(logging.FileHandler(
                self._args.log, encoding='utf-8'))
        logging.basicConfig(level=level, format=fmt, handlers=handlers)
        for name in self.DISABLED_LOGGERS:
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)

    @staticmethod
    def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog='toolkitrc-report',
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
        parser.add_argument('-v', '--verbose', action='count',
                            default=0,
                            help='log analysis decisions (-v) and '
                                 'low-level details (-vv)')
        parser.add_argument('--log', type=Path, default=None,
                            help='also write log messages to this '
                                 'file, in addition to stderr')
        return parser.parse_args(argv)

    @staticmethod
    def _load_single(path: Path) -> List[BatteryTest]:
        try:
            log = LogFile(path)
        except (LogParseError, OSError) as exc:
            print('error: {}'.format(exc), file=sys.stderr)
            return []
        return [BatteryTest([log], path.stem)]


def cli() -> None:
    """
    Console scripts entry point.
    """

    sys.exit(Application().run())
