# Bench: RTL-SDR vs Airspy R2 CW level with an external amplifier

A to Z procedure for measuring, with the Thrifty-X code, how strongly an
RTL-SDR and Airspy R2 units receive the same CW tone at their lowest
gain settings, behind an external amplifier, over a range of generator
levels.

**Current workflow:** the N9310A is controlled automatically over USB by
default. The operator reviews the sweep plan and swaps only the receiver at
the fixed RF-chain endpoint. See
[n9310a_usb_control.md](n9310a_usb_control.md) for USB/WSL setup and SCPI
validation. Set `GENERATOR_MODE=manual` only when front-panel operation is
desired.

It repeats the 2026-06-10 gain-equivalence bench (N9310A straight into
each receiver, correlation SNR; its findings are in that deck) with an
amplifier in the chain and a plain CW tone, so the figures describe the
receivers alone.  The R2 settings swept by default are the ones that
bench compared: 0/0/0, 0/0/8 (the RTL's gain-0 registers) and the
per-unit RTL-equivalent settings (unit A 0/0/10, unit B 0/0/11).

## 1. What is measured

`scripts/bench_cw_level.py` captures each setting for a few seconds and
reduces it, the same way for every receiver, to:

| Figure | Meaning | Compare across devices? |
|---|---|---|
| `cn0_dbhz` | carrier-to-noise density, dB-Hz (Welch PSD, ~150 Hz bins, noise 50–300 kHz from centre) | **yes**: independent of sample rate, FFT size and ADC scale; this is the sensitivity figure |
| `carrier_dbfs` | tone power, dB below the device's own digital full scale | **no** as RF level: full scale is device-relative (RTL: 8-bit output word; Airspy: ADC sine). R2 0/0/8 reads ~21 dB below RTL gain 0 for the same input ([audit](rtl_vs_airspy_dbfs_audit.md)); compare only within one device type |
| `noise_dbfs_hz` | noise density next to the tone | within one device type; across types only through C/N0 (same device-relative scale as the carrier) |
| `near_fs_frac` | samples within 2 % of full scale | clipping flag: rows above 1e-5 are excluded |
| `registers` | the R2's R820T2 registers during the capture | check against `scripts/r820t_register_model.py` |

A change in C/N0 is a change in correlation SNR at the same code length
and sample rate, so C/N0 differences compare directly with the June
bench's correlation-SNR differences.

RTL gain is set in **manual** mode through `rtl_sdr` (`-g 0` would be
AGC; the script passes `-g 0.1`, which librtlsdr snaps to manual 0 dB,
the same registers as fastcard's gain 0: LNA 0 / Mixer 0 / VGA 8).  The
script stops if `rtl_sdr` reports AGC.

## 2. Hypotheses and pass criteria (proposed)

1. **Linearity.**  Carrier dBFS vs generator level has slope
   1.00 ± 0.05 in the clean band for every setting.  The top of the
   sweep where it bends (or `*` marks clipping) is the compression limit.
2. **Amplifier-limited noise.**  If the amplifier's noise dominates (RF
   off noise with the amplifier ≥ 10 dB above the terminated-input
   noise, step 7.4), every receiver sees the same noise, so
   **ΔC/N0 vs RTL gain 0 → 0 dB** even at R2 0/0/8.  A remaining gap
   then lies after the tuner (ADC / DDC), not in its noise figure.
3. **Equivalence.**  At the per-unit settings (A 0/0/10, B 0/0/11)
   |ΔC/N0| ≤ 0.5 dB, repeatable to σ ≤ 0.3 dB over three runs.

## 3. Equipment

- Agilent N9310A (CW, level set by hand), its RF cable
- External amplifier (record model, gain G, NF and P1dB from its sheet)
- **DC block(s)** (inner/outer), SMA/N adapters, one fixed cable to the
  receiver, optional fixed attenuator
- 50 Ω SMA terminator (noise reference)
- RTL-SDR: Nooelec NESDR SMArTee-family unit used in this bench (RTL2832U + R820T2, TCXO; hardware bias-tee behavior must be recorded)
- Airspy R2 unit A (0x637862DC2E602DD7) and unit B (0xB01861DC393A891F)
- Windows laptop with WSL2 (Ubuntu), USB 2.0/3.0 port, powered USB hub
  optional

## 4. Safety

- Receiver input stays below **−20 dBm** (the script refuses steps above
  it; both receivers are damaged around +10 dBm).  Check the
  amplifier's output P1dB too.
- Choose one amplifier-power topology and use it consistently. If the
  amplifier is powered from the **receiver-side bias tee**, there must be
  a DC path from receiver to amplifier (so do not put a DC block between
  those two points). If the amplifier is powered externally, isolate any
  receiver-side bias voltage with a DC block so the amplifier is not
  double-powered.
- The Airspy R2 bias tee is software switched. RTL-SDR bias-tee behavior is
  hardware-dependent and cannot be switched by this script; SMArTee-family
  units can have an always-on bias output. Record the actual condition with
  `AMP_POWER` and `RTL_BIAS_TEE_NOTE`.
- Do not assume equal bias-tee current capability across receivers. Verify
  the amplifier current requirement before using receiver bias power.
- Change cables only with the generator's RF **OFF**.

## 5. Wiring

```
Receiver-bias-powered amplifier:
N9310A RF OUT ─ [DC-safe input / DC block as required] ─ AMP ─ cable ─ receiver bias tee

Externally powered amplifier:
N9310A RF OUT ─ AMP ─ DC block ─ cable ─ receiver
                   └─ external amplifier supply
```

Use the **same** chain for every receiver; move only the receiver end
of the last cable.  Measure the losses you can (adapters, attenuator)
into `LOSS`; the receiver input is logged as `tx + AMP_GAIN − LOSS`.

## 6. N9310A settings and control

For automatic operation, first verify the connection:

```bash
python scripts/n9310a_control.py status
```

The automated bench sets frequency, level and RF output itself with readback
verification and returns RF to OFF before receiver swaps and on exit.

For manual/fallback operation:

1. **Preset**, then wait for self-test.
2. **Frequency** → `161.315` **MHz** (the receivers tune 161.300 MHz; the
   tone lands 15 kHz above centre, clear of the RTL-SDR's DC spike).
3. **Mod On/Off** → modulation **off** (no MOD annunciator); LF output
   off.
4. **Amplitude** → the first level the script asks for (e.g. `-125`
   **dBm**).  Leave amplitude offset at 0 dB.
5. **RF On/Off** → toggles as the script asks (`RF OFF` steps measure
   the noise floor with the chain connected).
6. Let the generator warm up ~30 min for frequency/level stability.
   The script logs the tone's frequency error in ppm (generator and
   receiver crystal together).

## 7. Laptop

### 7.1 Windows: pass the USB devices to WSL (PowerShell as admin)

```powershell
winget install --interactive --exact dorssel.usbipd-win   # once
usbipd list                                  # RTL2832U 0bda:2838, Airspy 1d50:60a1
usbipd bind --busid <BUSID>                  # once per device/port
usbipd attach --wsl --busid <BUSID> --auto-attach   # keep open in its own window
```

Attach only the receiver under test; re-attach after re-plugging (or
keep `--auto-attach` running per port).

### 7.2 WSL: tools, udev rules, venv (once)

```bash
cd ~/Thrifty-x
git fetch origin
git checkout master && git pull          # or the branch named in the PR
scripts/bench/wsl_setup.sh               # apt packages, udev rules, venv, self-test
. ~/thriftyx-venv/bin/activate
```

The venv goes to `~/thriftyx-venv` unless you name another:
`scripts/bench/wsl_setup.sh .venv` reuses a venv inside the checkout
(then activate `.venv/bin/activate` here and in 8.2).

If udev is not running in WSL (no systemd), after every attach:
`sudo chmod 666 /dev/bus/usb/*/*`.

### 7.3 Check the receivers

```bash
lsusb | grep -Ei '0bda:2838|1d50:60a1'
rtl_test -t                       # "Found Rafael Micro R820T tuner"
airspy_info                       # both R2 serials (attach one at a time)
rtl_test -s 2400000               # 10 s: no "lost at least ... bytes"
```

`ldconfig -p | grep librtlsdr` names the RTL library.  Ubuntu's
package is osmocom librtlsdr; the Blog fork writes a few tuner
registers differently (user guide §4.7).  The sweep logs which one
ran.

### 7.4 Noise reference (terminated input, no amplifier)

With a 50 Ω terminator on each receiver's input:

```bash
python scripts/bench_cw_level.py measure --unit RTL --device rtlsdr \
    --rtl-gains 0 --tx-dbm off --out bench/run1/terminated.csv
python scripts/bench_cw_level.py measure --unit R2-A --device airspy_r2 \
    --airspy-serial 0x637862DC2E602DD7 --stages 0/0/0,0/0/8,0/0/10,0/0/11 \
    --packing --tx-dbm off --out bench/run1/terminated.csv
```

Compare its `noise_dbfs_hz` with the sweep's `RF off` column: ≥ 10 dB
higher with the amplifier means the amplifier sets the noise.

## 8. The sweep

### 8.1 Level plan

Receiver input = generator level + G − LOSS.  Cover from below the
noise to about −35 dBm at the receiver in 5 dB steps; the N9310A goes
down to about −127 dBm, so add a fixed attenuator if G is large.

| Amp gain G | `LEVELS` (generator, dBm) | Receiver input (dBm) |
|---|---|---|
| 10 dB | `off,-125:-45:5` | −115 … −35 |
| 20 dB | `off,-125:-55:5` | −105 … −35 |
| 30 dB (+10 dB atten.) | `off,-125:-55:5`, `LOSS=10` | −105 … −35 |

Per receiver: RTL 1 setting, R2 4 settings × 5 s per level: about
5 min (RTL) and 12 min (each R2) for 15 levels.

### 8.2 Run everything

```bash
. ~/thriftyx-venv/bin/activate
cd ~/Thrifty-x
AMP_GAIN=20 LOSS=0.5 AMP_POWER="external supply" RUN=run1 \
    scripts/bench/run_cw_bench.sh
```

The script asks you to connect RTL, then R2-A, then R2-B. In the default
`GENERATOR_MODE=auto` mode it programs every N9310A level itself; you do
**not** touch the generator between levels. At receiver-swap prompts the
script first turns RF OFF. Use `GENERATOR_MODE=manual` to retain the older
per-level front-panel prompts.  Each receiver keeps its settings for the
whole sweep; only the generator changes.  Take as long as you like at a
prompt: the R2 keeps streaming (one RX start per sweep, so the tuner is
calibrated once) but its driver discards the samples while no capture is
running, so waiting cannot fill the driver's 4 s buffer or leave stale
samples for the next measurement.  Results:
`bench/run1/results.csv`, `run_info.txt`, `report.md`,
`cw_levels.png`.

Repeat as `RUN=run2`, `RUN=run3` (the June bench used n = 3), swapping
the receiver order between runs.  Other variables:

| Variable | Default | Use |
|---|---|---|
| `UNITS` | `RTL R2-A R2-B` | subset / order |
| `R2_STAGES` | `0/0/0,0/0/8,0/0/10,0/0/11` | R2 settings per level |
| `R2_RATES` | `10M` | Space-separated rates, e.g. `"2.5M 10M"` to run both automatically |
| `GENERATOR_MODE` | `auto` | `manual` keeps the old front-panel generator workflow |
| `N9310A_RESOURCE` | `auto` | Explicit VISA resource when multiple generators are attached |
| `PACKING` | `1` | 12-bit USB packing (fewer drops over usbip) |
| `CAPTURE_SECONDS` | `5` | per setting and level |
| `R2_BIAS_TEE` | `0` | `1` powers the amplifier from the R2's bias tee (DC-safe chain only); keep `0` with a separately powered amplifier |
| `AMP_POWER` | `external` | how the amplifier is powered, recorded in `run_info.txt` |
| `REF` | `RTL:g0` | report reference (defaults to the first unit's first setting without RTL) |

One receiver by hand:

```bash
python scripts/bench_cw_level.py sweep --unit R2-B --device airspy_r2 \
    --airspy-serial 0xB01861DC393A891F --stages 0/0/8,0/0/11 --packing \
    --levels off,-125:-55:5 --amp-gain 20 --out bench/run1/results.csv
```

### 8.3 Check the R2 registers

Each R2 row stores the tuner registers read during its capture.  Check
one against the model (it must match; a difference means the firmware
or a leftover register write is not what the comparison assumes):

```bash
python - <<'PY' > /tmp/r2regs.txt
import csv
row = next(r for r in csv.DictReader(open('bench/run1/results.csv'))
           if r['unit'] == 'R2-A' and r['setting'] == '0/0/8')
print(row['registers'])
PY
python scripts/r820t_register_model.py -f 161.3M --airspy-rate 10M \
    --lna 0 --mixer 0 --vga 8 --dump /tmp/r2regs.txt
```

## 9. Report and reading it

Why R2 carrier dBFS sits ~21 dB below the RTL's at matched gain codes (and
why that is not a sensitivity loss): [dBFS audit](rtl_vs_airspy_dbfs_audit.md).
Recompute the cross-device statistics from any set of CSVs with
`python scripts/bench_cw_audit.py bench/<run>/*.csv`.

```bash
python scripts/bench_cw_level.py report bench/run1/results.csv \
    --ref RTL:g0 --band -110:-65 --plot bench/run1/cw_levels.png
```

- **C/N0 table**: per unit:setting and generator level; `*` = clipping.
- **Against RTL:g0**: mean ΔC/N0 (sensitivity), its σ over the band,
  Δcarrier (gain structure), the RF-off noise, and the carrier slope.
  Set `--band` to the levels where every setting is above noise and
  below clipping.

| Result | Reading |
|---|---|
| ΔC/N0 of 0/0/8 ≈ 0 with the amplifier, where the June bench (no amplifier) had a gap | that gap was receiver noise figure; behind the field LNA the receivers are equivalent at matched gain |
| ΔC/N0 of 0/0/8 keeps the June gap | the gap is after the noise-setting stage (IF/ADC/decimation), not tuner NF |
| A − B keeps a constant offset | unit-to-unit: keep per-unit settings |
| slope < 0.95 at the top | compression; exclude those levels, lower G or LOSS the top |
| `dropped` > 0 on R2 rows | samples were lost during that capture and the affected blocks are left out (fewer `segments`): USB/usbip overflow, so use `PACKING=1`, a direct port, or `R2_RATE=2.5M`; if `notes` says "… of them buffer overflow" the host itself was too slow to consume the stream |

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `usb_open error -3` / `airspy_open() failed` | permissions: udev rule (7.2) or `sudo chmod 666 /dev/bus/usb/*/*`; device attached to WSL? |
| `rtl_sdr did not report a manual gain` | old rtl_sdr; check `rtl_sdr -h`, reinstall `rtl-sdr` |
| No tone detected at any level | generator RF off / wrong frequency (161.315 MHz), DC block orientation, amplifier unpowered |
| Tone detected with RF OFF | a real narrowband signal in the ±3 kHz search window (generator leakage, a nearby emitter, a receiver spur): check with the terminator. `tone_offset_hz` and `excess_sigma` of the RF-off rows (`scripts/bench_cw_audit.py` lists them) say where it is and how strong; the same offset on every row is a spur. Detection is against the noise around the tone, so a merely raised noise floor near centre no longer reads as a tone |
| C/N0 drops at high levels | clipping (`*`) or amplifier compression: stop the sweep lower |

## 11. Keep

`bench/<run>/` (CSV, run_info, report, plot), the amplifier model and
settings, cable/attenuator losses, generator and receiver serials, room
temperature, and photos of the chain.

### Bias-tee powered amplifier metadata

When the inline amplifier is powered from the receiver-side bias tee, set
`AMP_POWER="receiver bias tee"`. The script now records RTL bias behavior as
hardware-dependent instead of claiming it is off. Use
`RTL_BIAS_TEE_NOTE="always-on (NESDR SMArTee)"` for the current RTL bench
unit.


## 12. R2 10 MSPS intermittent-stream diagnostic

A repeated diagnostic isolates the intermittent broadband bursts observed at
10 MSPS. The fixed RF chain, bias-tee-powered AIS preamp, 0/0/8 gain, packing,
generator levels and capture timing are held constant. Only sample rate and
whether the benchmark issues R820T2 register-read control transfers are
changed.

Default matrix:

| Condition | Rate | R820T2 reads | Purpose |
|---|---:|---|---|
| A_2p5_reg_on | 2.5 MSPS | on | known-clean low-bandwidth control |
| B_10m_reg_on | 10 MSPS | on | reproduce the disturbed condition |
| C_10m_reg_off | 10 MSPS | off | isolate register-control transfers from the 10 MSPS stream |

Run three balanced repetitions:

    RUN=r2_stream_diag1 REPEATS=3 PAIRS=3 \
    AMP_GAIN=20 AMP_POWER="receiver bias tee" \
    bash scripts/bench/run_r2_stream_diagnostic.sh

Each condition starts with RF OFF, alternates -100/-90 dBm for PAIRS pairs,
and ends RF OFF. The condition order rotates between repetitions so each
condition occupies early/middle/late positions.

The diagnostic summary flags RF-ON rows with any reported sample drop, any
near-full-scale sample, a >3 dB broadband-noise jump, or a >6 dB RMS jump.
Outputs are written under bench/<RUN>/, including summary.md and combined.csv.

Interpretation:
- A clean, B disturbed, C clean -> register-read/control-transfer hypothesis
  supported.
- A clean, B disturbed, C disturbed -> 10 MSPS streaming/USB/libairspy/usbipd
  remains the leading path.
- A disturbed too -> the problem is not confined to 10 MSPS; revisit RF/bias
  power and general USB health.
- B and C both clean -> the intermittent failure was not reproduced; increase
  repetitions before assigning a cause.

The normal benchmark can also skip Airspy register reads directly with
R2_SKIP_REGISTERS=1 (or bench_cw_level.py --skip-registers).


## Full RTL/R2 equivalence qualification

For the publication-oriented paired validation using 5 dB input steps,
multiple tone offsets, RTL gain 0, Airspy 2.5/10 MSPS, matched R820T gain-code
points, held-out input calibration, and separate RTL/R2 hardware-swap runners,
see `docs/bench/rtl_r2_equivalence_validation.md`.

Run with one shared RUN name:

    RUN=equiv_YYYYMMDD_01 bash scripts/bench/run_rtl_equivalence_validation.sh
    # swap only RTL -> R2
    RUN=equiv_YYYYMMDD_01 bash scripts/bench/run_r2_equivalence_validation.sh
    python scripts/bench/compare_receiver_equivalence.py bench/equiv_YYYYMMDD_01
