"""
Command-line interface.

The :func:`cli` function is the ``console_scripts`` entry point
(``toolkitrc_report.cli:cli`` in ``setup.py``); the same code path is
used by ``python -m toolkitrc_report``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import LogFile, LogParseError
from toolkitrc_report.report import ReportGenerator
from toolkitrc_report.scanner import DirectoryScanner


class Application:
    """
    Command-line entry point: argument parsing and run orchestration.
    """

    args: argparse.Namespace = None

    def __init__(self, argv: Optional[List[str]] = None):
        self.args = self._parse_args(argv)

    def run(self) -> int:
        """
        Execute the selected mode; returns the process exit code.
        """

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
