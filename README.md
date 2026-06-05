# Thrifty-X

**Airspy-based TDOA positioning system for wildlife tracking.**

Thrifty-X is a derivative of [Thrifty](https://github.com/swkrueger/Thrifty)
by Schalk Willem Krüger (North-West University, 2016) — the original work
targets RTL-SDR.  Thrifty-X keeps the signal-processing pipeline intact and
extends the hardware support to [Airspy Mini](https://airspy.com/airspy-mini/)
and [Airspy R2](https://airspy.com/airspy-r2/), modernises the codebase for
Python 3.10+, and adds a unified Qt-based detection viewer plus a
Raspberry Pi 5 deployment story.

Version: see `thriftyx/__init__.py` (`__version__`).

## Table of Contents

1. [Documentation](#documentation)
2. [What's Changed from Original Thrifty](#whats-changed-from-original-thrifty)
3. [Supported Hardware](#supported-hardware)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [CLI Overview](#cli-overview)
7. [Typical Workflow](#typical-workflow)
8. [Capture Reference](#capture-reference)
9. [Inspecting a Capture (`analyze_detect`)](#inspecting-a-capture-analyze_detect)
10. [Detector & Signal-Processing Defaults](#detector--signal-processing-defaults)
11. [Using Existing RTL-SDR Data](#using-existing-rtl-sdr-data)
12. [Permissions / udev (Linux)](#permissions--udev-linux)
13. [Repository Layout](#repository-layout)
14. [Raspberry Pi 5 Deployment](#raspberry-pi-5-deployment)
15. [Testing](#testing)
16. [Known Limitations](#known-limitations)
17. [Publications & Attribution](#publications--attribution)
18. [License](#license)

## Documentation

All documentation is in English.

| Document | Audience |
|----------|----------|
| [docs/user_guide.md](docs/user_guide.md) | End users — install, hardware, gain tuning (incl. `--gain-mode`), template extraction, config reference, threshold tuning, command reference, troubleshooting |
| [rpi/installation_pi5.md](rpi/installation_pi5.md) | Raspberry Pi 5 + Bookworm installation |
| [docs/rpi5_deployment_report.md](docs/rpi5_deployment_report.md) | Pi 5 deployment analysis report |
| [docs/rpi5_runbook.md](docs/rpi5_runbook.md) | Pi 5 operational runbook |
| [docs/rpi5_validation_checklist.md](docs/rpi5_validation_checklist.md) | Pi 5 acceptance/validation checklist |
| [docs/verification/](docs/verification/) | Engineering verification reports (sample format, sub-offset bounds, gain mode, threshold path, auto-classify) |

## What's Changed from Original Thrifty

| Aspect | Original Thrifty | Thrifty-X |
|--------|------------------|-----------|
| SDR hardware | RTL-SDR only (8-bit, 2.4 MSPS) | RTL-SDR + Airspy Mini (12-bit, 3/6 MSPS) + Airspy R2 (12-bit, 2.5/10 MSPS) |
| Python version | 2.7 / early 3 | 3.10+ (ruff + mypy gated in CI; type hints rolling out module-by-module) |
| ADC resolution | 8-bit unsigned | 12-bit signed (Airspy) / 8-bit unsigned (RTL-SDR, auto-detected) |
| Gain control | Single `tuner_gain` | LNA + Mixer + VGA (3-stage) or combined `linearity`/`sensitivity` presets |
| AGC | n/a | Optional `--lna-agc` / `--mixer-agc` for R820T2 |
| LO correction | n/a | Software `--ppm` |
| C capture binary | `fastcard` (librtlsdr) | `fastcapture` (libairspy) |
| Detection viewer | One matplotlib window per (block × plot) | Unified Qt window with block-tab + plot-tab |
| Visualization | GnuRadio / osmosdr | matplotlib (+ PyQt5/PySide6 for the unified viewer) |
| Packaging | `setup.py` only | `pyproject.toml` + `setup.py`; dynamic version |
| Tests | Minimal | 33 test modules / 336 tests; lint + type-check + pytest + C builds gated in CI |
| Pi deployment | Pi 3 / Jessie + RTL-SDR | Pi 5 / Bookworm + Airspy with systemd, soak test, idempotent update |

**Signal-processing pipeline is preserved.** Carrier detection (Dirichlet
kernel interpolation), SoA estimation, TDOA clock correction, and
Levenberg-Marquardt position solving use the same algorithms as the
original Thrifty.  Two implementation defaults were changed for
performance reasons and can be flipped from the command line:

| Setting | Original Thrifty | Thrifty-X default | Override |
|---------|------------------|-------------------|----------|
| Carrier frequency shift | time-domain (`exp(2πj·Δf·t)`) | **`integer`** — `np.roll` in the frequency domain; ~2× faster, ~+0.03 m RMSE | `--freq-shift-method time_domain` |
| SoA sub-sample interpolation | Gaussian | **`parabolic`** — equivalent accuracy per the original paper, cheaper | `--soa-interpolation gaussian` |

## Supported Hardware

| Device | Sample Rates | Frequency Range | ADC | 12-bit USB packing |
|--------|--------------|-----------------|-----|---------------------|
| **RTL-SDR (R820T/2)** | 2.4 MSPS (typical) | ~24–1700 MHz | 8-bit unsigned | n/a |
| **Airspy Mini** | 3 MSPS / 6 MSPS | 24–1800 MHz | 12-bit signed | Optional (`--packing`) |
| **Airspy R2** | 2.5 MSPS / 10 MSPS | 24–1800 MHz | 12-bit signed | Optional — useful at 10 MSPS on USB 2.0 |

The Airspy HAL lives in `thriftyx/hal/` and talks to `libairspy` via
`ctypes`.  Device selection (index or 64-bit serial) is handled by
`thriftyx/hal/device_factory.py`.

## Requirements

- [Python](https://www.python.org/) **3.10+**
- [NumPy](https://numpy.org/) **>= 1.23**
- [SciPy](https://scipy.org/) **>= 1.9**
- [libairspy](https://github.com/airspy/airspyone_host) — required for live
  Airspy capture (not needed to process existing `.card` files)
- [librtlsdr](https://github.com/osmocom/rtl-sdr) — required for live
  RTL-SDR capture

Optional Python extras (defined in `pyproject.toml`):

| Extra | Adds | Use when… |
|-------|------|-----------|
| `analysis` | `matplotlib>=3.6` | You want `scope`, `analyze_toads`, `analyze_beacon`, `analyze_tdoa`, or the matplotlib fallback of `analyze_detect` |
| `gui` | `matplotlib>=3.6` + `PyQt5>=5.15` | You want the **unified Qt viewer** for `analyze_detect` (PySide6 is also accepted at runtime if installed separately) |
| `fft` | `pyfftw>=0.13` | Faster FFT in the capture loop (notably on Raspberry Pi 5) |
| `dev` | `pytest>=7.0`, `pytest-cov`, `mypy`, `ruff` | Running the test suite and linters |
| `all` | All of the above | Full developer install |

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

For a minimal end-user install (live capture + headless detect, no plots):

```bash
pip install -e .
```

For a headless field receiver with fast FFT but no GUI:

```bash
pip install -e ".[fft]"
```

For a workstation that only inspects data:

```bash
pip install -e ".[gui]"
```

The package exposes two equivalent console scripts — `thriftyx` and the
legacy alias `thrifty`.

## CLI Overview

All commands are dispatched via `thriftyx <command> [args]` (see
`thriftyx/cli.py`).

### Core pipeline

| Command | Purpose |
|---------|---------|
| `capture` | Capture positioning signals from an SDR (RTL-SDR / Airspy Mini / Airspy R2) into a `.card` file |
| `detect` | Detect carrier presence and estimate SoA per block; writes `.toad` files |
| `identify` | Identify transmitter IDs and filter duplicate detections |
| `match` | Match detections from multiple receivers |
| `tdoa` | Estimate TDOA by synchronising with beacon transmissions |
| `pos` | Estimate transmitter position from TDOA estimates (Levenberg-Marquardt) |

### Analysis tools

| Command | Purpose |
|---------|---------|
| `analyze_detect` | Re-run the detector on a `.card` and plot signals (unified Qt viewer with block + plot tabs, or matplotlib fallback) |
| `analyze_toads`  | Compute statistics on `.toads` data |
| `analyze_beacon` | Analyse the difference in SoA of a beacon between two receivers |
| `analyze_tdoa`   | Compute statistics on slices of TDOA data |
| `scope`          | Live time-domain + frequency-domain plot (matplotlib) |

### Utilities

| Command | Purpose |
|---------|---------|
| `template_generate` | Generate an ideal (synthetic) template |
| `template_extract`  | Extract a template from captured data |
| `gold`              | Print or analyse a Gold-code sequence |

Run `thriftyx help <command>` (or `thriftyx <command> --help`) for the
full argument list of any command.

## Typical Workflow

```bash
# 1. On each receiver — capture + detect in one step (or split into two):
thriftyx capture rx0.card --device-type airspy_mini \
    --sample-rate 6M --freq 433.83M \
    --lna-gain 5 --mixer-gain 5 --vga-gain 5
thriftyx detect rx0.card -o rx0.toad

# 2. On the central server, combine .toad files from all receivers:
thriftyx identify rx0.toad rx1.toad rx2.toad
thriftyx match
thriftyx tdoa
thriftyx pos
```

The pipeline is identical to the original Thrifty.  The legacy `thrifty`
command works as an alias for everything above.

## Capture Reference

The capture command is generic over device type; flags are interpreted by
the matching HAL.  Defaults below come from `thriftyx/settings.py`
(`DEFINITIONS`) — they are populated unconditionally, so no callsite
needs a `.get(default)` fallback.

### Device selection

| Flag | Default | Notes |
|------|---------|-------|
| `--device-type {rtlsdr, airspy_mini, airspy_r2}` | `airspy_mini` | Drives which HAL is loaded and which packing/ADC width applies |
| `-d, --device-index N` | `0` | 0-based enumeration index when multiple devices are connected |
| `--airspy-serial SERIAL` | _(unset)_ | 64-bit Airspy board serial (hex or decimal); overrides index |

### Tuning

| Flag | Default | Notes |
|------|---------|-------|
| `--sample-rate, -s` | `2.4M` | Parsed by metric-float; Airspy Mini supports 3 M / 6 M; Airspy R2 supports 2.5 M / 10 M |
| `--freq, -f`        | `433.83M` | Tuner centre frequency (Hz) |
| `--block-size, -b`  | `16384` | Samples per block; must be a power of 2 |
| `--history, -y`     | `4920`  | Sample overlap between blocks (block_history) |

### Gain — Airspy

| Flag | Default | Range | Notes |
|------|---------|-------|-------|
| `--gain-mode {manual, linearity, sensitivity}` | `manual` | — | Preset modes delegate the LNA/Mixer/VGA ladder to libairspy and force AGC off. See caveat below. |
| `--lna-gain N`   | `0` | 0–14 | Manual LNA index |
| `--mixer-gain N` | `0` | 0–15 | Manual Mixer index |
| `--vga-gain N`   | `0` | 0–15 | Manual VGA / IF index |
| `--combined-gain N` | `0` | 0–21 | Index into the preset ladder. **`0` = minimum**, **`21` = maximum** (libairspy inverts internally). Min row floors VGA at index 4, so only manual `0/0/0` reaches true zero internal gain. |
| `--lna-agc`   | `false` | bool | Engages R820T2 LNA AGC (manual mode) |
| `--mixer-agc` | `false` | bool | Engages R820T2 Mixer AGC (manual mode) |

> The `DEFINITIONS` table starts every gain at `0` so deployments must
> explicitly choose a value — there is no "safe" default.  See the
> [user guide](docs/user_guide.md#gain-tuning) for a recommended starting
> point per ADC headroom budget.

### Gain — RTL-SDR

| Flag | Default | Notes |
|------|---------|-------|
| `--gain, -g` | `0` | RTL-SDR tuner gain in dB |

### RF / USB extras

| Flag | Default | Notes |
|------|---------|-------|
| `--ppm F`     | `0`     | LO correction in ppm; positive → crystal runs fast |
| `--packing`   | `false` | Enable libairspy 12-bit USB packing (~33 % bandwidth saving; matters at 10 MSPS) |
| `--bias-tee`  | `false` | Feed DC up the antenna lead.  **Verify your chain is DC-isolated.**  A warning is printed when on |

### Selecting a specific Airspy

```bash
# Enumerate connected Airspy boards:
python3 -c "from thriftyx.hal import list_airspy_serials; \
            print([f'0x{s:016X}' for s in list_airspy_serials()])"

# Select by index (default 0):
thriftyx capture rx0.card --device-type airspy_mini -d 1

# Or pin to a serial:
thriftyx capture rx0.card --device-type airspy_mini \
    --airspy-serial 0x6440EBC51DC01ED5
```

## Inspecting a Capture (`analyze_detect`)

```bash
thriftyx analyze_detect rx0.card -m 20
```

Re-runs the detector on up to 20 detected blocks and opens a **single
unified window** with two `QTabBar`s — block index across the top and
plot family along the second row — driving a shared `FigureCanvas` with
the standard matplotlib navigation toolbar.  Switching either tab redraws
the figure in place; no per-block, per-plot pop-up windows.

### Plot families

| Family | Shows |
|--------|-------|
| `overview` | Combined summary figure (carrier, threshold, correlation, position) |
| `time`     | Time-domain I/Q of the synced and unsynced signal |
| `overlays` | Template aligned on top of the synced signal — useful for sanity-checking sub-sample SoA |
| `spectra`  | FFT magnitude, filtered carrier window, PSD |
| `corrs`    | Correlation against the template + threshold visualization |

### Options

| Flag | Default | Notes |
|------|---------|-------|
| `-m, --max N` | `20` | Process at most N detected blocks |
| `-i, --blocks RANGE` | _(none)_ | Subset specific block indices (e.g. `0-10`) |
| `-p, --plot LIST` | all | Comma-separated subset of plot families |
| `--prefer-qt / --no-prefer-qt` | `--prefer-qt` | Whether to attempt the Qt viewer; matplotlib fallback is automatic if no Qt binding is available |
| `--no-gui` | _(unset)_ | Force the matplotlib-only fallback (one figure per `(block, plot)`) |
| `--export PREFIX` | _(unset)_ | Write PNGs to `PREFIX_block<N>/<plot>.png` instead of displaying |
| `--save [PREFIX]` | _(unset)_ | Save detection signals (unsynced, synced, correlation, template, metadata) as `.npz` files with the given prefix (default `signals`) |

### Requirements

- The Qt viewer needs `pip install -e ".[gui]"` (matplotlib + PyQt5).
  PySide6 is also accepted at runtime if installed separately.
- With `--no-gui`, only matplotlib is needed (`pip install -e ".[analysis]"`).
- Plotters are constructed **lazily** per block — the viewer opens
  immediately and only pays the per-block FFT-filter + threshold cost
  when a block tab is first selected.

## Detector & Signal-Processing Defaults

Most detector options come from `thriftyx/settings.py` and are shared
with the original Thrifty.  The two settings whose Thrifty-X defaults
differ from the upstream are:

| Flag | Default | Alternatives | Trade-off |
|------|---------|--------------|-----------|
| `--freq-shift-method` | `integer` | `time_domain` | `integer` uses `np.roll` (FFT-bin shift), ~2× faster; `time_domain` multiplies by `exp(2πj·Δf·t)` and is the original.  Difference in measured RMSE is ~0.03 m on the reference dataset. |
| `--soa-interpolation` | `parabolic` | `gaussian`, `none` | `parabolic` and `gaussian` are equivalent in accuracy per the original paper.  `none` disables sub-sample refinement and is for debugging. |

Other commonly-tuned detector flags (all unchanged from upstream):

| Flag | Default | Purpose |
|------|---------|---------|
| `--carrier-window, -w` | `0--1` (whole spectrum) | Restrict carrier search to a frequency range |
| `--carrier-threshold, -t` | `15*snr` | Carrier detection threshold expression |
| `--corr-threshold, -u`    | `15*snr` | Correlation threshold expression |
| `--template, -z`          | `template.npy` | Path to the matched-filter template |
| `--rxid, -r`              | `-1` | Receiver ID stamped into output files |

For multi-TX captures (e.g. BatRF's two-collar deployment), use
`thriftyx identify --map freqmap.cfg` rather than the histogram
auto-classifier — it is more robust against very uneven per-TX
populations (see [user guide §9.2.1](docs/user_guide.md#921-identifying-transmitters-identify---map)).
For RTL-SDR with an external LNA, the default `15*snr` is often too
strict; a `10*snr` starting point is documented in
[user guide §5.5](docs/user_guide.md#55-threshold-tuning).

**Carrier sub-bin offset is bounded.** The Dirichlet-kernel
interpolator now passes `bounds=([0, -0.5], [∞, 0.5])` to
`scipy.optimize.curve_fit`, so `CarrierSyncInfo.offset` and the
`carrier_offset` column of `.toad(s)` are guaranteed in
`[-0.5, 0.5]` (Krüger §4.4.2). The correlation interpolator is
clipped to `±0.6` by `soa_estimator._clip_offset`. See
[`docs/verification/sub_offset_investigation.md`](docs/verification/sub_offset_investigation.md)
for the reasoning. If you are re-running an analysis on data captured
before this fix landed, the TX1 carrier-offset distribution will shift
by up to ±0.5 of a bin (~76 Hz at 10 Msps / 65536 FFT) on previously
out-of-bound detections.

## Using Existing RTL-SDR Data

Existing `.card` files captured with the **original** Thrifty (v1
format, 8-bit unsigned interleaved I/Q) are auto-detected by the v1/v2
header sniffer and processed correctly:

```bash
thriftyx detect old_rtlsdr_data.card -o detections.toad
```

The `block_data` module promotes 8-bit unsigned to the same complex64
representation used by Airspy 12-bit data so the rest of the pipeline is
ADC-width-agnostic.  A regression test (`tests/unit/test_block_data.py`)
guards the conversion.

## Permissions / udev (Linux)

Airspy devices appear as USB devices; ordinary users need permission to
open them.  Install the official rules and add your user to `plugdev`:

```bash
# From the airspyone_host package, or place equivalent rules manually:
sudo cp /usr/share/airspy/52-airspy.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"   # then log out / back in
```

If `airspy_open()` returns `-1000` after that, another process (often
GNU Radio / SDR# / Gqrx) holds the device open.

## Repository Layout

```
Thrifty-x/
├── thriftyx/            # ▶ Active Python package — Python 3.10+
│   ├── cli.py           #   Command dispatcher (HELP banner + MODULES)
│   ├── settings.py      #   DEFINITIONS — every CLI flag, default, parser
│   ├── airspy_capture.py
│   ├── detect.py
│   ├── detect_analysis.py # Unified Qt viewer (`analyze_detect`)
│   ├── gold.py, matchmaker.py, tdoa_est.py, pos_est.py, ...
│   └── hal/             #   Airspy/RTL-SDR HAL (ctypes-based)
├── fastcapture/         # ▶ Active C library binding to libairspy
├── fastdet/             # ▶ Active C++ correlation detector (links fastcapture)
├── thrifty/             # ◌ Reference only — original Schalk-Krüger Thrifty
├── tests/
│   ├── unit/            #   24 unit-test modules
│   ├── integration/     #   1 integration test (block_data + mock capture)
│   └── test_*.py        #   8 top-level pipeline-stage tests
├── scripts/             # Standalone analysis helper scripts
├── example/             # Example detector configs + template
├── rpi/                 # Pi 5 deployment assets (services, scripts, configs)
└── docs/                # User & deployment documentation
```

The active code (`thriftyx/`, `fastcapture/`) is what `pip install`
exposes.  `pyproject.toml` pins
`[tool.setuptools.packages.find].include = ["thriftyx*"]`, so the legacy
`thrifty/` directory is **not** packaged and should not be imported.
It is kept in the tree purely for diff/comparison.

## Raspberry Pi 5 Deployment

Thrifty-X ships with a complete Pi 5 + Bookworm deployment layout under
`rpi/`:

| File / Directory | Purpose |
|------------------|---------|
| [`rpi/installation_pi5.md`](rpi/installation_pi5.md) | Step-by-step Pi 5 install (libairspy + pyfftw + systemd) |
| `rpi/systemd/` | Capture/heartbeat unit templates |
| `rpi/detector.service` | systemd unit for the detector service |
| `rpi/thriftyx-capture.cfg.example` | Capture config template (sample rate, gain, packing, ppm) |
| `rpi/heartbeat.py` | Periodic health probe written to a known path |
| `rpi/soak_test.sh` | 24-hour stability test |
| `rpi/update_node.sh` | **Idempotent** in-place upgrade script (safe to re-run) |
| `rpi/cleanup_old_captures.sh` | Retention policy for `.card` files |
| `rpi/ntp-after-online.{service,sh}` | Force NTP sync after network is up |
| `rpi/pyFFTW-0.9.2-no-fftwl.patch` | Build patch for `pyfftw` on Pi 5 (no `long double` FFTW) |

Operational documents live under `docs/`:

- [`docs/rpi5_deployment_report.md`](docs/rpi5_deployment_report.md) — design analysis
- [`docs/rpi5_runbook.md`](docs/rpi5_runbook.md) — day-to-day operations
- [`docs/rpi5_validation_checklist.md`](docs/rpi5_validation_checklist.md) — acceptance checklist

The capture loop uses `pyfftw` when available (install with
`pip install -e ".[fft]"`) and a batched `fwrite`/flush strategy to
reduce microSD wear.

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

The suite covers, among other things:

- Airspy device enumeration, tuning, and capture-safety paths
  (`tests/unit/test_airspy_*.py`)
- The detector and its sub-sample interpolation
  (`tests/unit/test_detect.py`)
- RTL-SDR vs Airspy bit-depth handling
  (`tests/unit/test_block_data.py`, `tests/unit/test_scripts_bit_depth.py`)
- The HAL factory + base abstractions
  (`tests/unit/test_hal_*.py`)
- The unified `analyze_detect` viewer plumbing in headless mode
  (`tests/unit/test_detect_analysis_viewer.py`)
- The data-layer integration path — `block_data` 8↔12-bit conversion,
  v1/v2 `.card` round-trip, and the capture loop against a mock SDR
  (`tests/integration/test_full_pipeline.py`). The downstream
  `detect → identify → match → tdoa → pos` stages are exercised by
  unit tests in `tests/unit/` and `tests/test_*.py`, not by the
  integration test.

CI runs `ruff check`, `mypy`, the full `pytest` suite, and the
`fastcapture` and `fastdet` CMake builds on every push and pull
request. fastdet links the fastcapture static archive: the workflow
builds and installs fastcapture to `/usr/local` before configuring
fastdet — details in
[`docs/verification/c_build_ci_failure.md`](docs/verification/c_build_ci_failure.md) §7.

## Known Limitations

- **Hot-plug detection** is not handled; if a device is unplugged
  mid-capture the reader times out after ~10 s and exits.
- The C `fastcapture` binary is provided mostly for parity with the
  original `fastcard` workflow — **the Python `thriftyx capture` path is
  the recommended entry point.** Both `fastcapture` and the `fastdet`
  detector are smoke-built in CI, but only the Python capture path is
  exercised end-to-end by the test suite.
- Live capture requires the C library for the chosen SDR (`libairspy`
  for Airspy, `librtlsdr` for RTL-SDR).  Processing previously-captured
  `.card` files does not.

## Publications & Attribution

Thrifty-X is built upon the work described in:

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system
> for tracking wildlife using off-the-shelf hardware.* Master's
> dissertation, North-West University, Potchefstroom Campus.
> [https://hdl.handle.net/10394/25449](https://hdl.handle.net/10394/25449)

```bibtex
@mastersthesis{kruger2016inexpensive,
  title  = {An inexpensive hyperbolic positioning system for tracking
            wildlife using off-the-shelf hardware},
  author = {Kr{\"u}ger, Schalk Willem},
  year   = {2016},
  school = {North-West University (South Africa), Potchefstroom Campus}
}
```

Original Thrifty source:
[github.com/swkrueger/Thrifty](https://github.com/swkrueger/Thrifty).

## License

This project is licensed under the **GNU General Public License v3.0** —
see [LICENSE.txt](LICENSE.txt) for details.

Thrifty-X is a derivative work of Thrifty.  Both the original and this
derivative are distributed under the same GPL-3.0 license.
