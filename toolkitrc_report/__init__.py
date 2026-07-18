"""
Battery charger log analyzer and PDF report generator.

Parses TSV-like log files produced by a ToolkitRC battery charger and
builds PDF reports with test parameters, per-cycle statistics and
plots. Both the single-file layout (every cycle of a test program in
one file) and the per-cycle layout of firmware 3.03+ (one cycle per
``Ch{N}_{Type}_{Cells}S_{Pass}.xls`` file) are supported.
"""

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import ItemParam, LogFile, LogParseError
from toolkitrc_report.report import ReportGenerator
from toolkitrc_report.scanner import DirectoryScanner
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration

__all__ = [
    'BatteryTest', 'DirectoryScanner', 'ItemParam', 'LogFile',
    'LogParseError', 'ReportGenerator', 'Segment', 'format_duration',
]
