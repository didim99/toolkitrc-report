## toolkitrc-report

Language: **EN** | [RU](readme-ru.md)

ToolkitRC battery charger log analyzer and PDF report generator
([report example](docs/repoert-example.pdf)).

Parses the log files written by ToolkitRC chargers and turns them
into readable PDF reports: charger settings, per-cycle and
whole-test statistics, and plots of various charge/discharge
parameters.

Tested on files recorded by a ToolkitRC M8D charger:
- firmware **3.01**: old format, one file for the whole test;
- firmware **3.06**: new format, one file per charge/discharge cycle.

### Contents

- [Installation](#installation)
- [Usage](#usage)
- [Input data](#input-data)
- [Report structure](#report-structure)
- [Analysis method](#analysis-method)
- [Integration](#integration)

---

### Installation

Requires Python 3.8+.

```bash
pip install -e .
```

To install the package itself (e.g. as an entry point in another
project's `setup.py`):

```python
entry_points={
    'console_scripts': [
        'toolkitrc-report = toolkitrc_report.cli:cli',
    ],
},
```

### Usage

The tool has two input modes, mutually exclusive:

```bash
# Old firmware: every cycle of one test is in a single log file.
python -m toolkitrc_report -f path/to/log.xls

# New firmware: one file per cycle, scanned from a directory
# (including its subdirectories — see "Directory layout" below).
python -m toolkitrc_report -d path/to/logs/
```

Or, once installed, via the console script:

```bash
toolkitrc-report -d path/to/logs/ -o path/to/reports/
```

Full option list:

```
-f, --file FILE     process a single log file
-d, --dir DIR       process a directory with per-cycle log files
-o, --output OUTPUT output directory for PDF reports
                    (default: next to the input)
-v, --verbose       log analysis decisions (-v) and low-level
                    details (-vv)
--log LOG           also write log messages to this file,
                    in addition to stderr
--strict            treat any charger setting difference between
                    files (CC/CV/DC/DV and others), not just the
                    test-defining ones, as a test break condition
                    (only affects -d mode)
```

`-v`/`-vv` are useful whenever a report's grouping, cycle
classification, or "full cycle" flags look surprising — every
non-obvious decision described below is logged at INFO or DEBUG.
matplotlib's own logger is always silenced so `-v` output stays
readable.

#### Examples

```bash
# Single old-format log file, report next to it.
python -m toolkitrc_report -f 2024-09-16_21-23-32.xls

# A directory of new-format per-cycle files, with decision logging.
python -m toolkitrc_report -d ./M8D-log -o ./reports -v

# Same, also saving the full log to a file for later inspection.
python -m toolkitrc_report -d ./M8D-log -o ./reports -vv --log run.log

# Strict mode: any CC/CV/DC/DV difference between files breaks the
# test, instead of only Mode/DMode/Cyc.
python -m toolkitrc_report -d ./M8D-log -o ./reports --strict
```

### Input data

#### Log file format

Each `.xls` file is a plain TSV-like text file (despite the
extension) with four sections:

```
==Items==
Type:LiPo Cells:4S Mode:Cycle DMode:Inter CC:500mA CV:4200mV ...

==Data==
time     Vin   Iin  PowerIn  Vout  Iout  PowerCh  Capa  InTmp ...
0:0:1    19750 145  3        16700 -500  8        1     25    ...
...

==End==
Capacity:2265mAh
IntRes1-8S(mou): 0  0  0  0 ...

==Error==
:Exceed safe capacity!
```

- **Items** — charger settings for this file: battery type, cell
  count, mode, charge/discharge current and cut-off voltage, cycle
  count, etc.
- **Data** — one row per logged second (the logging interval can be
  changed in the charger's settings — use the smallest available
  interval for the most accurate data): voltages, currents, power,
  accumulated capacity, temperatures, and per-cell voltages (up to
  8S).
- **End** — written once the file's recording stops; holds the final
  capacity and per-cell internal resistance.
- **Error** — written together with `End`; either a real error
  (e.g. `Exceed safe capacity!`) or a benign completion note
  (`NO ERROR`, `No need`).

Some firmware versions duplicate the `End`/`Error` sections; the
parser detects and collapses the duplicate.

#### Two file layouts

|                 | Old (firmware 3.01)              | New (firmware 3.03+)               |
|-----------------|----------------------------------|------------------------------------|
| Cycles per file | all cycles of a test in one file | each cycle in its own file         |
| File naming     | free-form (e.g. a timestamp)     | `Ch{N}_{Type}_{Cells}S_{Pass}.xls` |
| Selected via    | `-f`                             | `-d`                               |

#### Directory layout (`-d` mode)

The scanner treats the given directory **and every subdirectory that
contains `.xls` files** as an independent scan candidate — logs for
different batteries kept in separate subfolders are picked up
automatically, one level deep. Within each candidate, files sharing
a channel, battery type and cell count are grouped and split into
individual test runs (see [Test boundaries](#test-boundaries) below).

Report naming depends on what a candidate directory turns out to
contain:

- **one test** → the report is named after the directory itself;
- **several tests with identical settings** (a battery re-tested
  several times) → named as a *test sequence*, `<dirname>-01`,
  `<dirname>-02`, … in pass-number order, starting at 1 regardless
  of the underlying charger pass numbers;
- **several tests with different settings** → the regular
  `Ch{N}_{Type}_{Cells}S_{passes}` naming is used, prefixed with the
  directory name for subdirectories (to avoid same-named reports
  from different subfolders overwriting each other).

Files that don't match the per-cycle naming pattern are treated as
standalone single-file tests, same as `-f` mode.

#### Notes for charger users

The charger has no internal real-time clock (RTC), so every log file
is created with the same fixed 1980 timestamp — the file name is the
only reliable way to tell what is where. So plan test runs with that
in mind. On top of that, whenever the charger reboots, its internal
file counter resets to 1 regardless of how many files with the same
settings are already on the SD card, and existing files can be
silently overwritten.

> [!WARNING]
> Clearing the log folder before you start testing is the single
> most important step for avoiding confusing or incorrect reports.

A workflow that produces reliable results:

- clear the log folder on the charger's SD card before starting;
- run a test, or a sequence of tests, without power-cycling the
  charger in between (different channels can run in parallel, since
  the file name includes the channel number);
- copy the collected log files to your PC into a dedicated folder
  per battery, using a meaningful directory name — it becomes the
  report name (see "Directory layout" above);
- run the report generator on that folder and enjoy the detailed
  report.

### Report structure

One PDF per detected test, named as described above. Structure:

**Page 1 — summary**
- Title, generation timestamp, and the list of source log files
  (compacted, e.g. `CH0_LiPo_4S_{1-3}.xls`).
- **Charger parameters** — a table of the `Items` settings with
  human-readable names (`CC` → Charge current, `PeakV` → ΔPeak
  voltage, etc.).
- **Test results** — a table of capacity, energy, cycle time, and
  cycle counts, separately for charge and discharge, with median ±
  spread across cycles; the overall **battery efficiency** — median
  discharge/charge energy ratio across adjacent full cycle pairs,
  shown only when at least one such pair exists; plus the overall
  **test status** (see [Test status](#test-status)) and the total
  test time.
- **Test summary** — a table with one row per cycle: mode,
  full/not-full flag, duration, start/end voltage, capacity, energy,
  and per-file status, color-coded green for charge and red for
  discharge; a final **Eff, %** column shows each pair's efficiency
  (see [Battery efficiency](#battery-efficiency)), with the cell
  spanning the exact two rows that pair's ratio was computed from.

**Page 2 — test summary plots** (only when the test has 3 or more
working cycles, regardless of full/incomplete status) — charge/
discharge capacity and charge/discharge energy, one point per round
trip on a shared "pass number" x-axis, starting from the first round
trip; capacity and energy include every round trip regardless of
full/not-full status, since those are raw measurements either way.
A third plot adds per-pair efficiency (same pairing as the Eff, %
column, so it only has a point where both cycles of a round trip are
full — other passes are left as a gap on the shared axis); this
third plot is included only when at least two such points exist.

**One page per working cycle** — duration/voltage/capacity/energy
table plus voltage & current, power, capacity & energy plots;
battery temperature and per-cell voltage plots when the data is
present.

**Two closing pages — whole-test overview** — the same plots over
the entire test's normalized timeline (all charger-clock resets and
gaps between files collapsed into one continuous scale), with
charge/discharge periods shaded, plus charger input voltage/current/
power and temperature.

### Analysis method

The report is built from raw samples, so a number of non-obvious
calls have to be made to turn that into "cycle 3 was a full
discharge" or "this directory is one battery tested six times".
Every one of them is logged (`-v`/`-vv`). All the "value ± spread"
statistics in the report use the median, not the mean, so a single
anomalous cycle (e.g. from a misdetected full cycle) doesn't skew
the reported value more than it should.

#### Test boundaries

Files sharing a channel/battery/cell-count key are ordered by their
pass number and split into tests. A test is expected to span
`2 * Cyc` sequentially-numbered files in `Cycle` mode (one
charge-file and one discharge-file per cycle), or a single file for
any other mode. A new test starts when:

- the previous file's cycle was aborted by a real error (see below),
- the previous file contains an `End` section (explicit test
  termination),
- the expected file count (`2 * Cyc`) has been reached — the next
  file starts a **new run of the same test** (a "test sequence"),
- the pass numbering breaks (gap, or reset after a charger reboot),
- or the test-defining settings change.

If a test ends with fewer files than expected (without an error or
`End`), its data is marked incomplete rather than silently treated
as a full test — this catches partially overwritten pass sequences.

"Same settings" only compares the settings that define *what* the
test is (`Mode`, `DMode`, `Cyc`), value-wise rather than by raw
text — `CC`/`CV`/`DC`/`DV` are legitimately retuned by the user
between cycles of one test and don't break the grouping by default.
With `--strict`, any difference in any charger setting — including
`CC`/`CV`/`DC`/`DV` — breaks the test, and the same stricter
comparison is used when deciding whether a directory's tests share
"identical settings" for test-sequence naming (see "Directory
layout" above).

#### Segment classification (working cycle vs. idle)

The raw data stream is first split wherever the charger's internal
clock jumps — a reset to `0:0:0`, a negative jump, or a gap far
larger than the normal 1-second logging interval (the charger's
clock is not fully reliable, especially mid-cycle on some channels).
Each resulting segment is then classified:

- segments with a negligible capacity change are **idle**
  (rest/waste-timeout periods);
- the rest are working segments, but only if their mean current is
  at least 25% of the strongest segment in the same file — during
  rest the charger still logs a small residual current and a
  creeping capacity counter, so a capacity change alone isn't
  enough; a segment that starts exactly at `0:0:0` is always
  counted as working regardless of current, since low-current
  cycles (long CV taper, deliberately interrupted cycles) are
  legitimate;
- for per-cycle files, consecutive working fragments of the same
  direction are merged back into one cycle, since a clock glitch can
  chop a single cycle into several pieces.

#### Full cycle detection

A cycle is a candidate "full" charge/discharge if it ends within 2%
of the configured cut-off voltage (`DV`/`CV` × cell count) and
starts close enough to the opposite limit — within 10% for charge,
5% for discharge. Discharge uses a tighter start bound because it's
usually the first cycle of a test (with no matching same-test full
charge to have just come from), so a start voltage several percent
off the charge cut-off is a stronger signal of a genuinely partial
cycle than the same relative gap on a charge start.

That condition alone isn't fully reliable — a cycle that happens to
touch both voltage limits can still be spurious. For example if it's
logged right after an external state reset, or if the battery sat
unused for a long time after a partial discharge and its voltage
relaxed back toward its nominal value. So when at least three
candidates of the same kind exist, any one deviating by more than
25% (duration and/or energy) from the median of the group is
demoted back to "not full".

Median/spread statistics on the summary page use only full cycles
when at least one exists for that kind, falling back to all cycles
otherwise; the spread is omitted when only a single value is
available.

#### Battery efficiency

The efficiency row pairs up full cycles of opposite kind that are
truly adjacent — consecutive cycle numbers, with nothing (full or
not) between them — and takes the discharge/charge energy ratio of
each pair. This is the round-trip energy efficiency of that specific
pair, not a per-cycle value. Cycles are only paired in the test's
own direction: the kind of the very first working cycle (full or
not) sets that direction for the whole test, so if the test runs
discharge-then-charge, a full charge is only paired with the full
discharge immediately before it, never with a following discharge.
A full cycle whose expected neighbor isn't full, or that's separated
from its neighbor by a gap, is left unpaired rather than matched
with a more distant one.

The row is shown only when at least one such pair exists, as the
median of all pair ratios; the spread is shown only when two or more
pairs exist — same convention as every other median/spread statistic
in the report.

#### Test status

Derived from whether the last file has an `End` section, whether a
real error aborted a cycle, and the expected-vs-actual file count:

- `completed` — normal end, optionally with `; warning: <text>` if
  the firmware logged a non-fatal warning at completion;

- `error: <text>` — the test was aborted by a real error;
- `incomplete (N of M files)` — fewer files than the expected
  `2 * Cyc` were found, with no error or `End` explaining why;
- `incomplete (log truncated)` — the last file has no `End` section
  at all.

An error text only counts as aborting the cycle if the file also
ends at a substantial current — the firmware writes warnings (e.g.
"Exceed safe capacity!") into files whose cycle still completed its
normal taper, so text alone isn't a reliable abort signal.

#### Power, energy, and timelines

Power is computed as `Vout × Iout` rather than taken from the
file's own power column, whose resolution is too coarse for
meaningful integration. Energy is the trapezoidal integral of power
over time. For "whole test" plots, every segment's relative time is
offset onto one continuous scale with each clock reset collapsed to
a single log interval, so multi-file tests plot as one uninterrupted
timeline.

### Integration

The `toolkitrc_report` package can be imported directly:

```python
from pathlib import Path
from toolkitrc_report import DirectoryScanner, ReportGenerator

for test in DirectoryScanner(Path('./M8D-log')).scan():
    ReportGenerator(test, Path('./reports') / '{}.pdf'.format(
        test.title)).build()
```

Public API:
- `LogFile`, `LogParseError`, `ItemParam` (single log file parsing);
- `Segment` (one charge/discharge/idle interval);
- `BatteryTest` (one detected test program);
- `DirectoryScanner` (directory scanner);
- `ReportGenerator` (report generator);
- the `format_duration` helper function.
