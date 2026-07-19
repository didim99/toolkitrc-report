"""
PDF report builder.

Produces a summary page with the charger parameters, test results and
test summary tables, a page with plots and statistics for every
working cycle, and the overall plots on the normalized continuous
timeline.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional, Tuple

import matplotlib

# The reports are rendered off-screen, so the non-interactive
# backend must be selected before pyplot is imported.
matplotlib.use('Agg')

from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec
from matplotlib.table import Table
from matplotlib.ticker import FuncFormatter
import matplotlib.pyplot as plt
import numpy as np

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.parser import ItemParam
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration

#: Table cell: text, column span and optional background color.
TableCell = Tuple[str, int, Optional[str]]
#: Concatenated global time scale and data series, ready to plot.
GlobalSeries = Tuple[np.ndarray, np.ndarray]


class ReportGenerator:
    """
    PDF report builder for one battery test.
    """

    PAGE_SIZE: ClassVar[Tuple[float, float]] = (8.27, 11.69)  # A4
    #: Uniform table row height in inches, shared by all tables.
    TABLE_ROW_HEIGHT: ClassVar[float] = 0.28
    CELL_COLORS: ClassVar[List[str]] = \
        plt.rcParams['axes.prop_cycle'].by_key()['color']
    CHARGE_COLOR: ClassVar[str] = '#d2ffc7'
    DISCHARGE_COLOR: ClassVar[str] = '#ffc7c7'
    ITEM_NAMES: ClassVar[Dict[str, str]] = {
        'Type': 'Battery type',
        'Cells': 'Cell count',
        'Mode': 'Mode',
        'DMode': 'Discharge mode',
        'CC': 'Charge current',
        'CV': 'Charge cut-off',
        'DC': 'Discharge current',
        'DV': 'Discharge cut-off',
        'PeakV': '\u0394Peak voltage',
        'Cyc': 'Cycle count',
        'Waste': 'Waste timeout',
        'InMax': 'Max in voltage',
        'eLoad': 'Load limit',
    }
    CYCLE_TABLE_NAMES: ClassVar[Tuple[str, ...]] = (
        'Duration', 'Start voltage', 'End voltage',
        'Capacity', 'Energy', 'Status')

    _test: BatteryTest = None
    _output: Path = None

    def __init__(self, test: BatteryTest, output: Path):
        self._test = test
        self._output = output

    def build(self) -> None:
        """
        Render the report and write the PDF file.
        """

        with PdfPages(self._output) as pdf:
            self._summary_page(pdf)
            for number, segment, full in self._test.working_cycles():
                self._cycle_page(pdf, number, segment, full)
            self._overall_pages(pdf)

    # -- report pages --------------------------------------------------

    def _summary_page(self, pdf: PdfPages) -> None:
        fig = plt.figure(figsize=self.PAGE_SIZE)
        fig.suptitle(
            'Battery test report: {}'.format(self._test.title),
            fontsize=14, fontweight='bold', y=0.975)
        stamp = datetime.now().strftime('%d.%m.%Y %H:%M')
        fig.text(0.5, 0.94, 'Generated: {}'.format(stamp),
                 ha='center', fontsize=9, color='0.35')
        cycles = self._test.working_cycles()
        items_rows = (len(self._test.items) + 1) // 2
        results_rows = len(self._test.summary()) + 1
        summary_rows = len(cycles) + 1
        # Height ratios track the row counts so that the shared
        # absolute row height gives every table enough space.
        ratios = [self._table_inches(rows) for rows
                  in (items_rows, results_rows, summary_rows)]
        grid = GridSpec(3, 1, figure=fig, height_ratios=ratios,
                        top=0.905, bottom=0.04, hspace=0.3)
        ax = fig.add_subplot(grid[0])
        ax.set_axis_off()
        ax.set_title('Charger parameters', fontsize=11)
        self._items_table(ax)
        ax = fig.add_subplot(grid[1])
        ax.set_axis_off()
        ax.set_title('Test results', fontsize=11)
        self._results_table(ax)
        ax = fig.add_subplot(grid[2])
        ax.set_axis_off()
        ax.set_title('Test summary', fontsize=11)
        self._test_summary_table(ax, cycles)
        pdf.savefig(fig)
        plt.close(fig)

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
                        top=0.94, bottom=0.05, hspace=0.6)
        ax_table = fig.add_subplot(grid[0])
        ax_table.set_axis_off()
        self._cycle_table(ax_table, segment)
        time = segment.rel_time
        idx = 1
        self._plot_v_i(fig.add_subplot(grid[idx]), time,
                       segment.vout, np.abs(segment.iout))
        idx += 1
        self._plot_power(fig.add_subplot(grid[idx]), time,
                         np.abs(segment.power))
        idx += 1
        self._plot_cap_ene(fig.add_subplot(grid[idx]), time,
                           (segment.capa - segment.capa[0]) / 1000.0,
                           segment.energy)
        idx += 1
        if show_temp:
            ax = fig.add_subplot(grid[idx])
            ax.plot(time, segment.data['ExtTmp'], color='tab:red')
            ax.set_ylabel('Battery temp, °C')
            self._style(ax)
            idx += 1
        if cells:
            self._plot_cells(fig.add_subplot(grid[idx]), time,
                             segment, cells)
        pdf.savefig(fig)
        plt.close(fig)

    def _overall_pages(self, pdf: PdfPages) -> None:
        test = self._test
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
                        hspace=0.55)
        ax = fig.add_subplot(grid[0])
        self._highlight_cycles(ax)
        self._plot_v_i(ax, time, vout, iout)
        ax = fig.add_subplot(grid[1])
        self._highlight_cycles(ax)
        self._plot_power(ax, time, power)
        ax = fig.add_subplot(grid[2])
        self._highlight_cycles(ax)
        self._plot_cap_ene(ax, time, capa, energy)
        if cells:
            ax = fig.add_subplot(grid[3])
            self._highlight_cycles(ax)
            self._plot_cells_concat(ax, cells)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=self.PAGE_SIZE)
        fig.suptitle('Whole test overview (continued)', fontsize=13,
                     fontweight='bold')
        grid = GridSpec(3, 1, figure=fig, top=0.94, bottom=0.05,
                        hspace=0.55)
        ax = fig.add_subplot(grid[0])
        self._highlight_cycles(ax)
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
        self._highlight_cycles(ax)
        self._plot_v_i(ax, time, vin, iin,
                       v_label='Input voltage, V',
                       i_label='Input current, A')
        ax = fig.add_subplot(grid[2])
        self._highlight_cycles(ax)
        ax.plot(time, vin * iin, color='tab:green')
        ax.set_ylabel('Input power, W')
        self._style(ax)
        pdf.savefig(fig)
        plt.close(fig)

    # -- tables --------------------------------------------------------

    def _items_table(self, ax: Axes) -> None:
        params = list(self._test.items.values())
        rows: List[List[TableCell]] = []
        for i in range(0, len(params), 2):
            row = [(self._item_name(params[i]), 1, None),
                   (params[i].display_value(), 1, None)]
            if i + 1 < len(params):
                row += [(self._item_name(params[i + 1]), 1, None),
                        (params[i + 1].display_value(), 1, None)]
            else:
                row += [('', 1, None), ('', 1, None)]
            rows.append(row)
        self._draw_table(ax, rows, [0.26, 0.24, 0.26, 0.24])

    def _results_table(self, ax: Axes) -> None:
        rows: List[List[TableCell]] = [
            [('Parameter', 1, None), ('Charge', 1, None),
             ('Discharge', 1, None)]]
        for name, chg_value, dis_value in self._test.summary():
            row: List[TableCell] = [(name, 1, None)]
            if dis_value is None:
                row.append((chg_value, 2, None))
            else:
                row += [(chg_value, 1, None), (dis_value, 1, None)]
            rows.append(row)
        self._draw_table(ax, rows, [0.24, 0.33, 0.33],
                         align='left', header_rows=1)

    def _test_summary_table(self, ax: Axes,
                            cycles: List[Tuple[int, Segment, bool]]
                            ) -> None:
        header = ('#', 'Mode', 'Full') + self.CYCLE_TABLE_NAMES
        rows: List[List[TableCell]] = [
            [(name, 1, None) for name in header]]
        for number, segment, full in cycles:
            color = (self.CHARGE_COLOR
                     if segment.kind == Segment.KIND_CHARGE
                     else self.DISCHARGE_COLOR)
            values = (str(number), segment.kind.capitalize(),
                      'yes' if full else 'no')
            values += self._cycle_values(segment)
            rows.append([(text, 1, color) for text in values])
        widths = [0.05, 0.11, 0.07, 0.12, 0.14,
                  0.13, 0.13, 0.13, 0.12]
        self._draw_table(ax, rows, widths, font_size=8,
                         header_rows=1)

    def _cycle_table(self, ax: Axes, segment: Segment) -> None:
        rows: List[List[TableCell]] = [
            [(name, 1, None) for name in self.CYCLE_TABLE_NAMES],
            [(text, 1, None)
             for text in self._cycle_values(segment)]]
        self._draw_table(ax, rows, [0.16] * 6, font_size=8,
                         header_rows=1)

    def _draw_table(self, ax: Axes, rows: List[List[TableCell]],
                    col_widths: List[float], font_size: int = 9,
                    align: str = 'center',
                    header_rows: int = 0) -> None:
        """
        Render a table with column-span and background color support.

        All tables share the same absolute row height (and therefore
        the same vertical padding), computed from ``TABLE_ROW_HEIGHT``
        against the height of the target axes. Column spans are
        emulated by hiding the borders between the continuation cells,
        since matplotlib tables have no native ``colspan``.
        """

        fig_height = ax.figure.get_size_inches()[1]
        ax_height = ax.get_position().height * fig_height
        row_height = min(self.TABLE_ROW_HEIGHT / ax_height,
                         1.0 / len(rows))
        table = Table(ax, loc='upper center')
        table.auto_set_font_size(False)
        for r, row in enumerate(rows):
            col = 0
            for text, span, color in row:
                for k in range(span):
                    edges = 'BT'
                    if k == 0:
                        edges += 'L'
                    if k == span - 1:
                        edges += 'R'
                    cell = table.add_cell(
                        r, col + k, col_widths[col + k], row_height,
                        text=text if k == 0 else '', loc=align,
                        facecolor=color if color else 'none')
                    cell.visible_edges = edges
                    cell.set_fontsize(font_size)
                    if r < header_rows:
                        cell.get_text().set_fontweight('bold')
                col += span
        ax.add_table(table)

    def _table_inches(self, rows: int) -> float:
        """
        Required table height in inches, including the title margin.
        """

        return rows * self.TABLE_ROW_HEIGHT + 0.45

    def _item_name(self, param: ItemParam) -> str:
        key = param.key
        return self.ITEM_NAMES.get(key, key)

    # -- plot helpers --------------------------------------------------

    def _concat(self, getter: Callable[[Segment], np.ndarray]
                ) -> GlobalSeries:
        time = np.concatenate(self._test.global_time)
        series = np.concatenate(
            [getter(seg) for seg in self._test.segments])
        return time, series

    def _highlight_cycles(self, ax: Axes) -> None:
        """
        Shade charge and discharge periods on a global-time plot.
        """

        pairs = zip(self._test.segments, self._test.global_time)
        for segment, gtime in pairs:
            if not segment.is_working:
                continue
            color = (self.CHARGE_COLOR
                     if segment.kind == Segment.KIND_CHARGE
                     else self.DISCHARGE_COLOR)
            ax.fill_between([gtime[0], gtime[-1]], 0.0, 1.0,
                            transform=ax.get_xaxis_transform(),
                            color=color, linewidth=0, zorder=0)

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
        self._style(ax)

    def _plot_power(self, ax: Axes, time: np.ndarray,
                    power: np.ndarray) -> None:
        ax.plot(time, power, color='tab:green')
        ax.set_ylabel('Power, W')
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
        self._style(ax)

    def _plot_cells(self, ax: Axes, time: np.ndarray,
                    segment: Segment, cells: List[str]) -> None:
        for pos, name in enumerate(cells):
            color = self.CELL_COLORS[pos % len(self.CELL_COLORS)]
            ax.plot(time, segment.data[name] / 1000.0, linewidth=0.8,
                    label=name, color=color)
        ax.set_ylabel('Cell voltage, V')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)

    def _plot_cells_concat(self, ax: Axes, cells: List[str]) -> None:
        time = np.concatenate(self._test.global_time)
        for pos, name in enumerate(cells):
            series = np.concatenate(
                [seg.data[name] / 1000.0
                 for seg in self._test.segments])
            color = self.CELL_COLORS[pos % len(self.CELL_COLORS)]
            ax.plot(time, series, linewidth=0.8, label=name,
                    color=color)
        ax.set_ylabel('Cell voltage, V')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)

    def _style(self, ax: Axes) -> None:
        ax.grid(True, alpha=0.3)
        ax.margins(x=0.01)
        ax.set_xlabel('Time')
        ax.tick_params(axis='both', labelsize=8)
        ax.xaxis.set_major_formatter(FuncFormatter(self._time_tick))

    @staticmethod
    def _cycle_values(segment: Segment) -> Tuple[str, ...]:
        return (format_duration(segment.duration),
                '{:.3f} V'.format(segment.v_start),
                '{:.3f} V'.format(segment.v_end),
                '{:.3f} Ah'.format(segment.cap_mah / 1000.0),
                '{:.3f} Wh'.format(segment.energy_wh),
                'error' if segment.has_error else 'ok')

    @staticmethod
    def _active_cells(segment: Segment) -> List[str]:
        return ['B{}'.format(i) for i in range(1, 9)
                if np.any(segment.data['B{}'.format(i)] > 0)]

    @staticmethod
    def _time_tick(value: float, pos: int) -> str:
        if value < 0:
            return ''
        return format_duration(value)
