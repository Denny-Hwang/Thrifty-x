# Thrifty-X User Guide

> Comprehensive operating manual for the Thrifty-X TDOA positioning system.

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
| Packaging | setup.py only | pyproject.toml (PEP 621) |

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

The `[all]` extra pulls in `[fft]` (pyFFTW), `[analysis]` (matplotlib),
`[gui]` (PyQt5, for the `analyze_detect` viewer) and `[dev]` (pytest,
mypy, ruff). Use `pip install -e ".[analysis]"` for the minimum runtime +
plotting setup; on a Raspberry Pi RX node use `.[analysis,fft]` (see
`rpi/installation_pi5.md`).

### 2.3 udev Rules (Linux Only)

Airspy devices must be reachable as a non-root user.  apt's
`libairspy0` (pulled in by `airspy`) installs the rules as
`/usr/lib/udev/rules.d/60-libairspy0.rules` for group `plugdev`:

```bash
sudo usermod -aG plugdev "$USER"    # log out and back in
```

(With libairspy built from source, copy `airspy-tools/52-airspy.rules`
from its tree to `/etc/udev/rules.d/`, then
`sudo udevadm control --reload && sudo udevadm trigger`.)

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

Set this in `detector.cfg` as `tuner_gain: 0.0`. It takes effect only
when the upstream `fastcard` C binary is on `PATH` and no `--input` is
given: capture then runs it with `-g <value>`.  Otherwise capture's
Python fallback does not open the dongle at all -- it reads samples
from `rtl_sdr` (`rtl_sdr -f 433.83M -s 2.4M -g 40 - | thriftyx capture
rx0.card --device-type rtlsdr --input -`), so set the gain with
`rtl_sdr -g`; the `gain = ... dB` in capture's banner then only echoes
`tuner_gain`.

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

libairspy exposes three gain configurations; Thrifty-X surfaces them via
`--gain-mode`:

| Mode | What it does | When to use |
|---|---|---|
| **manual** *(default)* | Apply LNA, Mixer, VGA indices directly. | Full control, debugging, external-LNA deployments. |
| **linearity** | libairspy resolves all three stages from one index — backs off the LNA first while keeping VGA — minimizes IMD. | Strong-signal environments (urban, near transmitters). |
| **sensitivity** | libairspy resolves all three stages from one index — holds LNA high and trims VGA first — minimizes noise figure. | Weak-signal environments (rural, distant TX). |

In `linearity` and `sensitivity` modes, control collapses to a single
**`--combined-gain`** index `0–21`. Thrifty-X delegates these modes
directly to `airspy_set_linearity_gain()` / `airspy_set_sensitivity_gain()`
(it does **not** re-derive the stage ladder in Python), so the mapping is
exactly libairspy's.

**Two consequences you must know** (verified against
`airspy/airspyone_host`, `libairspy/src/airspy.c`):

1. **`combined-gain 0` is the *minimum*, `21` is the *maximum*.**
   libairspy inverts the index internally
   (`value = GAIN_COUNT - 1 - value;`, `GAIN_COUNT == 22`), so the
   user-facing `0` selects the lowest-gain row of the ladder. This is the
   opposite of what the raw lookup-table order suggests.

2. **Preset modes force AGC off and cannot reach a true all-zero internal
   gain.** Both functions call `airspy_set_mixer_agc(device, 0)` and
   `airspy_set_lna_agc(device, 0)`, so `--lna-agc` / `--mixer-agc` are
   ignored (Thrifty-X warns if you set them with a preset). And the
   minimum-gain row is `LNA=0, Mixer=0, VGA=4` — the VGA floor is **4**,
   not 0. Only **manual `0/0/0`** reaches `LNA=0, Mixer=0, VGA=0`.

> **Recommendation for external-LNA deployments (e.g. BatRF, +20 dB
> external LNA at 161.3 MHz):** use **manual mode with `LNA=0 Mixer=0
> VGA=0`** to keep the internal front-end at its true hardware minimum and
> avoid overdriving the ADC. The preset modes are offered as a convenience
> for single-knob tuning when no external amplifier is present; they
> cannot reach the internal minimum because of the VGA=4 floor above.

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
   samples piling up near ±16384 (libairspy's int16 full scale for the
   12-bit ADC), drop the VGA.

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
| Histogram piles up beyond ±8 000 (Airspy int16) | ADC near full scale; libairspy's int16 path saturates above about half scale | Lower the whole chain |
| `gain = 0.00 dB` displayed (RTL-SDR) | Cosmetic display only | Ignore |

---

## 5. Configuration Reference

### 5.1 `detector.cfg` Format

The file uses simple `key: value` pairs, one per line, with `#`
introducing a comment. The same parser is used by every Thrifty-X
command, so a single `detector.cfg` covers `capture`, `detect`,
`scope`, `template_*`, etc.

`sample_rate`, `chip_rate` and `tuner_freq` accept a metric suffix:
`k` or `K`, `M`, `G` (e.g. `2.4M = 2_400_000`).  A lowercase `m` means
milli, not mega: `chip_rate: 0.999707m` is rejected, since no template
fits that many samples per chip.  `carrier_window` is in FFT bins
unless it ends in `Hz`: `50-60kHz` is 50 to 60 kHz, but `50-60k` is
bins 50 000 to 60 000.  That lies beyond the 32768-bin FFT at 6 Msps,
where capture refuses it, but inside the 65536-bin FFT at 10 Msps,
where it is only warned about (past Nyquist) and capture searches the
wrong frequencies; `20-30k` there draws no warning at all.
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
block_history:      4920              # >= 11-bit template length (4914)
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
block_history:      12349             # 11-bit template (12285) + 64
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
block_history:      20539             # 11-bit template (20475) + 64
carrier_window:     7 - 124           # 1 kHz to 19 kHz @ 152.6 Hz/bin
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer
soa_interpolation:  parabolic
```

To switch hardware, copy the appropriate file to `detector.cfg` in the
directory you run the commands from (every command reads
`./detector.cfg`).  Keep it outside the checkout: editing the tracked
`example/` files makes the tree dirty, and `rpi/update_node.sh` refuses
to update a dirty tree.

```bash
cp ~/Thrifty-x/example/detector_mini.cfg ~/thriftyx-run/detector.cfg
```

All three examples set `rxid: 0`.  With more than one receiver, give
each its own `rxid` (edit its `detector.cfg`, or pass `detect --rxid
N`), equal to the id of its line in `pos-rx.cfg` (Section 11).  `detect`
stamps it into every detection; `capture` does not use it.

### 5.3 Parameter Dependencies

Changing `sample_rate` cascades into several other parameters. Always
keep the table below internally consistent.

| Parameter | Formula | RTL @ 2.4 M | Mini @ 6 M | R2 @ 10 M |
|---|---|---|---|---|
| template length | (2^bits − 1) × sample_rate / chip_rate, truncated | 11-bit: 4,914<br>10-bit: 2,455 | 11-bit: 12,285<br>10-bit: 6,139 | 11-bit: 20,475<br>10-bit: 10,232 |
| `block_size`     | ≥ template + `block_history` and ≥ 2 × `block_history`, power of 2 | 16,384 | 32,768 | 65,536 |
| `block_history`  | ≥ template length − 1; default: the stock 4,920, or the 11-bit template + 64 where that is longer | 4,920 | 12,349 | 20,539 |
| block period     | `block_size` / `sample_rate` | 6.83 ms | 5.46 ms | 6.55 ms |
| bin resolution   | `sample_rate` / `block_size` | 146.5 Hz | 183.1 Hz | 152.6 Hz |
| `carrier_window` low  | `ceil(1000 / bin_res)` | 7 | 6 | 7 |
| `carrier_window` high | `floor(19000 / bin_res)` | 130 | 103 | 124 |

> ⚠️  `template.npy` **must be generated at the capture's `sample_rate`**.
> `detect` takes the sample rate and block geometry from the card's
> `#v2` header, but it cannot correct a template made for another rate:
> a mismatch loses the weak bursts and turns strong ones into false
> detections with wrong SoAs (`detect` warns when its template holds no
> code at the card's sample rate). When you change the
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

### 5.5 Threshold Tuning

`carrier_threshold` and `corr_threshold` are expressions in the noise
estimate, e.g. the default `15 * snr`. The expression is parsed by
`thriftyx.setting_parsers.threshold` and evaluated identically for every
device — the threshold is `sqrt(const + snr·noise_rms² + stddev·stddev²)`
in `carrier_detect._calculate_threshold` / `soa_estimator.calculate_threshold`.
There is **no device-specific branching** in the threshold path: the same
expression yields the same absolute threshold given the same noise stats,
on RTL-SDR and Airspy alike.  So a threshold change is a per-site tuning decision, not a code default.

**When to lower `corr_threshold`:**

- **RTL-SDR with an external LNA** (e.g. a +20 dB front-end): the default
  `15 * snr` is often too strict and rejects real detections. Start from
  **`--corr-threshold "10*snr"`** and verify the detection count against a
  known-good capture.
- **Airspy R2 with the same external LNA**: usually fine at the default
  `15 * snr` (the 12-bit ADC has a quieter noise floor). Lower only if a
  sweep shows detections are being clipped.

These are **starting points to verify per site**, not hard rules. The
in-repo default stays `15 * snr` because it suits the common
no-external-amplifier case. To find the right value for a given site,
sweep the expression on a representative capture and watch where the
detection count plateaus:

```bash
for T in "8*snr" "10*snr" "12*snr" "15*snr"; do
    echo -n "$T  ->  "
    thriftyx detect capture.card --corr-threshold "$T" -o /tmp/t.toad --quiet
    grep -c . /tmp/t.toad
done
```

The plateau is the lowest threshold that does not also admit a rising
tail of false positives; pick a value just above it.

---

## 6. Template System

> ⭐ **The second most common source of confusion.** Detection performance
> hinges on using a *captured* template, not a theoretical one.

### 6.1 What Is a Template?

A Thrifty transmitter keys its carrier on and off with a binary code of
`2^n − 1` chips (register length `n` = 5 … 11 bits) at about
1 Mchip/s, one code period per burst.  Each transmitter is assigned a
code: a register length, an **index** within its family (0 … 2^n) and,
for 8 and 10 bits, a **family**:

- **Gold family** (`--family gold`; the only family for 5, 6, 7, 9 and
  11 bits).  Codes from a *preferred pair* of registers: any two codes
  periodically cross-correlate at most 65 of 1023 or 2047 chips
  (−23.9 dB).  The 10-bit family is the one the GPS C/A codes come from
  (`gold(10, 1025 − d)` is the GPS PRN with G2 delay `d`).
- **Legacy codes** (`--family legacy`; 8 and 10 bits only).  The codes
  Thrifty, and Thrifty-X before this was fixed, generated for 8 and 10
  bits.  They are not Gold codes (the 10-bit ones reach 97 of 1023,
  −20.5 dB), but transmitters programmed from `template_generate 10 N`
  of an earlier release send them, and only a legacy template matches
  those.

Those bounds are periodic.  A burst is correlated once, aperiodically,
and there the families differ by about 1 dB (median peak sidelobe
−19.5 dB legacy, −20.2 dB Gold for 10 bits; −22.8 dB for 11 bits), so
the family a fleet already uses is not worth reprogramming it for.

The upstream Thrifty transmitters send the **11-bit code 0**: the
template captured from them (`example/template.npy`) is `gold(11, 0)`.
Which code your transmitters send is a fact about their firmware —
check it on a capture (Section 6.4) rather than assuming it.

The **template** is that code sampled at the receiver's sample rate.
Detection is performed by FFT-based correlation between captured blocks
and this template; `detect` prints which code its template holds at
the start of every run.

### 6.2 Theoretical Template

```bash
thriftyx template_generate <bits> <index> [--family gold|legacy] -o template.npy
# Upstream Thrifty transmitters: 11-bit code 0
thriftyx template_generate 11 0 -o template.npy
# A transmitter programmed with the old 10-bit code 3
thriftyx template_generate 10 3 --family legacy -o template.npy
```

8- and 10-bit codes need `--family`: the same index is a different code
in each family.  A template for the wrong code or family does not
simply detect nothing — weak bursts are lost, and strong ones (above
roughly 25 dB correlation SNR) are "detected" on its correlation
sidelobes with SoAs off by hundreds of samples.  So a detection count
does not prove a template right; check the code on a capture
(Section 6.4).  The index must be 0 … 2^n (older releases wrapped
larger values).

The output is a clean `{−1, +1}` square wave at the configured
sample rate. It can be generated **without any hardware**, but it does
not match the receiver's analog frontend response, so correlation SNR
is poor (mismatched filter).

### 6.3 Captured Template (Strongly Recommended)

Extract a matched filter directly from a real capture:

```bash
# Step 1 — short live capture (5–10 s is plenty)
thriftyx capture initial.card --duration 10

# Step 2 — find the code the transmitter sends (Section 6.4)
thriftyx gold --identify initial.card

# Step 3 — generate that code as a theoretical seed template, e.g.
thriftyx template_generate 11 0 -o template_ideal.npy

# Step 4 — extract a continuous-valued template from the capture
thriftyx template_extract initial.card \
    --template template_ideal.npy \
    -o template_captured.npy

# Step 5 — make it the active template
cp template_captured.npy template.npy
```

The extracted template has continuous (not just `±1`) values that
encode the analog frontend's pulse shaping, filter ripple, and group
delay — i.e. a true matched filter for *this* receiver chain.

`template_extract` cuts the template from a complete burst whose
correlation peak lies within 0.2 samples of a whole sample.  It never
uses the partial detection a burst also leaves in the neighbouring
block, recognised (as `identify` drops it) by a stronger detection in
the block before or after; that would give a template starting
part-way into the code.  If no complete burst qualifies it fails and
asks for a longer capture.  A weaker transmitter's bursts still count
as complete.  The output is replaced only once extraction succeeds, so
`-o` may name the template it reads (`--template template.npy -o
template.npy`); a symlink is written through and an existing file
keeps its permissions.

Indicative correlation SNR improvement on real captures:

| Template | RTL-SDR | Airspy Mini | Airspy R2 |
|---|---|---|---|
| Theoretical (±1) | ~13 dB | ~5 dB | ~7 dB |
| **Captured** | **~56 dB** | **~41 dB** | **~39 dB** |

The 30+ dB gap is enough that detection often fails entirely with a
theoretical template at long range. **Always extract a captured
template before serious work.**

### 6.4 Which Code Does a Transmitter Send?

`thriftyx gold --identify` reads a capture and needs no template: a
`.card` holds only blocks with a carrier, and the code is the envelope
of an on-off keyed burst.  It cuts out the strongest complete burst,
tries every code of every register length that fits it, in both
families, and prints the best match with the command that generates it:

```bash
thriftyx gold --identify initial.card
#   initial.card: burst of 12285 samples
#   best match: 11-bit Gold code 0: correlation 0.896 (6.001 samples/chip)
#   runner-up:  11-bit Gold code 356: correlation 0.073, inverted (5.995 samples/chip)
#   template for this code: thriftyx template_generate 11 0 --family gold --sample-rate 6M
```

(A transmitter programmed with the old 10-bit code 3 reports
`best match: 10-bit legacy (not Gold) code 3` and `template_generate 10
3 --family legacy`.)  The exit status is 0 for a clear match, 1
otherwise.

- A clear match correlates well above 0.5 and several times the
  runner-up (less for 5- and 6-bit codes, whose 31 or 63 chips
  correlate more with each other); otherwise it says that no code
  matches clearly.  Capture one transmitter at a time, close enough for
  a clean burst.
- It also reads a template (`.npy`, or fastdet's `.tpl`): `thriftyx
  gold --identify template.npy --sample-rate 6M` tells which code an
  existing template holds.
- `--family` and a register length narrow the search (`thriftyx gold 10
  --family legacy --identify initial.card`).
- For a template it also reports the cyclic shift: other than 0, the
  template does not start where a burst does; re-extract it rather than
  regenerating the code.

### 6.5 Template Regeneration When Changing Devices

`template.npy` is **specific to a sample rate**. Switching from RTL-SDR
to Airspy Mini changes the samples per 11-bit code period from 4,914 to
12,285 — the old template won't correlate (`detect` warns when the
template holds no code at the sample rate). Whenever you change the
sample rate (or device):

1. `cp ~/Thrifty-x/example/detector_<device>.cfg detector.cfg` (in your
   working directory)
2. `thriftyx gold --identify template.npy --sample-rate <old rate>` to
   read the code off the old template, then `thriftyx template_generate
   <bits> <index> [--family ...] -o template_ideal.npy` with what it reports
3. `thriftyx capture initial.card --duration 10`
4. `thriftyx template_extract initial.card --template template_ideal.npy -o template.npy`

---

## 7. Quick Start: Single TX / Single RX Test

A complete first-light pipeline with a single beacon transmitter on
air.  It works in its own directory, so the checkout stays clean.

```bash
# 0. Activate the environment and make a working directory
source ~/Thrifty-x/.venv/bin/activate
mkdir -p ~/thriftyx-run && cd ~/thriftyx-run

# 1. Pick the device-specific config
cp ~/Thrifty-x/example/detector_r2.cfg detector.cfg   # adjust for your hardware

# 2. Short capture for template extraction
thriftyx capture initial.card --duration 5

# 3. Find the transmitter's code (Section 6.4) and generate it as a
#    theoretical seed template, e.g. for the 11-bit code 0:
thriftyx gold --identify initial.card
thriftyx template_generate 11 0 -o template_ideal.npy

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

- Step 2 / 5 — `block #N: mag[bin] = … (thresh = …, noise = …)` lines
  on stderr, one per detected block.
- Step 3 — `best match: …` with a correlation well above the
  runner-up's (Section 6.4).
- Step 6 — one summary line per block on stdout, like
  `blk=12; carrier: yes @ 50.171 kHz / 274:+0.21, SNR = ... ; corr: yes @ ...`;
  the number of `corr: yes` lines is the detection count (the same
  detections are written to `rx0.toad`).
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
| `analyze_beacon` | Diff in SoA of a beacon between two receivers, with clock-sync residuals in metres. `--beacon`, `--rx0`, `--rx1`, `-s` (sample rate; default: `sample_rate` or `device_type` of `detector.cfg`, or `-c` config). |
| `analyze_tdoa` | Per-slice statistics on `.tdoa` data. `--rx0`, `--rx1`, `--tx`, `--timestamp`. |

### Utilities

| Command | One-liner |
|---|---|
| `template_generate` | Generate an ideal code template. `bits` `index` [`--family gold\|legacy`] `-o file.npy`. |
| `template_extract`  | Extract a matched template from a capture. `input.card --template ideal.npy -o new.npy`. |
| `gold` | Print a code (`bits` `index` [`--family`]), or identify the one a capture or template holds (`--identify file.card`). |

### Common options

- `-o / --output` — output file.  `detect` writes `.toad` records only
  with `-o FILE` or `-a FILE`; without either it prints only its
  per-block summary lines, which are not a `.toad` file.  `identify`,
  `match`, `tdoa` and `pos` write `data.toads`, `data.match`,
  `data.tdoa` and `data.pos` by default.  These four and
  `template_extract` write the file only once their results are
  complete (`template_extract` reports a missing directory first), so a
  failed run leaves an existing file untouched.  `detect` opens it once
  the input, template and settings have loaded -- a run that fails to
  start keeps the old file -- and then streams detections into it, so
  an error part-way through a card leaves a partial file.  For these
  six commands `-o -` writes to stdout, and progress lines go to stderr
  instead.  `template_generate` and `template_extract` default to
  `template.npy` and `capture.npy`.
- `-a / --append` — append to an existing output file (`detect` only).
- `--quiet` — suppress per-block status output (`detect`).
- `--raw` — input is raw I/Q rather than `.card` (`detect`,
  `analyze_detect`).  Raw files record nothing about the capture, so
  the sample format and rate follow `--device-type` (default Airspy
  Mini: int16, 6 MSPS) unless `--bit-depth` / `--sample-rate` are given;
  for `rtl_sdr` output pass `--device-type rtlsdr`.

### Selected `capture` options

- `--device-type {rtlsdr, airspy_mini, airspy_r2}` — overrides config.
- `--duration <sec>` — stop after N seconds (default: until Ctrl+C).
- `--input <path>` — RTL-SDR only: read raw samples from a file or `-`
  (stdin) with the Python capture, instead of letting the `fastcard`
  binary open the dongle.  Without `fastcard` the Python capture reads
  stdin by default.  Useful with `rtl_sdr -f … -s … - | thriftyx
  capture … --device-type rtlsdr --input -`.
- `--fastcard <path>` — alternate path to the `fastcard` binary
  (RTL-SDR only). If the binary isn't on `PATH`, Thrifty-X falls back
  to its Python carrier detector.  Card data goes to the same place
  either way: the output file, or stdout for `-` or when stdout is a
  pipe.
- `--rotate <sec>` — start a new output file every N seconds, on
  wall-clock boundaries (`--rotate 3600` switches files on the hour on
  every receiver).  The output path is then a `strftime` pattern, e.g.
  `rx0_%Y%m%dT%H%M%S.card` (directories may use fields too, e.g.
  `%Y%m%d/rx0_%H%M%S.card`, and are created as needed); each file gets
  its own `#v2` header, and
  block indices continue across files, so sample-of-arrival stays
  continuous for the whole run.  Finished files can be processed or
  deleted while capture keeps running.  Not available with the
  `fastcard` binary.

Card lines are stamped with the time the block's last sample arrived
from the SDR, not the time the block was processed, so a host that
falls behind for a moment does not skew the timestamps `match` pairs
receivers by.  Samples lost on the way (USB overflow, a host too slow
for the rate) are replaced by zeros and reported on stderr: block
indices keep their meaning after a drop, and only detections that
overlap the gap are affected.

The output file is opened only once the SDR has been opened and
configured, so a capture that fails to start leaves an existing file
with the same name untouched.  A bad setting (unknown device type,
unparseable value or `airspy_serial`, a `carrier_window` outside the
FFT, a `chip_rate` impossible at the sample rate, invalid `--rotate`)
exits with status 78
(`EX_CONFIG`); systemd units use it to stop restarting a node whose
configuration needs fixing.

---

## 9. Understanding Detection Output

### 9.1 `.card` File Format

A `.card` file contains only blocks where a carrier was detected
(matching the original Thrifty's `fastcard` behaviour). Two on-disk
formats exist:

- **v1** (original Thrifty, RTL-SDR, no header) — lines of
  `<timestamp> <block_idx> <base64 of raw uint8 I/Q>`.
- **v2** (Thrifty-X) — a leading header line such as
  `#v2 bit_depth=12 sample_rate=6000000 endian=little block_size=32768 block_history=12349`,
  then the same data lines (int16 I/Q for `bit_depth=12`, uint8 for
  `bit_depth=8`).  Every Thrifty-X writer emits the header, including
  the Python RTL-SDR capture path.

`detect`, `analyze_detect` and `template_extract` apply the header's
`sample_rate`, `block_size`, `block_history` and `bit_depth` before
processing, overriding (with a warning) any explicit setting that
disagrees.  Headers written before `block_history` was recorded still
work: the history is re-derived for the recorded rate the same way the
capture derived it.  Headerless v1 cards from the original Thrifty are
decoded as 8-bit with the configured block geometry, so they are
usable without conversion.

### 9.2 `.toad` File Format

One detection per line, 12 whitespace-separated columns in this order
(`toads_data.DetectionResult.serialize`):

| # | Column | Meaning |
|---|---|---|
| 1 | `rxid` | Receiver ID (`detect --rxid`, or `rxid:` from the config); unique per receiver |
| 2 | `timestamp` | Linux epoch time at which the block's last sample arrived |
| 3 | `block` | Block index within the capture (continues across `--rotate` files) |
| 4 | `soa` | Sample-of-arrival: `block * (block_size - block_history) + sample + offset` |
| 5 | `corr_sample` | Correlation peak index within the block |
| 6 | `corr_offset` | Sub-sample offset of the correlation peak (clipped to ±0.6) |
| 7 | `corr_energy` | Correlation peak amplitude |
| 8 | `corr_noise` | Correlation noise RMS |
| 9 | `carrier_bin` | FFT bin nearest the carrier |
| 10 | `carrier_offset` | Sub-bin carrier offset, in [-0.5, 0.5] |
| 11 | `carrier_energy` | Carrier peak amplitude |
| 12 | `carrier_noise` | Carrier-detection noise RMS |

The `.toads` file produced by `identify` inserts a `txid` column after
`rxid` and drops per-receiver duplicates (a burst detected in two
overlapping blocks): of two detections with the same `rxid` and `txid`,
adjacent `block` indices and timestamps at most 1 s apart, the one with
the lower `corr_energy`.  The timestamp check keeps the detections of
another capture session, whose block index restarts at 0.

**Magnitude note for `carrier_energy` / `corr_energy`.** Samples are
normalised so that ADC full scale is `|z| = 1` on every device. RTL-SDR
uses `(x − 127.4) / 128`. For Airspy, libairspy's INT16_IQ output
left-shifts each 12-bit ADC code by 4 and converts the real stream to
I/Q with a unity-gain half-band filter, so a tone of A ADC codes
arrives as `|I + jQ| ≈ 8·A`, and full scale (2048 codes) is int16
16384; the detector divides by 16384. `scripts/airspy_scale_probe.sh`
reproduces the measurement on libairspy's own conversion code. On
hardware, a strong tone at high gain should top out near ±16 000 before
distorting; `python scripts/card_stats.py rx0.card` prints a card's
peak and RMS magnitude as a fraction of ADC full scale.

Airspy `.toad` files written before this scale was corrected used a
divisor of 2048, so their `carrier_energy`, `corr_energy` and noise
columns are 8× larger than current output for the same signal. SNR
values and the default `15*snr` thresholds are unaffected; any
threshold with an absolute constant term (for example `100c`) should be
re-tuned for Airspy.

### 9.2.1 Identifying transmitters (`identify --map`)

`thriftyx identify` writes a `.toads` file with a `txid` column. By
default it auto-classifies transmitters by clustering the
carrier-bin histogram. The auto-classifier
(`thriftyx/identify.py:detect_transmitter_windows`) handles the
common BatRF dual-bin pattern, but it is **not robust to extremely
uneven transmitter populations** (40:1 detection-count ratios can
make the weak transmitter disappear: the histogram peak of the rare
transmitter falls below the clustering threshold).

**For paper-grade or production captures, supply an explicit
frequency map via `--map`:**

```ini
# freqmap.cfg - one TX per line, value is "start - stop" in FFT bins
# (the carrier_bin column of the .toad files).
1: 100 - 105   # TX1 carrier sits in bins 100..105
2: 125 - 130   # TX2 carrier sits in bins 125..130

# Optional per-receiver bin offsets, for receivers whose LO is off.
# Key starts with '@' followed by rxid; a receiver without one uses 0.
@1: 2
```

```bash
thriftyx identify --map freqmap.cfg rx0.toad rx1.toad rx2.toad -o data.toads
```

The map is parsed by `thriftyx.identify.load_freqmap`.  Ranges are in
FFT bins only: identify does not know the sample rate and block size
that convert Hz (`bin = Hz * block_size / sample_rate`), so a range with
a `Hz` unit or a `k`/`M` prefix, or a line that does not parse, stops
identify with an error naming the line.  A range `start - stop` holds
the whole bins `start` to `stop`: a detection belongs to it when its
`carrier_bin + carrier_offset` is at least `start - 0.5` and below
`stop + 0.5`, after the range is shifted by the receiver's `@rxid`
offset (0 without one).  So `100 - 105` takes a carrier at bin 105 with
offset +0.3, and ranges of adjacent bins (`100 - 105`, `106 - 110`) do
not overlap.  A detection outside every TX range gets `txid = -1`
(sentinel for "unidentified") and is dropped from the `.toads` output
by `filter_duplicates`. A warning is logged for each unidentified
detection.

The auto-classifier is fine for ad-hoc inspection runs but the
explicit map is the recommended production workflow.

### 9.3 Detection Analysis Plots

`thriftyx analyze_detect <file.card> -m <N> -p <plots>` runs the
detector again on the first `N` blocks and renders one or more of the
following plot families.  By default it opens a single **unified Qt
viewer** with two tab bars — one for the block index, one for the plot
type — so every detection × every plot lives in one window (the same
layout as the original thrifty).  Install the `gui` extra
(`pip install -e ".[gui]"`) to enable it; without it, or with
`--no-gui`, a single matplotlib window opens instead (Left/Right switch
block, Up/Down switch plot, q quits).  On a machine without a display
neither viewer can open; use `--export` to write PNGs.
Use `-p overview` first.

1. **overview** — 4-panel: sample histogram + frequency-compensated
   magnitude over time + FFT (carrier search) + correlation output.
2. **time** — time-domain waveform (real + imaginary, magnitude).
3. **overlays** — captured signal overlaid on the template after
   alignment.
4. **spectra** — magnitude spectrum, with the carrier window shaded.
5. **corrs** — cross-correlation vs. autocorrelation, with the
   sub-sample interpolation (parabolic / Gaussian) overlay.

What "good" looks like:

- **Histogram** centred near 0, no clusters at the ends of the range
  (0 / 255 for RTL-SDR's unsigned bytes, about ±16384 for Airspy).
- **FFT** with a clear carrier peak inside the configured
  `carrier_window`.
- **Correlation** with one tall peak and a low side-lobe floor.
- **Overlays** with template and signal tracking each other.

What "bad" looks like:

- Histogram clustered at the extremes → ADC clipping (lower gain).
- Correlation peak buried in noise → wrong template (code index,
  register length or family), or bad gain.
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
| Zero detections | Template for another code (index, length or family) | `thriftyx gold --identify capture.card` (Section 6.4) |
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
  `export MPLBACKEND=TkAgg` (with WSLg) or use `--export PREFIX` to
  save PNG files to disk instead (`analyze_detect` writes
  `PREFIX_block<N>/<plot>.png`, `analyze_toads` `PREFIX_<n>.png`).
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

Each receiver's detections must carry its own `rxid` (`detect --rxid
N`, or `rxid:` in its `detector.cfg`; Section 5.2), and that `rxid` is
the id of the receiver's line in `pos-rx.cfg`.  Detections of several
receivers under one `rxid` cannot be matched: `match` then finds no
pairs, and `identify` warns about files that hold one `rxid` over the
same period.

Receiver and beacon coordinates live in `pos-rx.cfg` and
`pos-beacon.cfg`, one `id: x y` line each (metres, any Cartesian
frame).  Every line in both files has the same number of coordinates:
with `id: x y z` the tag's height is solved too, which needs at least
4 receivers; `id: x` gives a 1-D position along the line of the
receivers and needs at least 2 (a tag beyond the outermost receiver has
that receiver's TDOAs, and is placed there).  A receiver pair that
never heard a beacon together gets no TDOA (counted as a failure),
while the other pairs are estimated.
End-to-end multi-receiver documentation will be added as the
integration testing matures.

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
