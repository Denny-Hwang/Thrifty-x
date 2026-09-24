# Thrifty-X

**Airspy-based TDOA positioning system for wildlife tracking.**

Thrifty-X is a derivative of [Thrifty](https://github.com/swkrueger/Thrifty)
by Schalk Willem Krüger (North-West University, 2016) — the original work
targets RTL-SDR.  Thrifty-X keeps the signal-processing pipeline intact and
extends the hardware support to [Airspy Mini](https://airspy.com/airspy-mini/)
and [Airspy R2](https://airspy.com/airspy-r2/), modernises the codebase for
Python 3.10+, ports the Qt detection viewer to current Qt bindings, and
adds a Raspberry Pi 5 deployment story.

**Forked from** [swkrueger/Thrifty](https://github.com/swkrueger/Thrifty)
at commit
[`2ad9775`](https://github.com/swkrueger/Thrifty/commit/2ad9775753a8712a61c81cc78fb0bc75a921d50b)
(2019-03-04, the last upstream commit).  The original sources are not
copied into this repository; `scripts/upstream_diff.sh` fetches that
commit and diffs `thriftyx/` against it (CI publishes the per-module
summary on every run).

Version: see `thriftyx/__init__.py` (`__version__`).

## Table of Contents

1. [Documentation](#documentation)
2. [What's Changed from Original Thrifty](#whats-changed-from-original-thrifty)
3. [Supported Hardware](#supported-hardware)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [CLI Overview](#cli-overview)
7. [Typical Workflow](#typical-workflow)
8. [Transmitter Codes](#transmitter-codes)
9. [Capture Reference](#capture-reference)
10. [Inspecting a Capture (`analyze_detect`)](#inspecting-a-capture-analyze_detect)
11. [Detector & Signal-Processing Defaults](#detector--signal-processing-defaults)
12. [Using Existing RTL-SDR Data](#using-existing-rtl-sdr-data)
13. [Permissions / udev (Linux)](#permissions--udev-linux)
14. [Repository Layout](#repository-layout)
15. [Raspberry Pi 5 Deployment](#raspberry-pi-5-deployment)
16. [Testing](#testing)
17. [Known Limitations](#known-limitations)
18. [Publications & Attribution](#publications--attribution)
19. [License](#license)

## Documentation

All documentation is in English.

| Document | Audience |
|----------|----------|
| [docs/user_guide.md](docs/user_guide.md) | End users — install, hardware, gain tuning (incl. `--gain-mode`), template extraction, config reference, threshold tuning, command reference, troubleshooting |
| [rpi/installation_pi5.md](rpi/installation_pi5.md) | Raspberry Pi 5 + Bookworm installation |
| [docs/rpi5_runbook.md](docs/rpi5_runbook.md) | Pi 5 operational runbook |
| [docs/rpi5_validation_checklist.md](docs/rpi5_validation_checklist.md) | Pi 5 acceptance/validation checklist |
| [docs/design/](docs/design/) | Design proposals for features not yet built |

Reviews, investigations and their findings are recorded in pull requests
and issues, not in the tree: the repository documents how the code
works now.  Design rationale that matters for using or changing the
code lives next to that code (docstrings and comments) or in the user
guide.

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
| Detection viewer | PyQt4 window with block and plot tab bars | Same layout ported to PyQt5/PySide6, with lazy plotting, a single-window matplotlib fallback, and headless PNG export |
| Visualization | GnuRadio / osmosdr | matplotlib (+ PyQt5/PySide6 for the unified viewer) |
| Packaging | `setup.py` only | `pyproject.toml` (PEP 621); dynamic version |
| Tests | Minimal | Unit tests per module plus an end-to-end 6 MSPS capture → pos test; ruff, mypy, pytest (3.10 & 3.13), C builds, C unit tests and the int16 card round trip gated in CI |
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

In addition, the port fixes several latent bugs in the original code.
These can make outputs differ from a legacy run in corner cases, always
in the direction of correctness:

- **Strong-signal noise estimates are clamped at 0** instead of going
  NaN (`carrier_detect`, `soa_estimator`) — very clean carriers that
  the original silently *failed to detect* (NaN threshold) are now
  detected with `noise=0`.
- **Detection sorting is fixed** (`matchmaker`, `tdoa_est`): the
  original's Python-2 `cmp=` lambdas returned booleans and produced an
  invalid order, corrupting the beacon-window extraction. Byte-for-byte
  reproduction of legacy `.match`/`.tdoa` output is therefore not
  guaranteed.
- **`tdoa -s` resolves the sample rate** from CLI → `detector.cfg` →
  `device_type` default instead of the original's hardcoded 2.4 MSPS.
- **Degenerate correlation peaks** (flat/saturated) yield offset 0
  instead of NaN/inf in the `.toad` output, and a failed Dirichlet fit
  falls back to the bin centre instead of aborting the detect run.
- **Carrier sub-bin interpolation wraps circularly at spectrum edges**
  (FFT bins are periodic): at the DC edge this matches the original's
  wrapped indexing; at the high edge the original crashed.

## Supported Hardware

| Device | Sample Rates | Frequency Range | ADC | 12-bit USB packing |
|--------|--------------|-----------------|-----|---------------------|
| **RTL-SDR (R820T/2)** | 2.4 MSPS (typical) | ~24–1700 MHz | 8-bit unsigned | n/a |
| **Airspy Mini** | 3 MSPS / 6 MSPS | 24–1800 MHz | 12-bit signed | Optional (`--packing`) |
| **Airspy R2** | 2.5 MSPS / 10 MSPS | 24–1800 MHz | 12-bit signed | Optional — useful at 10 MSPS on USB 2.0 |

**Sample scale.** Every device is normalised so ADC full scale is
`|z| = 1`: RTL-SDR as `(x − 127.4) / 128`, Airspy int16 as `x / 16384`
(libairspy's INT16_IQ output is ×8 per ADC code; see
`AIRSPY_INT16_FULL_SCALE` in `thriftyx/block_data.py`). Airspy results
from before this was corrected used `/2048`, so their absolute
energy/noise columns are 8× larger; SNRs are unchanged.

The HAL lives in `thriftyx/hal/`.  `thriftyx/hal/profiles.py` holds
every hardware fact (sample rates, tuning and gain ranges, bit depth)
for each device type; the drivers, the config validator, the settings
defaults and `tdoa` all read it, so there is one place to add or
correct a device.  The Airspy driver talks to `libairspy` via `ctypes`
and is loaded only when a device is actually opened.  Capture drives
devices only through the `SDRDevice` interface, so a new driver
registered with `thriftyx.hal.register_device` (plus a profile) works
without touching capture.

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
| `gold`              | Print a code, or identify the one a capture or template holds (`--identify`) |

Run `thriftyx help <command>` (or `thriftyx <command> --help`) for the
full argument list of any command.

## Typical Workflow

```bash
# 1. Once per receiver: shared settings and a template generated at the
#    capture sample rate, for the code your transmitters send (every
#    command reads ./detector.cfg).  `thriftyx gold --identify rx0.card`
#    reports that code; see "Transmitter Codes" below.
cp example/detector_mini.cfg detector.cfg     # or detector_r2.cfg / detector.cfg (RTL-SDR)
thriftyx template_generate 11 0 -o template.npy   # upstream Thrifty transmitters' code

# 2. On each receiver: capture, then detect with that receiver's own
#    id (rx1.card with --rxid 1, and so on).
thriftyx capture rx0.card --duration 60
thriftyx detect rx0.card -o rx0.toad --rxid 0

# 3. On the central server, combine .toad files from all receivers.
#    Each step reads the previous one's default output file:
thriftyx identify rx0.toad rx1.toad rx2.toad   # -> data.toads (adds txid)
thriftyx match                                 # -> data.match
thriftyx tdoa -s 6M                            # -> data.tdoa (needs pos-rx.cfg, pos-beacon.cfg)
thriftyx pos                                   # -> data.pos  (needs pos-rx.cfg)
```

Every receiver needs its own `rxid`, set by `detect --rxid N` or by
`rxid:` in that receiver's `detector.cfg` (the example configs all say
`0`).  It is stamped into each detection and must be the id of the
receiver's line in `pos-rx.cfg`.  `capture` does not record it, so it
is set when detecting.  Detections of several receivers under one
`rxid` look like a single receiver: `match` pairs nothing, and
`identify` warns when two files hold one `rxid` over the same period.

`tdoa` and `pos` need the surveyed positions in `pos-rx.cfg` (one line
per receiver) and `pos-beacon.cfg` (one line per beacon transmitter),
in metres in any local Cartesian frame (UTM works too; `-r` / `-b`
choose other files).  Every line in both files has the same number of
coordinates: `id: x y` gives 2-D positions and needs at least 3
receivers; `id: x y z` also solves the tag's height and needs at least
4; `id: x` gives a 1-D position along the line of the receivers and
needs at least 2 (a tag beyond the outermost receiver has that
receiver's TDOAs, and is placed there).  `tdoa -s` is the receivers'
sample rate; without it `tdoa` reads `sample_rate` from `detector.cfg`,
then falls back to the `device_type` default with a warning.  A
receiver pair that never heard a beacon together gets no TDOA (it is
counted as a failure); the other pairs are still estimated.

`detect`, `analyze_detect` and `template_extract` take the sample rate,
block geometry and bit depth from the card's `#v2` header, so a card
is always processed the way it was captured, even on a machine without
the receiver's `detector.cfg`.  An explicit setting that contradicts
the header is overridden with a warning.  The template is the one input
the header cannot supply: it must be generated or extracted at the
capture's sample rate.

The pipeline is identical to the original Thrifty.  The legacy `thrifty`
command works as an alias for everything above.

## Transmitter Codes

The receiver's template must be the code the transmitters send: the
same register length, index and — for 8 and 10 bits — code family,
sampled at the capture rate.  `thriftyx gold --identify rx0.card` reads
it off a capture, no template needed:

```bash
thriftyx gold --identify rx0.card
#   rx0.card: burst of 12285 samples
#   best match: 11-bit Gold code 0: correlation 0.896 (6.001 samples/chip)
#   runner-up:  11-bit Gold code 356: correlation 0.073, inverted (5.995 samples/chip)
#   template for this code: thriftyx template_generate 11 0 --family gold --sample-rate 6M
```

It also reads a template (`.npy` or `.tpl`), to check which code a
receiver searches for.  `detect` prints the code its template holds at
the start of every run and warns when it holds none at the card's
sample rate.  A template for the wrong code or family does not simply
detect nothing: strong bursts still give "detections" on its
correlation sidelobes, with SoAs off by hundreds of samples — check the
code on a capture, not by whether detections appear.

- **Gold family** (`--family gold`).  Codes `0 … 2^N` of the Gold family
  of `N`-bit registers (N = 5, 6, 7, 9, 10, 11): any two codes
  periodically cross-correlate at most 65 of 1023 or 2047 chips
  (−23.9 dB).  The 10-bit family is the one the GPS C/A codes come from
  (`gold(10, 1025 − d)` is the GPS PRN with G2 delay `d`;
  `tests/unit/test_gold.py` checks all 32).
- **Legacy codes** (`--family legacy`).  Thrifty, and Thrifty-X until
  this was fixed, generated its 8- and 10-bit codes from register pairs
  that are not preferred pairs: periodic correlations reach 97 of 1023
  (−20.5 dB).  Transmitters programmed from that output keep needing
  them, e.g. `template_generate 10 3 --family legacy`.  An 8-bit Gold
  family cannot exist (no preferred pair when N is divisible by 4).
- **5, 6, 7, 9 and 11 bits** are the same in both families and need no
  `--family`.  The template captured from the upstream Thrifty
  transmitters (`example/template.npy`) is the 11-bit code 0.

A burst is correlated once, not periodically, so in practice the Gold
10-bit codes are about 1 dB better than the legacy ones (median peak
sidelobe −20.2 vs −19.5 dB); 11-bit codes gain about 2.5 dB more.

**Upgrading from an earlier Thrifty-X:**
- `template_generate 10 N` / `8 N` (and `gold`, and
  `scripts/chip_rate_search.py`) now stop with an error until you add
  `--family`: `--family legacy` reproduces what they generated before,
  bit for bit.  Existing template files are unaffected.
- Code indices outside `0 … 2^N` are an error (they used to wrap).
- Default `block_history` grows where it could not hold an 11-bit
  template: 5182 at 2.5 MSPS, 6206 at 3 MSPS (from 4920), 12349 at 6 MSPS
  and 20539 at 10 MSPS; block sizes do not change.  A configured
  (explicit) history that is too short is kept but now logged as a
  warning, since captures made with it can never be correlated with an
  11-bit template.  Cards keep the geometry they were captured with:
  `detect` reads it from the `#v2` header (or, for older cards, the
  `# arguments` line or the rule used when they were captured), and
  `fastdet --card` refuses a card whose recorded geometry differs from
  its arguments.

## Capture Reference

The capture command is generic over device type; flags are interpreted by
the matching HAL.  Defaults below come from `thriftyx/settings.py`
(`DEFINITIONS`), except `--sample-rate` and `--bit-depth`, whose
defaults come from the `--device-type` profile.

### Device selection

| Flag | Default | Notes |
|------|---------|-------|
| `--device-type {rtlsdr, airspy_mini, airspy_r2}` | `airspy_mini` | Selects the driver and sets the default sample rate and bit depth |
| `-d, --device-index N` | `0` | 0-based enumeration index when multiple devices are connected |
| `--airspy-serial SERIAL` | _(unset)_ | 64-bit Airspy board serial (hex or decimal); overrides index |

### Tuning

| Flag | Default | Notes |
|------|---------|-------|
| `--sample-rate, -s` | by device: `6M` Mini, `10M` R2, `2.4M` RTL-SDR | Parsed by metric-float; Airspy Mini supports 3 M / 6 M; Airspy R2 supports 2.5 M / 10 M.  `tdoa` falls back to the same default.  At 10 MSPS on a USB 2.0 host, enable `--packing`. |
| `--freq, -f`        | `433.83M` | Tuner centre frequency (Hz) |
| `--block-size, -b`  | `16384` | Samples per block; must be a power of 2 |
| `--history, -y`     | `4920` (larger above 2.4 MSPS, below) | Sample overlap between blocks (block_history) |

> **Default block parameters auto-adjust with sample rate.** When
> `block_size` / `block_history` are left at their defaults but the
> sample rate makes them too small for the template of the longest
> supported code (11 bits, 2047 chips), the loader enlarges them (logged
> at INFO): the history becomes that template + 64 samples, slack for a
> template made at a transmitter's measured chip rate.  With the
> default Airspy rates the effective defaults are therefore `32768` /
> `12349` (Mini, 6 MSPS) and `65536` / `20539` (R2, 10 MSPS), matching
> the user-guide tables; every shorter code fits as well.
> Explicitly-set values are **never** rewritten; the log says which code
> lengths they can still correlate. Note that changing `block_size`
> changes the FFT length and bin width.

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
> [user guide](docs/user_guide.md#45-gain-tuning-procedure) for a recommended starting
> point per ADC headroom budget.

### Gain — RTL-SDR

| Flag | Default | Notes |
|------|---------|-------|
| `--gain, -g` | `0` | RTL-SDR tuner gain in dB |

### RF / USB extras

| Flag | Default | Notes |
|------|---------|-------|
| `--ppm F`     | `0`     | LO correction in ppm; positive → crystal runs fast |
| `--packing`   | `false` | Enable libairspy 12-bit USB packing (25 % bandwidth saving; matters at 10 MSPS) |
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
| `--backend {auto,qt,tk,pyplot}` | `auto` | `auto` tries the Qt viewer, then falls back to a single matplotlib window.  On a machine without a display the Qt probe runs in a subprocess, so a missing display falls back instead of aborting |
| `--no-gui` | _(unset)_ | Skip Qt and use the single-window matplotlib viewer (Left/Right = block, Up/Down = plot, q = quit).  With no usable interactive backend it prints a hint to use `--export` |
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
| `--carrier-window, -w` | `0--1` (whole spectrum) | Restrict carrier search to a range of FFT bins (`7-130`), or of frequencies when it ends in `Hz` (`1-19kHz`) |
| `--carrier-threshold, -t` | `15*snr` | Carrier detection threshold expression |
| `--corr-threshold, -u`    | `15*snr` | Correlation threshold expression |
| `--template, -z`          | `template.npy` | Path to the matched-filter template |
| `--rxid, -r`              | `-1` | Receiver ID stamped into each detection: unique per receiver, the id of its line in `pos-rx.cfg` |

For multi-TX captures (e.g. BatRF's two-collar deployment), use
`thriftyx identify --map freqmap.cfg` rather than the histogram
auto-classifier — it is more robust against very uneven per-TX
populations (see [user guide §9.2.1](docs/user_guide.md#921-identifying-transmitters-identify---map)).
For RTL-SDR with an external LNA, the default `15*snr` is often too
strict; a `10*snr` starting point is documented in
[user guide §5.5](docs/user_guide.md#55-threshold-tuning).

**Carrier sub-bin offset.** `CarrierSyncInfo.offset` and the
`carrier_offset` column of `.toad(s)` lie in `[-0.5, 0.5]`: the
Dirichlet-kernel fit may move the carrier anywhere inside its ±3-bin
window, and the reported bin is then re-centred on the bin nearest the
fitted frequency.  The carrier's main lobe is `block_size /
template_len` bins wide (6.4 at 10 Msps) and flat on top, so under
noise the largest bin is often not the nearest one; the earlier fit
clipped at ±0.5 bin around the largest bin and was biased by up to the
lobe width (2.5× the RMS frequency error on synthetic R2 data).  Data
processed before this change can show `carrier_bin` values one bin
different for the same transmission.

The correlation-peak offset is clipped to `±0.6` sample by
`soa_estimator._clip_offset`: a three-point parabolic or Gaussian fit
can land slightly beyond half a sample when the peak straddles two
samples, and those values are kept, while runaway fits on flat or
saturated peaks are bounded (`fastdet` clips at `±0.5`).

## Using Existing RTL-SDR Data

Existing `.card` files captured with the **original** Thrifty (v1
format, 8-bit unsigned interleaved I/Q, no header) are recognised by
their missing `#v2` header and decoded as 8-bit.  A v1 card does not
record how it was captured, so process it with the RTL-SDR settings it
was captured with:

```bash
thriftyx detect old_rtlsdr_data.card -o detections.toad -c example/detector.cfg
```

The `block_data` module promotes 8-bit unsigned to the same complex64
representation used by Airspy 12-bit data so the rest of the pipeline is
ADC-width-agnostic.  A regression test (`tests/unit/test_block_data.py`)
guards the conversion.

## Permissions / udev (Linux)

Airspy devices appear as USB devices; ordinary users need permission to
open them.  Debian, Ubuntu and Raspberry Pi OS's `libairspy0` package
already installs the rules (`/usr/lib/udev/rules.d/60-libairspy0.rules`,
group `plugdev`), so only the group membership is needed:

```bash
sudo apt install airspy            # pulls in libairspy0 and its rules
sudo usermod -aG plugdev "$USER"   # then log out / back in
```

With libairspy built from source, install its rules file yourself:
`sudo cp airspyone_host/airspy-tools/52-airspy.rules /etc/udev/rules.d/ &&
sudo udevadm control --reload && sudo udevadm trigger`.

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
│   └── hal/             #   SDR HAL: profiles.py (device facts), drivers
├── fastcapture/         # ▶ Active C library binding to libairspy
├── fastdet/             # ▶ Active C++ correlation detector (links fastcapture)
├── tests/
│   ├── unit/            #   Unit tests, one module per area
│   ├── integration/     #   End-to-end pipeline tests
│   ├── mocks/           #   Scripted SDR devices and signal generators
│   └── test_*.py        #   Tests carried over from upstream Thrifty
├── scripts/             # Helper scripts, e.g. card_stats.py (headroom vs ADC
│                        #   full scale), airspy_scale_probe.sh, upstream_diff.sh
├── example/             # Example detector configs + template
├── rpi/                 # Pi 5 deployment assets (services, scripts, configs)
└── docs/                # User & deployment documentation
```

`pip install` exposes the `thriftyx` package (`pyproject.toml` pins
`[tool.setuptools.packages.find].include = ["thriftyx*"]`).  To compare
a module with the original Thrifty, run
`scripts/upstream_diff.sh carrier_sync.py` (any module name works).

## Raspberry Pi 5 Deployment

Thrifty-X ships a Pi 5 + Bookworm deployment layout under `rpi/`.

**Pi 5 (supported)** — the Python capture service with systemd:

| File / Directory | Purpose |
|------------------|---------|
| [`rpi/installation_pi5.md`](rpi/installation_pi5.md) | Step-by-step Pi 5 install (libairspy, chrony + `chrony-wait`, systemd) |
| `rpi/systemd/thriftyx-capture@.{service,env.example}` | Capture unit: ordered after clock sync, restarts forever (`Restart=always`, no start limit) |
| `rpi/systemd/thriftyx-heartbeat.{service,timer,env.example}` | Liveness probe every 60 s, runs as `pi` |
| `rpi/thriftyx-capture.cfg.example` | Capture config template (sample rate, gain, packing, ppm) |
| `rpi/heartbeat.py` | Health probe: JSON to stdout/journald, optional POST to `THRIFTYX_HEARTBEAT_URL` |
| `rpi/soak_test.sh` | 24-hour stability test |
| `rpi/update_node.sh` | In-place upgrade script (safe to re-run) |
| `rpi/cleanup_old_captures.sh` | Retention policy for `.card` files |

**Legacy (unsupported)** — kept from the original Pi 3 / RTL-SDR
deployment and the C `fastdet` path; not installed or tested by the Pi 5
guide:

| File | What it is |
|------|------------|
| `rpi/installation.md` | Original Pi 3 / Jessie installation guide |
| `rpi/detect.sh`, `rpi/detector.cfg` | RTL-SDR capture + detect pipeline (needs the upstream `fastcard` binary) |
| `rpi/detector.service`, `rpi/fastdet.sh`, `rpi/fastdet.cfg`, `rpi/template.tpl` | C `fastdet` service (needs `fastdet` built and `/home/pi/detector`) |
| `rpi/freq-map.cfg`, `rpi/template.npy` | Example frequency map and template (11-bit code 0 at 2.4 MSPS, like `rpi/template.tpl`) for the legacy pipeline |
| `rpi/ntp-after-online.{service,sh}` | Older clock-sync helper, superseded by `chrony-wait.service` |
| `rpi/pyFFTW-0.9.2-no-fftwl.patch` | Build patch for pyFFTW 0.9.2; current pyFFTW does not need it |

Operational documents live under `docs/`:

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
- The libairspy RX callback through the real ctypes boundary
  (`tests/unit/test_airspy_ctypes_callback.py`)
- The unified `analyze_detect` viewer plumbing and its fallbacks in
  headless mode, and `--export` writing every plot family to PNG
  (`tests/unit/test_detect_analysis_viewer.py`,
  `tests/unit/test_detect_analysis_export.py`)
- The whole chain end to end at 6 MSPS: three simulated receivers with
  different clock offsets and drift run capture → detect → identify →
  match → tdoa → pos through the real CLIs, and the mobile transmitter's
  position must come back within 3 m
  (`tests/integration/test_pipeline_6msps.py`)

CI runs `ruff check .` over the whole tree, `mypy`, the full `pytest`
suite, the `fastcapture` and `fastdet` CMake builds and their `ctest`
unit tests on every push and pull request. fastdet links the fastcapture static archive, so the workflow
builds and installs fastcapture to `/usr/local` before configuring
fastdet; it then installs fastdet there too and runs the installed
binary, which must find `libfastdet.so` without an `ldconfig`.

## Known Limitations

- **Hot-plug detection** is not handled; if a device is unplugged
  mid-capture the reader stops within ~10 s (`fastcapture` within ~1 s)
  and exits non-zero, and the systemd unit restarts capture once the
  device is back.
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
