"""
PDF report builder.

Produces a summary page with the Items and results tables, a page
with plots and statistics for every working cycle, and the overall
plots on the normalized continuous timeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Tuple

import matplotlib

# The reports are rendered off-screen, so the non-interactive
# backend must be selected before pyplot is imported.
matplotlib.use('Agg')

from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec
import matplotlib.pyplot as plt
import numpy as np

from toolkitrc_report.battery import BatteryTest
from toolkitrc_report.segment import Segment
from toolkitrc_report.utils import format_duration

#: Concatenated global time scale and data series, ready to plot.
GlobalSeries = Tuple[np.ndarray, np.ndarray]


class ReportGenerator:
    """
    PDF report builder for one battery test.
    """

    PAGE_SIZE: Tuple[float, float] = (8.27, 11.69)  # A4 portrait
    CELL_COLORS: List[str] = \
        plt.rcParams['axes.prop_cycle'].by_key()['color']

    test: BatteryTest = None
    output: Path = None

    def __init__(self, test: BatteryTest, output: Path):
        self.test = test
        self.output = output

    def build(self) -> None:
        """
        Render the report and write the PDF file.
        """

        with PdfPages(self.output) as pdf:
            self._summary_page(pdf)
            for number, segment, full in self.test.working_cycles():
                self._cycle_page(pdf, number, segment, full)
            self._overall_pages(pdf)

    # -- report pages --------------------------------------------------

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
        self._plot_cap_ene(fig.add_subplot(grid[2]), time, capa,
                           energy)
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
        self._plot_v_i(ax, time, vin, iin,
                       v_label='Input voltage, V',
                       i_label='Input current, A')
        ax = fig.add_subplot(grid[2])
        ax.plot(time, vin * iin, color='tab:green')
        ax.set_ylabel('Input power, W')
        ax.set_xlabel('Time, h')
        self._style(ax)
        pdf.savefig(fig)
        plt.close(fig)

    # -- tables --------------------------------------------------------

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

    def _cycle_table(self, ax: Axes, segment: Segment) -> None:
        cap_ah = segment.cap_mah / 1000.0
        rows = [
            ['Duration', format_duration(segment.duration)],
            ['Start voltage', '{:.3f} V'.format(segment.v_start)],
            ['End voltage', '{:.3f} V'.format(segment.v_end)],
            ['Capacity', '{:.3f} Ah'.format(cap_ah)],
            ['Energy', '{:.3f} Wh'.format(segment.energy_wh)],
        ]
        table = ax.table(cellText=rows, loc='center', cellLoc='left',
                         colWidths=[0.3, 0.3])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.0, 1.2)

    # -- plot helpers --------------------------------------------------

    def _concat(self, getter: Callable[[Segment], np.ndarray]
                ) -> GlobalSeries:
        time = np.concatenate(self.test.global_time) / 3600.0
        series = np.concatenate(
            [getter(seg) for seg in self.test.segments])
        return time, series

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

    def _plot_cells(self, ax: Axes, time: np.ndarray,
                    segment: Segment, cells: List[str]) -> None:
        for pos, name in enumerate(cells):
            color = self.CELL_COLORS[pos % len(self.CELL_COLORS)]
            ax.plot(time, segment.data[name] / 1000.0, linewidth=0.8,
                    label=name, color=color)
        ax.set_ylabel('Cell voltage, V')
        ax.set_xlabel('Time, h')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)

    def _plot_cells_concat(self, ax: Axes, cells: List[str]) -> None:
        time = np.concatenate(self.test.global_time) / 3600.0
        for pos, name in enumerate(cells):
            series = np.concatenate(
                [seg.data[name] / 1000.0
                 for seg in self.test.segments])
            color = self.CELL_COLORS[pos % len(self.CELL_COLORS)]
            ax.plot(time, series, linewidth=0.8, label=name,
                    color=color)
        ax.set_ylabel('Cell voltage, V')
        ax.set_xlabel('Time, h')
        ax.legend(loc='best', fontsize=7, ncol=4)
        self._style(ax)

    @staticmethod
    def _active_cells(segment: Segment) -> List[str]:
        return ['B{}'.format(i) for i in range(1, 9)
                if np.any(segment.data['B{}'.format(i)] > 0)]

    @staticmethod
    def _style(ax: Axes) -> None:
        ax.grid(True, alpha=0.3)
        ax.margins(x=0.01)
