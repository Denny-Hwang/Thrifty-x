# Thrifty-X User Guide

> Comprehensive operating manual for the Thrifty-X TDOA positioning system.
> A Korean translation is available at [user_guide_ko.md](user_guide_ko.md).

## Table of Contents

1. [Introduction](#1-introduction)
2. [Installation](#2-installation)
3. [Supported Hardware](#3-supported-hardware)
4. [Understanding Gain Settings](#4-understanding-gain-settings)
5. [Configuration Reference](#5-configuration-reference)
6. [Template System](#6-template-system)
7. [Quick Start: Single TX / Single RX Test](#7-quick-start-single-tx--single-rx-test)
8. [Command Reference](#8-command-reference)
9. [Understanding Detection Output](#9-understanding-detection-output)
10. [Troubleshooting](#10-troubleshooting)
11. [Multi-Receiver TDOA Setup (Future Work)](#11-multi-receiver-tdoa-setup-future-work)
12. [License & Attribution](#12-license--attribution)

---

## 1. Introduction

**Thrifty-X** is a software-defined radio (SDR) based time-difference-of-arrival
(TDOA) positioning system aimed at wildlife tracking and other low-cost
localization applications. It is a fork of the original
[Thrifty](https://github.com/swkrueger/Thrifty) developed by **Schalk Willem
Krüger** at North-West University as part of his MEng dissertation. Thrifty-X
preserves the signal processing pipeline of the original — Dirichlet-kernel
carrier interpolation, sample-of-arrival (SoA) estimation, beacon-based clock
correction, and Levenberg-Marquardt position solving — while extending the
hardware support and modernizing the codebase.

**Supported hardware:** RTL-SDR (RTL2832U + R820T/R820T2), Airspy Mini, Airspy R2.

**Key differences from the original Thrifty:**

| Aspect | Original Thrifty | Thrifty-X |
|---|---|---|
| SDR support | RTL-SDR only | RTL-SDR + Airspy Mini + Airspy R2 |
| Python | 2.7 / early 3 | 3.10+ with type hints |
| ADC | 8-bit unsigned | 8-bit (RTL) and 12-bit signed (Airspy) |
| Gain control | Single tuner_gain | Per-stage LNA + Mixer + VGA on Airspy |
| C library | fastcard (librtlsdr) | fastcapture (libairspy) |
| Visualization | GNU Radio / osmosdr | matplotlib (FuncAnimation) |
| Packaging | setup.py only | pyproject.toml + setup.py |

**License:** GPL-3.0-only (same as the original Thrifty).

**Citation:**

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system for
> tracking wildlife using off-the-shelf hardware.* Master's dissertation,
> North-West University, Potchefstroom Campus.
> https://hdl.handle.net/10394/25449

```bibtex
@mastersthesis{kruger2016inexpensive,
  title={An inexpensive hyperbolic positioning system for tracking wildlife
         using off-the-shelf hardware},
  author={Kr{\"u}ger, Schalk Willem},
  year={2016},
  school={North-West University (South Africa), Potchefstroom Campus}
}
```

---

## 2. Installation

### 2.1 Requirements

- Python **3.10 or newer**
- NumPy >= 1.23, SciPy >= 1.9
- (Optional) matplotlib >= 3.6 — required for `scope`, `analyze_*`, and plots
- (Optional) libairspy — required for live Airspy capture
- (Optional) librtlsdr / `rtl_sdr` binary — required for live RTL-SDR capture

### 2.2 Ubuntu 22.04 / WSL2 Ubuntu

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
                    build-essential cmake pkg-config \
                    airspy librtlsdr-dev rtl-sdr

# Clone and install in editable (development) mode
git clone https://github.com/Denny-Hwang/Thrifty-x.git
cd Thrifty-x
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"          # numpy + scipy + matplotlib + dev tools
```

The `[all]` extra pulls in `[fft]` (pyFFTW), `[analysis]` (matplotlib), and
`[dev]` (pytest, mypy, ruff). Use `pip install -e ".[analysis]"` for the
minimum runtime + plotting setup.

### 2.3 udev Rules (Linux Only)

Airspy devices must be reachable as a non-root user:

```bash
# Use the rules shipped with airspyone_host (apt's airspy package)
sudo cp /usr/share/airspy/52-airspy.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"    # log out and back in
```

For RTL-SDR, install `rtl-sdr` and blacklist the kernel DVB driver:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
```

### 2.4 WSL2 USB Passthrough (Windows)

WSL2 cannot see USB devices natively; use **usbipd-win** on the Windows host:

```powershell
# In an elevated PowerShell on Windows
winget install usbipd
usbipd list
# Identify your SDR by VID:PID:
#   Airspy Mini / R2 = 1d50:60a1
#   RTL-SDR (RTL2832U + R820T2) = 0bda:2838
usbipd bind   --busid <X-Y>
usbipd attach --wsl --busid <X-Y>
```

After `attach`, the device appears inside WSL. Verify:

```bash
lsusb                       # should list Bus … Device … 1d50:60a1 Airspy
airspy_info                 # prints Airspy serial + firmware
rtl_test -t                 # exercises an RTL-SDR
```

If `airspy_info` hangs or `airspy_open()` returns `-1000`, another process
(GNU Radio, SDR#, Gqrx) holds the device, or the WSL USB state is stale —
run `wsl --shutdown` from PowerShell and re-attach.

### 2.5 Verify the Installation

```bash
thriftyx --help                              # prints command list
python3 -c "import thriftyx; print(thriftyx.__version__)"
```

---

## 3. Supported Hardware

### 3.1 RTL-SDR (RTL2832U + R820T / R820T2)

- ADC: **8-bit** unsigned, 1 byte per I + 1 byte per Q
- Sample rates: 0.9 – 2.4 MSPS (2.4 MSPS recommended)
- Frequency range: 24 – 1766 MHz
- Gain: single dB value (the R820T2 LNA + Mixer is auto-distributed by the driver)
- Cost: ~$25 (clones), ~$35 (RTL-SDR Blog v3 / v4)
- Use case: prototyping, original-Thrifty compatibility

### 3.2 Airspy Mini

- ADC: **12-bit** signed
- Sample rates: 3 MSPS, 6 MSPS
- Frequency range: 24 – 1700 MHz
- Gain: 3-stage (LNA 0–14, Mixer 0–15, VGA/IF 0–15) — see [Section 4](#4-understanding-gain-settings)
- Bias-tee: yes (4.5 V, ~50 mA — for external LNA / preamp)
- Cost: ~$99
- Use case: field deployment (small, low-power), best SNR-per-dollar

### 3.3 Airspy R2

- ADC: **12-bit** signed
- Sample rates: 2.5 MSPS, 10 MSPS
- Frequency range: 24 – 1800 MHz
- Gain: 3-stage (same R820T2 tuner)
- Bias-tee: yes
- **External clock input**: yes — essential for coherent multi-receiver sync
- Cost: ~$169
- Use case: highest precision TDOA (external clock + 10 MSPS)

### 3.4 Hardware Comparison

| Feature | RTL-SDR | Airspy Mini | Airspy R2 |
|---|---|---|---|
| ADC resolution | 8-bit | 12-bit | 12-bit |
| Max sample rate | 2.4 MSPS | 6 MSPS | 10 MSPS |
| Raw sample period | 417 ns | 167 ns | 100 ns |
| Theoretical SoA precision* | ~12 ns | ~5 ns | ~3 ns |
| External clock input | No | No | **Yes** |
| Position accuracy** | ~3.5 m | ~1.5 m (target) | ~1.0 m (target) |

\* With sub-sample interpolation (parabolic or Gaussian, see Krüger 2016).
\** Real-world validation in progress. The 3.5 m figure is from the original
RTL-SDR experiments in Krüger 2016.

---

## 4. Understanding Gain Settings

> ⭐ **This section is the single most common source of confusion.** Read it
> at least once before tuning a receiver.

### 4.1 Why Gain Matters

The signal arriving at the antenna is extremely weak — for example, a
distant 166 MHz beacon may deliver only **−80 dBm**, which is **10
picowatts**. To digitize that signal usefully, the SDR must amplify it
into the ADC's dynamic range:

- **Too little gain** → the signal sinks below the ADC's quantization
  noise (especially painful on RTL-SDR's 8-bit ADC) and detection fails.
- **Too much gain** → the ADC saturates / clips, distorting the waveform
  and creating spurious detections.
- **Goal:** keep the noise floor **just above** the ADC's quantization
  floor, leaving headroom for short bursts.

A water analogy: pipe (antenna) → three valves (gain stages) → cup (ADC).
The cup must not overflow, and there must be enough water to taste.

### 4.2 RTL-SDR Gain

RTL-SDR exposes a **single** `tuner_gain` value (in dB). Internally the
R820T2 driver distributes it across LNA and Mixer. Typical values:

- `0.0` — auto-gain (driver's internal AGC)
- `14.4` to `49.6` — common manual values (the driver snaps to the
  nearest supported step)

Set this in `detector.cfg` as `tuner_gain: 0.0`. When the
`fastcard` C binary is available it's invoked with `-g <value>`;
otherwise the Python fallback applies the same gain via librtlsdr.

> The RTL-SDR scope/capture log line "gain = 0.00 dB" is a known
> cosmetic display issue and does not affect operation.

### 4.3 Airspy 3-Stage Gain (LNA → Mixer → VGA)

The Airspy R820T2 frontend has **three independent gain stages**. The
order matters:

#### Stage 1 — LNA (Low-Noise Amplifier), index 0–14

- **Position:** immediately after the antenna (RF front end).
- **Role:** the *first* amplifier — the most important one. Anything
  amplified here drowns out noise added by later stages. This is the
  Friis cascaded-noise principle: noise factor of stage *N* is divided
  by the gain of all preceding stages.
- **Analogy:** in a quiet library, the LNA is how close the microphone
  sits to the speaker. Closer (higher LNA) = clearer voice, but also
  more breath noise.
- **Caveat:** strong out-of-band signals (FM broadcast, LTE) are
  amplified too and produce intermodulation distortion (IMD) downstream.

#### Stage 2 — Mixer, index 0–15

- **Position:** right after the LNA.
- **Role:** frequency conversion + gain. Mixes the RF signal with a
  local oscillator (LO) so the wanted band lands at a manageable
  intermediate frequency (IF, ~5 MHz inside R820T2).
- **Analogy:** strobing a fast-spinning wheel (RF) with a flashlight
  (LO) — a slow apparent motion (IF) appears, and the brightness
  controls the visual gain.
- **Caveat:** in strong-signal environments, reduce the Mixer along
  with the LNA before touching the VGA.

#### Stage 3 — VGA / IF (Variable Gain Amplifier), index 0–15

- **Position:** after the Mixer, just before the ADC.
- **Role:** trim the final ADC drive level.
- **Analogy:** the master volume on a stereo — turning it up makes
  both the music *and* the hiss louder.
- **Caveat:** raising the VGA does **not** improve SNR. If the LNA and
  Mixer have already set the noise floor, the VGA only scales it. Keep
  the VGA modest.

#### Index vs. dB

The indices are register values inside the R820T2 chip, **not** decibels.
Per-step gain is non-linear:

| Stage | Index range | Approx. dB span | Approx. step |
|---|---|---|---|
| LNA   | 0–14 | 0 to ~26 dB | uneven |
| Mixer | 0–15 | 0 to ~19 dB | uneven |
| VGA   | 0–15 | 0 to ~26 dB | ~1.5 dB / step (most linear) |

Combined three-stage maximum is ~65 dB.

### 4.4 Gain Modes (manual / linearity / sensitivity)

libairspy exposes three preset modes; Thrifty-X surfaces them via
`--gain-mode`:

| Mode | What it does | When to use |
|---|---|---|
| **manual** *(default)* | Apply LNA, Mixer, VGA indices directly. | Full control, debugging. |
| **linearity** | Lookup table that backs off the LNA first while keeping VGA — minimizes IMD. | Strong-signal environments (urban, near transmitters). |
| **sensitivity** | Lookup table that holds LNA high and trims VGA first — minimizes noise figure. | Weak-signal environments (rural, distant TX). |

In `linearity` and `sensitivity` modes, control collapses to a single
**combined-gain** value 0–21 (0 = max gain, 21 = min). Excerpts from
libairspy's actual lookup tables:

```
Linearity mode (combined index → LNA, Mixer, VGA):
   0:  14, 12, 14    (max gain)
   5:  10, 10,  9
  10:   8,  7,  5
  15:   0,  3,  1
  21:   0,  0,  0    (min gain)

Sensitivity mode:
   0:  14, 12, 13    (max gain)
   5:  14, 10,  8
  10:  12,  7,  5
  15:   7,  2,  4
  21:   0,  0,  4    (min gain)
```

### 4.5 Gain-Tuning Procedure

A reproducible procedure that works for both Airspy devices:

1. **Disconnect the antenna.** Run `thriftyx scope` and observe the
   noise floor (FFT panel).
2. **Connect the antenna** (50 Ω terminator on the bench is a good
   intermediate step). The noise floor should rise by only **2–3 dB**.
   A bigger jump means the LNA is already too high or there's a
   strong out-of-band emitter.
3. Start from a moderate baseline: `LNA=7, Mixer=7, VGA=7` (the
   `detector_mini.cfg` / `detector_r2.cfg` defaults).
4. **Raise LNA first** while watching the noise floor. Stop one step
   *before* the noise floor visibly creeps upward.
5. **Tune Mixer** for fine adjustment of in-band signal level.
6. **Use VGA last**, only to set the final ADC drive level. If the
   sample histogram from `analyze_detect ... -p overview` shows
   clipping at ±2047, drop the VGA.

**Suggested starting points:**

| Device | LNA | Mixer | VGA | Environment |
|---|---|---|---|---|
| Airspy Mini (general) | 10 | 10 | 10 | medium range, moderate RF environment |
| Airspy Mini (weak signal) | 14 | 12 |  8 | long range, clean RF environment |
| Airspy R2 (general)   | 10 | 10 | 10 | medium range |
| Airspy R2 (weak signal) | 14 | 14 | 12 | long range, clean RF environment |
| Airspy R2 (strong signal) |  5 |  5 |  8 | close-range, urban RF |

> ⚠️  `LNA=14, Mixer=15, VGA=15` is the absolute maximum and **will**
> saturate the ADC for any non-trivial input. We have observed the
> reported noise field climbing past 90 in this configuration —
> always step back at least one notch on each stage.

### 4.6 Diagnosing Gain Problems

| Symptom | Likely cause | Fix |
|---|---|---|
| Zero detections | Gain too low | Raise LNA first |
| Noise field >> 10 in capture status line | Gain too high | Lower VGA first |
| Sporadic correlation hits in odd bins | IMD (LNA too high) | Lower LNA |
| Histogram peaks at −2048 / +2047 | ADC clipping | Lower the whole chain |
| `gain = 0.00 dB` displayed (RTL-SDR) | Cosmetic display only | Ignore |

---

## 5. Configuration Reference

### 5.1 `detector.cfg` Format

The file uses simple `key: value` pairs, one per line, with `#`
introducing a comment. The same parser is used by every Thrifty-X
command, so a single `detector.cfg` covers `capture`, `detect`,
`scope`, `template_*`, etc.

Numeric suffixes accepted: `K`, `M`, `G` (e.g. `2.4M = 2_400_000`).
`carrier_window` and threshold expressions are parsed by
`thriftyx.setting_parsers`.

CLI flags always override config values.

### 5.2 Per-Device Config Examples

The `example/` directory ships three pre-tuned configs.

**`example/detector.cfg` — RTL-SDR @ 2.4 MSPS (default):**

```
rxid:               0
device_type:        rtlsdr
bit_depth:          8
sample_rate:        2.4M
chip_rate:          0.999707M
tuner_freq:         433.83M           # adjust to your TX
tuner_gain:         0.0
capture_skip:       600
block_size:         16384             # 2^14, ~6.83 ms at 2.4 MSPS
block_history:      4920              # >= template length (2455)
carrier_window:     7 - 130           # ~1 kHz to ~19 kHz offset
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer           # or 'time_domain'
soa_interpolation:  parabolic         # or 'gaussian' / 'none'
```

**`example/detector_mini.cfg` — Airspy Mini @ 6 MSPS:**

```
rxid:               0
device_type:        airspy_mini
bit_depth:          12
sample_rate:        6M
chip_rate:          0.999707M
tuner_freq:         166M
capture_skip:       100
lna_gain:           7                 # range 0–14
mixer_gain:         7                 # range 0–15
vga_gain:           7                 # range 0–15
bias_tee:           false
block_size:         32768             # 2^15, ~5.46 ms at 6 MSPS
block_history:      12278             # 2 × template length (6139)
carrier_window:     6 - 103           # 1 kHz to 19 kHz @ 183.1 Hz/bin
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer
soa_interpolation:  parabolic
```

**`example/detector_r2.cfg` — Airspy R2 @ 10 MSPS:**

```
rxid:               0
device_type:        airspy_r2
bit_depth:          12
sample_rate:        10M
chip_rate:          0.999707M
tuner_freq:         166M
capture_skip:       100
lna_gain:           7
mixer_gain:         7
vga_gain:           7
bias_tee:           false
block_size:         65536             # 2^16, ~6.55 ms at 10 MSPS
block_history:      20464             # 2 × template length (10232)
carrier_window:     7 - 124           # 1 kHz to 19 kHz @ 152.6 Hz/bin
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer
soa_interpolation:  parabolic
```

To switch hardware, copy the appropriate file over `detector.cfg`:

```bash
cp example/detector_mini.cfg example/detector.cfg
```

### 5.3 Parameter Dependencies

Changing `sample_rate` cascades into several other parameters. Always
keep the table below internally consistent.

| Parameter | Formula | RTL @ 2.4 M | Mini @ 6 M | R2 @ 10 M |
|---|---|---|---|---|
| template length | (2^code_len − 1) × sample_rate / chip_rate | 2,457 | 6,139 | 10,232 |
| `block_size`     | ≥ 2 × `block_history`, power of 2 | 16,384 | 32,768 | 65,536 |
| `block_history`  | ≥ template length | 4,920 | 12,278 | 20,464 |
| block period     | `block_size` / `sample_rate` | 6.83 ms | 5.46 ms | 6.55 ms |
| bin resolution   | `sample_rate` / `block_size` | 146.5 Hz | 183.1 Hz | 152.6 Hz |
| `carrier_window` low  | `ceil(1000 / bin_res)` | 7 | 6 | 7 |
| `carrier_window` high | `floor(19000 / bin_res)` | 130 | 103 | 124 |

> ⚠️  `template.npy` and `detector.cfg` **must agree on `sample_rate`**.
> A mismatch silently produces zero detections. When you change the
> sample rate, regenerate the template (see [Section 6.5](#65-template-regeneration-when-changing-devices)).

### 5.4 Frequently Used Airspy CLI Flags

| Flag | Default | Notes |
|---|---|---|
| `--lna-gain N` / `--mixer-gain N` / `--vga-gain N` | from config | Per-stage indices in `manual` mode. |
| `--gain-mode {manual, linearity, sensitivity}` | `manual` | Selects gain table. |
| `--combined-gain N` | 0 | 0–21, used by linearity/sensitivity. |
| `--lna-agc` / `--mixer-agc` | false | Engage R820T2 AGC loops. |
| `--bias-tee` | false | 4.5 V on the antenna lead — ensure DC isolation. |
| `--ppm F` | 0 | Software LO correction in ppm. |
| `--packing` | false | Enable libairspy 12-bit USB packing (helps R2 at 10 MSPS). |
| `--airspy-serial 0x…` | – | Select a specific Airspy by 64-bit serial. |
| `-d N` / `--device-index N` | 0 | Select RTL-SDR or Airspy by enumeration index. |

---

## 6. Template System

> ⭐ **The second most common source of confusion.** Detection performance
> hinges on using a *captured* template, not a theoretical one.

### 6.1 What Is a Template?

Thrifty-X transmitters emit a Gold-code modulated continuous-wave
signal. **Gold codes** are length-`(2^n − 1)` binary sequences with
favourable autocorrelation properties — the same family used for GPS
C/A codes and CDMA. Thrifty-X uses `code_len = 10`, giving 1023 chips
per code period.

Each transmitter is assigned a unique `code_index` (0 … 1024). The
**template** is that Gold code resampled to the receiver's sample rate.
Detection is performed by FFT-based correlation between captured blocks
and this template.

### 6.2 Theoretical Template

```bash
thriftyx template_generate <code_len> <code_index> -o template.npy
# Example: code length 10, transmitter index 3
thriftyx template_generate 10 3 -o template.npy
```

The output is a clean `{−1, +1}` BPSK square wave at the configured
sample rate. It can be generated **without any hardware**, but it does
not match the receiver's analog frontend response, so correlation SNR
is poor (mismatched filter).

### 6.3 Captured Template (Strongly Recommended)

Extract a matched filter directly from a real capture:

```bash
# Step 1 — generate a theoretical seed template
thriftyx template_generate 10 3 -o template_ideal.npy

# Step 2 — short live capture (5–10 s is plenty)
thriftyx capture initial.card --duration 10

# Step 3 — extract a continuous-valued template from the capture
thriftyx template_extract initial.card \
    --template template_ideal.npy \
    -o template_captured.npy

# Step 4 — make it the active template
cp template_captured.npy template.npy
```

The extracted template has continuous (not just `±1`) values that
encode the analog frontend's pulse shaping, filter ripple, and group
delay — i.e. a true matched filter for *this* receiver chain.

Indicative correlation SNR improvement on real captures:

| Template | RTL-SDR | Airspy Mini | Airspy R2 |
|---|---|---|---|
| Theoretical (±1) | ~13 dB | ~5 dB | ~7 dB |
| **Captured** | **~56 dB** | **~41 dB** | **~39 dB** |

The 30+ dB gap is enough that detection often fails entirely with a
theoretical template at long range. **Always extract a captured
template before serious work.**

### 6.4 When You Don't Know the Gold-Code Index

If the transmitter's `code_index` is unknown, brute-force the search
space. Indices 0–20 cover most field deployments:

```bash
for i in $(seq 0 20); do
  thriftyx template_generate 10 $i -o /tmp/template_test.npy
  count=$(thriftyx detect capture.card \
      --template /tmp/template_test.npy -o /dev/null 2>&1 \
      | grep -c "corr: yes")
  echo "index $i: $count corr hits"
done
```

The index with the most `corr: yes` hits is the transmitter's setting.
Once identified, regenerate / re-extract the proper template before
production runs.

### 6.5 Template Regeneration When Changing Devices

`template.npy` is **specific to a sample rate**. Switching from RTL-SDR
to Airspy Mini changes the sample count per code period from 2,457 to
6,139 — the old template won't correlate. Whenever you change the
sample rate (or device):

1. `cp example/detector_<device>.cfg example/detector.cfg`
2. `thriftyx template_generate 10 <code_index> -o template_ideal.npy`
3. `thriftyx capture initial.card --duration 10`
4. `thriftyx template_extract initial.card --template template_ideal.npy -o template.npy`

---

## 7. Quick Start: Single TX / Single RX Test

A complete first-light pipeline. Run it from `example/` with a single
beacon transmitter on air.

```bash
# 0. Activate the environment
cd ~/Thrifty-x
source .venv/bin/activate
cd example

# 1. Pick the device-specific config
cp detector_r2.cfg detector.cfg          # adjust for your hardware

# 2. Generate a theoretical seed template
thriftyx template_generate 10 3 -o template_ideal.npy

# 3. Short capture for template extraction
thriftyx capture initial.card --duration 5

# 4. Extract a captured (matched) template
thriftyx template_extract initial.card \
    --template template_ideal.npy -o template.npy

# 5. Production capture (30 s)
thriftyx capture rx0.card --duration 30

# 6. Detect (carrier + correlation) → .toad
thriftyx detect rx0.card -o rx0.toad

# 7. Identify transmitter IDs → .toads
thriftyx identify rx0.toad -o rx0.toads

# 8. Statistics and analysis
thriftyx analyze_toads -i rx0.toads
thriftyx analyze_detect rx0.card -m 2 -p overview
```

**What success looks like at each step:**

- Step 3 / 5 — `block #N: mag[bin] = … (thresh = …, noise = …)` lines
  on stderr, one per detected block.
- Step 6 — lines like `block #… cardet: yes corr: yes …` on stdout;
  the number of `corr: yes` lines is the detection count.
- Step 7 — one line per unique transmission written to `rx0.toads`.
- Step 8 — `analyze_toads` prints summary statistics; `analyze_detect`
  pops up a 4-panel overview plot.

If `corr: yes` is rare or absent, return to Section 4 (gain) and
Section 6 (template).

---

## 8. Command Reference

All commands are subcommands of `thriftyx` (the legacy `thrifty` alias
also works). Run `thriftyx help <command>` for full option listings.
The dispatch table lives in `thriftyx/cli.py`.

### Core pipeline

| Command | One-liner | Input → Output |
|---|---|---|
| `capture` | Capture from SDR with carrier-detection prefilter | SDR → `.card` |
| `detect`  | Carrier sync + correlation, estimate SoA | `.card` → `.toad` |
| `identify` | Map detections to transmitter IDs, drop duplicates | `*.toad` → `.toads` |
| `match` | Time-window matching across receivers | `.toads` → `.match` |
| `tdoa` | Beacon-corrected TDOA estimation | `.toads` + `.match` → `.tdoa` |
| `pos` | Levenberg-Marquardt position solve | `.tdoa` → `.pos` |

### Analysis

| Command | One-liner |
|---|---|
| `scope` | Live time / FFT / histogram plot via matplotlib. `--trigger-level <0–1>` for hold-on-peak behaviour. |
| `analyze_toads` | Summary statistics on a `.toads` file. `-i data.toads -m data.match`. |
| `analyze_detect` | Re-run detection with diagnostic plots. `-m N` (max blocks), `-p overview,time,overlays,spectra,corrs`. |
| `analyze_beacon` | Diff in SoA of a beacon between two receivers. `--beacon`, `--rx0`, `--rx1`. |
| `analyze_tdoa` | Per-slice statistics on `.tdoa` data. `--rx0`, `--rx1`, `--tx`, `--timestamp`. |

### Utilities

| Command | One-liner |
|---|---|
| `template_generate` | Generate an ideal Gold-code template. `length` `index` `-o file.npy`. |
| `template_extract`  | Extract a matched template from a capture. `input.card --template ideal.npy -o new.npy`. |

### Common options

- `-o / --output` — write to a file instead of stdout.
- `-a / --append` — append to an existing output file (`detect` only).
- `--quiet` — suppress per-block status output (`detect`).
- `--raw` — input is raw I/Q rather than `.card` (`detect`,
  `analyze_detect`).

### Selected `capture` options

- `--device-type {rtlsdr, airspy_mini, airspy_r2}` — overrides config.
- `--duration <sec>` — stop after N seconds (default: until Ctrl+C).
- `--input <path>` — read from a file or `-` (stdin) instead of a live
  device. Useful with `rtl_sdr -f … -s … - | thriftyx capture …`.
- `--fastcard <path>` — alternate path to the `fastcard` binary
  (RTL-SDR only). If the binary isn't on `PATH`, Thrifty-X falls back
  to its Python carrier detector.

---

## 9. Understanding Detection Output

### 9.1 `.card` File Format

A `.card` file contains only blocks where a carrier was detected
(matching the original Thrifty's `fastcard` behaviour). Two on-disk
formats exist:

- **v1** (RTL-SDR legacy, no header) — lines of
  `<timestamp> <block_idx> <base64 of raw uint8 I/Q>`.
- **v2** (Airspy) — leading header line
  `#v2 bit_depth=12 sample_rate=6000000`, then
  `<timestamp> <block_idx> <base64 of int16 I/Q>` lines.

Format auto-detection is performed by `thriftyx.block_data.card_reader`,
so existing v1 RTL-SDR captures from the original Thrifty are usable
without conversion.

### 9.2 `.toad` File Format

One detection per line, whitespace-separated:

| Column | Meaning |
|---|---|
| `rxid` | Receiver ID (`rxid:` from the config) |
| `timestamp` | Linux epoch time of the block |
| `block_idx` | Block number within the capture |
| `soa` | Sample-of-arrival (sub-sample precision) |
| `corr_idx`, `corr_offset`, `corr_energy` | Correlation peak metadata |
| `carrier_idx`, `carrier_offset`, `carrier_energy` | Carrier-detection metadata |
| `noise_rms`, `block_energy` | Noise / energy stats used for thresholding |

The `.toads` file produced by `identify` adds a `txid` column and
de-duplicates per-receiver detections.

### 9.3 Detection Analysis Plots

`thriftyx analyze_detect <file.card> -m <N> -p <plots>` runs the
detector again on the first `N` blocks and renders one or more of the
following plot families. Use `-p overview` first.

1. **overview** — 4-panel: sample histogram + frequency-compensated
   magnitude over time + FFT (carrier search) + correlation output.
2. **time** — time-domain waveform (real + imaginary, magnitude).
3. **overlays** — captured signal overlaid on the template after
   alignment.
4. **spectra** — magnitude spectrum, with the carrier window shaded.
5. **corrs** — cross-correlation vs. autocorrelation, with the
   sub-sample interpolation (parabolic / Gaussian) overlay.

What "good" looks like:

- **Histogram** centred near 0, no clusters at ±127 (RTL-SDR) or
  ±2047 (Airspy).
- **FFT** with a clear carrier peak inside the configured
  `carrier_window`.
- **Correlation** with one tall peak and a low side-lobe floor.
- **Overlays** with template and signal tracking each other.

What "bad" looks like:

- Histogram clustered at the extremes → ADC clipping (lower gain).
- Correlation peak buried in noise → wrong template, bad gain, or
  wrong `code_index`.
- Carrier peak outside the window → adjust `tuner_freq` or
  `carrier_window`.

---

## 10. Troubleshooting

### 10.1 Common Issues

| Symptom | Likely cause | Action |
|---|---|---|
| `usb_claim_interface error -6` | Stale USB handle after Ctrl+C | `usbipd detach` → `usbipd attach`; or `udevadm trigger` |
| `airspy_info` hangs | WSL USB state stale | `wsl --shutdown` from PowerShell, then re-attach |
| `airspy_open() returned -1000` | Another process owns the device | Close GNU Radio / SDR# / Gqrx |
| Zero detections | Wrong Gold-code index | Brute-force scan (Section 6.4) |
| Zero detections | Gain too low | Raise LNA (Section 4.5) |
| Zero detections | Template ↔ config sample-rate mismatch | Regenerate template (Section 6.5) |
| `corr: no` everywhere | Theoretical template only | Extract captured template (Section 6.3) |
| Carrier in unexpected bin | Frequency offset / wrong `tuner_freq` | Use `thriftyx scope` to locate the actual carrier |
| Very high noise field | Gain too high (especially VGA) | Lower VGA, then Mixer |
| Histogram clustered at ±max | ADC saturation | Lower the entire chain |
| `airspy_start_rx() failed: -1000` | Unstable USB link | Re-attach via `usbipd`; try a USB 3.x port |
| Irregular block intervals | USB buffer drops | Increase `capture_skip`; switch port; enable `--packing` on R2 |

### 10.2 WSL2-Specific Tips

- USB attach / detach is done from **PowerShell**, not WSL.
- For matplotlib plots inside WSL, set a usable backend:
  `export MPLBACKEND=TkAgg` (with WSLg) or use `--export plot.pdf` to
  save to disk instead.
- `wsl --shutdown` is a clean recovery from any USB-state mess.
- WSL's clock can drift — use `sudo hwclock -s` if `.toad` timestamps
  look wrong.

---

## 11. Multi-Receiver TDOA Setup (Future Work)

Multi-receiver positioning is **work in progress** in Thrifty-X.
The high-level architecture (carried over from the original Thrifty):

- **Minimum 3 receivers**, plus **1 beacon transmitter** at a known
  location, plus **N tag transmitters** to localize.
- The beacon's known position is used to compensate for asynchronous
  receiver clocks: every TDOA between receivers is anchored to a
  beacon emission.
- **Coherent synchronization** (sharing a 10 MHz reference) is
  possible only with the **Airspy R2's external clock input**. Mini /
  RTL-SDR are limited to beacon-based correction.

Pipeline stages (CLI commands):

```
*.toads  →  thriftyx match    → .match
.toads + .match  →  thriftyx tdoa  -r pos-rx.cfg -b pos-beacon.cfg → .tdoa
.tdoa  →  thriftyx pos  -r pos-rx.cfg → .pos
```

Receiver and beacon coordinates live in `pos-rx.cfg` and
`pos-beacon.cfg` (one `id: x y` line each). End-to-end multi-receiver
documentation will be added as the integration testing matures.

---

## 12. License & Attribution

Thrifty-X is licensed under **GPL-3.0-only**, the same license as the
upstream project. See [LICENSE.txt](../LICENSE.txt) for full terms.

- Original Thrifty © 2016–2017 Schalk Willem Krüger, North-West
  University. Source:
  [github.com/swkrueger/Thrifty](https://github.com/swkrueger/Thrifty).
- Thrifty-X © 2025–2026 Sungjoo Hwang and PNNL contributors.

If you publish results obtained with Thrifty-X, please cite the
original dissertation:

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system
> for tracking wildlife using off-the-shelf hardware.* Master's
> dissertation, North-West University, Potchefstroom Campus.
> https://hdl.handle.net/10394/25449

```bibtex
@mastersthesis{kruger2016inexpensive,
  title={An inexpensive hyperbolic positioning system for tracking wildlife
         using off-the-shelf hardware},
  author={Kr{\"u}ger, Schalk Willem},
  year={2016},
  school={North-West University (South Africa), Potchefstroom Campus}
}
```
