# Airspy IQ Format Diagnostic Report

- Date: 2026-05-14
- Working directory: `/home/user/Thrifty-x`
- Branch: `claude/diagnose-airspy-iq-format-BZzTO`
- Artifacts: `diag/` directory (includes scripts, synthetic .card files, FFT plots, text logs)
- Changes: **No code modifications** (diagnostic only)

> **UPDATE 2026-05-14 (reflecting real-data results)** — Validating the hypothesis with an actual R2 capture
> (`gs_r2_161_3_20260513_152100_TX2_Gain000/b000/capture.card`),
> **Scenario 1-bis (FLOAT32 silent fallback) was NOT confirmed in this capture**.
> For details see the **§9 Phase E — Real-data Re-verification** section at the end of this report.
> The body §1-§5 preserves the original analysis based on the synthetic controls as-is, and §9 takes precedence.

---

## 0. Environment / Data Availability Note

- The R2 capture directory specified by the prompt, `~/github/Thrifty-x/example/gs_r2_161_3_20260513_153826`,
  **does not exist** in the current workspace (`/home/user/Thrifty-x` and `~`).
- The R2 USB device is also not connected in this environment (no `airspy_info` / `lsusb` results).
- Therefore the *measured-data* portions of Phase B-1 / B-2 / B-3 could not be performed. Instead:
  - Analysis scripts (`diag/check_card_format.py`, `diag/check_fft_dualbin.py`,
    `diag/expected_carrier_bin.py`) were written and saved to disk.
  - Using project code (`thriftyx.block_data.complex_to_raw`, etc.) directly,
    **two kinds of synthetic .card files** were generated and analyzed as controls:
    - `diag/synth_int16_iq.card` — the output of the normal INT16_IQ path (the format the current code emits).
    - `diag/synth_float32_misread.card` — the output of the hypothetical "libairspy operated as FLOAT32_IQ but
      the HAL labeled the byte stream on disk as INT16 verbatim" scenario.
- As soon as an actual R2 capture file is obtained, re-running the same scripts with a `<path/to/file.card>` argument
  will automatically populate all the verdict cells in this report.

---

## 1. Phase A Results: Code Analysis

See `diag/phase_a_code_analysis.md` for the detailed table. Summary:

| Item | Result |
|------|------|
| Whether `airspy_set_sample_type` is called | **Yes** — both the Python HAL (`airspy_mini.py:361-364`) and the C fastcapture (`airspy_reader.c:201-202`) call it with `AIRSPY_SAMPLE_INT16_IQ`(=2). |
| Callback data dtype | **`int16`** (Python: `np.frombuffer(..., dtype=np.int16)`; C: `int16_t *`) — consistent. |
| `raw_to_complex(bit_depth=12)` normalization | `floats / 32768.0` (assumes full int16 range). Matches the `airspy_reader.c` comment *"scaled to full int16 range by libairspy"*. |
| `.card` v2 storage dtype | int16 IQ interleaved, base64. Header `#v2 bit_depth=12 sample_rate=…`. |
| Capture path | `thriftyx capture` → `_capture_airspy()` (`airspy_capture.py:326`) → Python HAL. **The C fastcapture is not called on the Airspy path.** |
| Surface-level inconsistency | **None**. All three layers (configuration, callback, normalization) assume INT16_IQ. |

**Potential weakness 1 (strong candidate):** `airspy_mini.py:361-364`
```python
ret = _lib.airspy_set_sample_type(
    self._handle, ctypes.c_int(AIRSPY_SAMPLE_INT16_IQ))
if ret != 0:
    logger.warning("airspy_set_sample_type() failed: %d", ret)
```
On failure it **only logs a warning and proceeds**. If libairspy fails to set the sample_type for that device,
data comes in at the default value (`AIRSPY_SAMPLE_FLOAT32_IQ` = 0), and the callback reinterprets it as int16.
4-byte float32 IQ → 2-byte int16 IQ gets incorrectly truncated, so the spectrum is corrupted.

**Potential weakness 2 (weak candidate):** There are known cases where calling `airspy_set_packing(True)`
resets the internal sample_type state in some libairspy builds. However, `example/detector_r2.cfg` has no
`packing` key, so the default value (`False`) is likely applied, making this path unlikely.

**Potential weakness 3 (weak candidate):** R2 LO leakage is larger than usual → the coexistence of DC near
bin 0 and the actual carrier bin could look like a "dual bin". However, the fact that SNR drops by nearly
25 dB from 40→14 dB is hard to explain by DC leakage alone.

---

## 2. Phase B Results: Data Verification (Control Comparison)

Script: `diag/check_card_format.py`
Log: `diag/phase_b_card_analysis.txt`

| Metric | INT16_IQ control (`synth_int16_iq.card`) | FLOAT32 misread control (`synth_float32_misread.card`) |
|---|---|---|
| Block raw bytes | **262144** | **524288** |
| bytes / complex sample assuming block_size=65536 | **4.00** ✔ | **8.00** ✗ |
| int16 interpretation mean | -8.49 | -75.48 |
| int16 interpretation std | 23160 | 17687 |
| int16 interpretation range (-32768..+32767) | PASS | PASS |
| float32 interpretation NaN | present (interpretation fails) | none |
| float32 interpretation range [-2, +2] | FAIL (NaN) | **PASS** (because the actual data was float32) |
| uint8 interpretation centred near 127.4 | PASS (coincidental) | PASS (coincidental) |
| Automatic verdict | "INT16_IQ scaled to FULL int16 range" | **"AMBIGUOUS — both interpretations plausible"** |

> The decisive clue is the **block raw byte count**. The normal path is `block_size * 2 (IQ) * 2 bytes(int16) = 262144`.
> If the raw bytes per block in the R2 capture file were 524288 (i.e., 8.00 bytes/complex sample), that is
> evidence that libairspy emitted FLOAT32_IQ (4 bytes/component) and the HAL stored that byte stream verbatim
> → **Scenario 1-bis confirmed**.

> When R2 measured data is obtained, an immediate verdict is possible with the single line:
> ```
> python diag/check_card_format.py /path/to/R2_capture.card
> ```
> The report's "FORMAT VERDICT" section directly classifies it as either INT16_IQ / FLOAT32 misread.

---

## 3. Phase C Results: FFT Analysis (Control Comparison)

Scripts: `diag/check_fft_dualbin.py`, `diag/expected_carrier_bin.py`
Log: `diag/phase_c_fft_analysis.txt`
Plots: `diag/phase_c_fft_comparison_synth_int16_iq.png`,
       `diag/phase_c_fft_comparison_synth_float32_misread.png`

### 3.1 Normal INT16_IQ Control

When FFT'd with the INT16 interpretation, a **single strong peak** is observed:

```
bin    20  f=    3.05 kHz  mag=  96.32 dB
bin    19  f=    2.90 kHz  mag=  57.68 dB   (adjacent leakage)
bin    21  f=    3.20 kHz  mag=  57.44 dB   (adjacent leakage)
bin    18  f=    2.75 kHz  mag=  51.54 dB
bin    22  f=    3.36 kHz  mag=  51.39 dB
```

1 peak, peak-to-floor ≈ 40 dB. **Consistent with normal operation by the RTL-SDR baseline.**

### 3.2 FLOAT32 misread Control

After saving the same synthetic data via the float32-misread path, FFT with the **(incorrect) int16 interpretation**:

```
bin 65516  f= 4998.47 kHz  mag=  89.40 dB    ← mirror peak
bin    20  f=    1.53 kHz  mag=  89.39 dB    ← first peak
bin 65476  f= 4995.42 kHz  mag=  79.82 dB    ← mirror peak
bin    60  f=    4.58 kHz  mag=  79.80 dB    ← second peak ★
bin 65436  f= 4992.37 kHz  mag=  75.91 dB
bin   100  f=    7.63 kHz  mag=  75.17 dB    ← third peak
```

**Multiple peaks + mirror + reduced peak-to-floor gap.** A pattern that qualitatively matches the user report.

### 3.3 Matching Against the User-Reported Values

| Item | User report (R2 7_7_7) | INT16 control | FLOAT32 misread control |
|---|---|---|---|
| Number of carrier peaks | **2** (bin ~20, ~72) | 1 (bin 20) | **2~3** (bin 20, 60, 100, multiple mirrors) |
| Carrier SNR | ~14 dB | ~40 dB | ~10–15 dB (peak-to-adjacent-peak margin) |
| Δbin (observed) | 52 (≈ 7.93 kHz) | — | 40 (≈ 3.05 kHz) ※ |
| Qualitative match | — | ✗ (single peak) | ✔ (multiple peaks) |

※ The difference between the user-reported Δbin=52 and the control's Δbin=40 is because **the TX frequency, center freq,
block_size, and signal SNR all differ**, so the absolute positions differ, but the phenomenon itself of "a single carrier
spreading into multiple bins" is reproduced. The control started from a synthetic tone (3.05 kHz), but on misread strong
peaks at 1.53 kHz / 4.58 kHz / 7.63 kHz appeared simultaneously. Therefore the two bins in the user report are also very
likely **"a separation, by the same mechanism, of an originally single carrier due to incorrect time alignment"**.

### 3.4 Expected Carrier bin

The `tuner_freq: 166M` in `example/detector_r2.cfg` is 4.7 MHz away from the 161.3 MHz TX, far outside the carrier_window
(bin 7-124, i.e., 1.07–18.9 kHz). That is, the capture specified in the prompt was likely performed with a **separate
detector.cfg (probably `tuner_freq: 161.3M` or a value very close to it)**. In that case:

- center=161.300 MHz → expected carrier bin = 0 (DC)
- If center was off by ±a few kHz, bin 20 (~3 kHz) falls precisely within a reasonable range.

Therefore, interpreting the **"real" carrier as the single bin 20** and bin 72 as an artifact caused by format corruption
is consistent with all the evidence gathered so far.

---

## 4. Root Cause Verdict

### Top hypothesis (strong): **silent fallback from `airspy_set_sample_type(INT16_IQ)` failure**

- In the code flow, if the ret value of `set_sample_type` is non-zero, it only logs `logger.warning` and ignores it
  (`airspy_mini.py:361-364`).
- If libairspy fails that call, the device operates at the **default value, FLOAT32_IQ**.
- The callback reinterprets the data as `np.int16`, so the byte alignment of every IQ sample is off and the spectrum
  breaks.
- In the synthetic control (Phase C 3.2), this scenario reproduced the **multiple-peaks + SNR collapse** pattern.
- Verification method: temporarily insert `raise DeviceConfigError(...)` at `airspy_mini.py:361-364` and retry the
  R2 7_7_7 capture. If an exception fires immediately in the same environment, the top hypothesis is confirmed.

### Second hypothesis (auxiliary): another libairspy API call (e.g. `set_packing`, `set_samplerate`) resets sample_type in some builds

- There are reports that in some builds the sample_type reverts to the default value immediately after `airspy_set_samplerate`.
- The code flow is: set sample_type in `device.open()` → call `set_sample_rate()` (`airspy_capture.py:381`),
  so if the R2 firmware/libairspy build behaves that way, the result is identical to the top hypothesis.
- Verification method: temporarily patch it to call `set_sample_type` once more **immediately after `set_sample_rate`**
  rather than right after `device.open()`, and compare the R2 capture results.

### Third hypothesis (weak): abnormal DC leakage of the R2

- bin 20 (~3 kHz) is the real carrier, and bin 72 is an R2 image leak or a strong noise peak.
- However, the 25 dB SNR drop exceeds the level of simple LO leakage and fits better with format corruption.

---

## 5. Recommended Fix Direction (this report does not modify anything)

- [ ] **Fail-fast handling of the HAL's `set_sample_type` return value**: change the `logger.warning` at
      `airspy_mini.py:361-364` to
      `raise DeviceConfigError(f"airspy_set_sample_type failed: {ret}")`.
      The fastcapture's `airspy_reader.c:201-202` already fails fast via `goto err`.
- [ ] **Re-set sample_type after `set_sample_rate`**: as a safety margin, add a sample_type re-setting method
      (e.g., `device.ensure_int16_iq()`) immediately after the `device.set_sample_rate(...)` call in `_capture_airspy`.
      This avoids the reset issue in some libairspy builds.
- [ ] **sample_type diagnostic output on opening**: logging at INFO which sample_type is active along with
      `libairspy_version()` would allow identifying the same symptom within 1 second if it recurs.
- [ ] **Optionally, the normalization constant in `raw_to_complex`**: if some R2 builds emit 12-bit data
      not expanded to full int16 but in the **raw 12-bit range (-2048..+2047)**, then `/2048.0` must be applied
      instead of `/32768.0` for SNR to recover normally. If both the raw bytes value from Phase B and the
      `as_int16.max()` value are small (e.g., max ≤ 2047, std ≤ 1000), suspect this path.

---

## 6. Post-Fix Verification Criteria

- [ ] Confirm a single carrier bin in the R2 7_7_7 capture (dual bin resolved)
- [ ] `python diag/check_card_format.py <new_capture.card>` gives the "INT16_IQ scaled to FULL int16
      range" verdict + bytes/complex sample == 4.00
- [ ] The INT16-interpretation FFT of `python diag/check_fft_dualbin.py <new_capture.card>` shows a single peak +
      peak-to-floor ≥ 35 dB
- [ ] carrier SNR ≥ 35 dB, corr SNR ≥ 35 dB (compared against the RTL-SDR baseline)
- [ ] When set with `apply_gain_mode('manual', lna=7, mixer=7, vga=7)`, the reported gain value is displayed
      as an actual value rather than 0.00 dB

---

## 7. Quick Reproduction / Re-run Guide

Once an R2 capture file (`.card`) is obtained, the follow-up analysis of this report can be automatically refreshed with the single line below.

```bash
cd /home/user/Thrifty-x
python3 diag/check_card_format.py /path/to/R2_capture.card  | tee diag/phase_b_real.txt
python3 diag/check_fft_dualbin.py  /path/to/R2_capture.card | tee -a diag/phase_c_real.txt
python3 diag/expected_carrier_bin.py                          | tee -a diag/phase_c_real.txt
```

- The FORMAT VERDICT line in `phase_b_real.txt` immediately verifies the top hypothesis.
- The INT16 vs FLOAT32 interpretation peak comparison in `phase_c_real.txt` is the auxiliary confirmation.

## 8. Artifact List (`diag/` directory)

| File | Description |
|---|---|
| `phase_a_code_analysis.md` | Phase A static code analysis results (detailed table + scenario checklist) |
| `check_card_format.py` | Decodes a .card file's v2 base64 payload and interprets it comparatively as int16 / float32 / uint8. Multi-block statistics possible via the `--all-blocks` option |
| `check_fft_dualbin.py` | Plots the FFT of the first block of a .card in both INT16/FLOAT32 for comparison |
| `check_signal_strength.py` | caller SNR / noise RMS / ADC clipping / carrier bin histogram of all blocks + recommended gain value |
| `expected_carrier_bin.py` | Computes the expected carrier bin from TX frequency / center / sample_rate + cfg sniff |
| `_synth_card.py` | Synthetic .card generator for the normal INT16 path + the hypothetical FLOAT32-misread path |
| `synth_int16_iq.card` | Normal INT16_IQ control (262144 bytes/block) — gitignore |
| `synth_float32_misread.card` | Scenario 1-bis control (524288 bytes/block) — gitignore |
| `phase_b_card_analysis.txt` | check_card_format analysis output for the two controls |
| `phase_b_synth.txt` | Synthetic .card generation log |
| `phase_c_fft_analysis.txt` | check_fft_dualbin + expected_carrier_bin output |
| `phase_c_fft_comparison_synth_int16_iq.png` | FFT of the INT16 control (single peak) |
| `phase_c_fft_comparison_synth_float32_misread.png` | FFT of the FLOAT32-misread control (multiple peaks) |
| `iq_format_diagnosis_report.md` | (this report) |

---

## 9. Phase E — Real-data Re-verification (UPDATE 2026-05-14)

### 9.1 Test Target

```
/home/batrf/github/Thrifty-x/example/
    gs_r2_161_3_20260513_152100_TX2_Gain000/b000/
        capture.card
        capture.log
        detect.log
        detector.cfg
```

### 9.2 capture.log Inspection Results

There is **no `airspy_set_sample_type() failed` or any sample_type-related warning in the log whatsoever**.
The only warning is the user notice regarding bias_tee=true. → **Scenario 1-bis (silent FLOAT32 fallback) of §1
did not occur in this capture**.

### 9.3 capture.card Format Inspection Results

| Metric | Measured value | Interpretation |
|---|---|---|
| Header | `#v2 bit_depth=12 sample_rate=10000000` | normal v2 |
| Block count after base64-decoding | 55 | — |
| Decoded bytes per block (identical for all blocks) | **262144** | block_size 65536 × 2 (IQ) × 2 bytes (int16) = 262144 ✔ |
| bytes/complex sample | **4.00** | INT16 IQ |
| int16 interpretation mean/std | min/max -153/+159, mean ≈ -0.29, std ≈ 15.94 | normal signal (centered near DC, small amplitude) |
| float32 interpretation | finite_ratio ≈ 0.51, min/max -3.3e+38 / 1.5e-38, many NaN/Inf | **clearly non-float32** |

→ This capture is a **normal INT16_IQ v2 .card**. The top hypothesis of §1 **does not hold** for this data.

### 9.4 New Top Cause (real-data based)

Inspection of `detector.cfg` and `capture.log` shows that the **gain stages are all set to 0**:

```
lna_gain:   0
mixer_gain: 0
vga_gain:   0
gain mode: manual; LNA=0 Mixer=0 VGA=0   ← capture.log
```

Because of this:

- The carrier peak magnitude is just above threshold (`mag[16] ≈ 0.6–0.9`, threshold ≈ 0.6–0.7,
  noise ≈ 0.2),
- Carrier SNR is mostly around the 12–14 dB level,
- Correlation almost always fails. In `detect.log` the blocks with `corr: yes` can be counted on one hand
  (blk 1747 = 12.06 dB, blk 2141 = 12.26 dB, blk 2196 = 11.80 dB).

Carrier bin distribution: mostly bin 16 (≈ 2.44 kHz @ 10 MSPS / 65536), occasionally bin 32 / 48.
→ **Single peak**, i.e., the multiple-peak (dual-bin) phenomenon of §3.2 is not reproduced in this capture.

> Therefore the limiting cause of this capture is **insufficient signal strength, not format corruption**.
> Scenario 1-bis still has meaning as a *defensive safety margin*, but it is not the direct cause of this capture.
> The 7_7_7 capture mentioned in the original prompt (`...153826` directory, carrier dual bin + 14 dB) and this capture
> (`...152100_TX2_Gain000`, gain=0, single bin + 12-14 dB) appear to be **different experiment sessions**, and if
> the original 7_7_7 capture is obtained, separate re-verification is needed.

### 9.5 Recommended Actions (real-data based, in priority order)

1. **Step up gain incrementally** — re-capture at the same antenna/distance in the following order:
   - `lna=4 mixer=4 vga=4` → confirm SNR change
   - if insufficient, `lna=6 mixer=6 vga=6`
   - if still insufficient, `lna=8 mixer=6 vga=6` (LNA has the largest impact on NF)
   - target: carrier SNR ≥ 25 dB, ADC clipping 0
2. **Check bias_tee** — if there is no active LNA in the antenna chain, set `bias_tee: false`. This prevents
   ~+4.5 V from flowing into a passive antenna.
3. **Keep the fail-fast patch** — although it is not the cause of this capture, the silent fallback of
   `airspy_set_sample_type` could *someday* mask the same kind of bug, so as a safety hardening measure
   `logger.warning` → `raise DeviceConfigError` is recommended (§5 item 1).
4. **Check template / timing** — if correlation keeps failing even after raising gain, suspect a template
   mismatch / timing problem. Out of scope for this report.

### 9.6 New Tools / Improvements

Added/improved in `diag/` per the follow-up request of this Phase E:

| Change | File | Notes |
|---|---|---|
| Enhanced | `check_card_format.py` | Added `--all-blocks` mode: outputs the card version/header, bit_depth, sample_rate, decoded block count, unique block sizes, bytes/complex, overall int16 plausibility statistics, overall float32 plausibility statistics, and carrier bin histogram all at once |
| New | `check_signal_strength.py` | carrier SNR / noise RMS / ADC clipping / carrier bin histogram + RMS amplitude distribution of all blocks. Automatically reads the `carrier_window` / gain from the sibling `detector*.cfg` and outputs a one-line RAISE/LOWER gain recommendation |

### 9.7 Quick Reproduction Guide

```bash
cd /home/user/Thrifty-x

# Format check (all-block statistics)
python3 diag/check_card_format.py <CARD> --all-blocks | tee diag/phase_e_format.txt

# Gain / SNR / clipping diagnosis + recommended values
python3 diag/check_signal_strength.py <CARD> | tee diag/phase_e_signal.txt

# Expected carrier bin
python3 diag/expected_carrier_bin.py | tee diag/phase_e_bin.txt
```

Putting the full path of `capture.card` in place of `<CARD>` automatically populates the table in §9.3 and
directly outputs the recommended gain values.
