# RTL-SDR vs Airspy R2: the ~20.9 dB carrier-dBFS difference

Audit of why the same CW tone reads about 20.9 dB lower in Airspy R2 dBFS
than in RTL-SDR dBFS at matched R820T gain codes, while C/N0 is nearly
equal.

**Verdict B: a receiver calibration difference, not a normalisation bug.**
The software path is proven to within 0.1 dB. The 20.9 dB lies in the
receivers' hardware RF-to-digital-full-scale transfer. Which hardware
stage contributes how much is **not resolved**: see
[Remaining unknowns](#remaining-unknowns).

Labels used below: **MEASURED** (bench data), **SOURCE** (proven from the
pinned upstream source), **TEST** (reproduced by code in this repository
or by running upstream code), **INFERRED** (reasoned, not proven),
**UNKNOWN**.

## 1. Provenance

| Item | Value |
|---|---|
| Thrifty-X at the start of the audit | `d941a65f391fe71bad59a45781529e8b82a4749a` (origin/master; the experiments ran on this commit) |
| libairspy | [airspy/airspyone_host](https://github.com/airspy/airspyone_host) `fc61ab6be57ed61f0e2bdd9c6dfae74cacef57d0`: `libairspy/src/airspy.c`, `iqconverter_int16.c`, `filters.h` |
| Airspy firmware | [airspy/airspyone_firmware](https://github.com/airspy/airspyone_firmware) `cf1a37440d40e4229e9b474077e9fdd56f4926b1`: `common/airspy_nos_conf.c` (R2), `common/r820t.c`, `airspy_m4/adchs.c`, `airspy_m4/airspy_m4.c` |
| librtlsdr | [osmocom/rtl-sdr](https://github.com/osmocom/rtl-sdr) `797f8143266d983c56d8f35d2d442527529dd8a5` (Release 2.0.3): `src/librtlsdr.c`, `src/tuner_r82xx.c`, `src/rtl_sdr.c`, `src/convenience/convenience.c` |
| Original Thrifty | [swkrueger/Thrifty](https://github.com/swkrueger/Thrifty) `2ad9775753a8712a61c81cc78fb0bc75a921d50b` |

### Bench data status

The verification CSVs (`rtl_161p2{85,315,322}_results.csv`,
`r2{a,b}_161p*_{2p5m,10m}_results.csv`) are **not in the repository** or
on any of its branches. They exist only on the bench laptop. The numbers
in §2 are therefore the **reported** summary statistics. They have **not
been independently recomputed** in this audit. To recompute every one of
them from the raw rows:

```bash
python scripts/bench_cw_audit.py <all verification CSVs> --setting g0,0/0/8
```

The script classifies rows by their own `unit`, `device`, `rate`,
`tone_hz` and `setting` columns, not by file name. It uses only RF-ON,
detected, unclipped rows for carrier, noise and C/N0, and RF-OFF rows
only for the RF-OFF noise and false-detection counts. It prints σ for
ddof 0 and ddof 1, and is tested in `tests/unit/test_bench_cw_audit.py`.
Treat this audit's conclusions as provisional until that output has
been checked against the table below.

## 2. Experiment and reported results

Matrix: centre 161.300 MHz; tones at 161.285, 161.315 and 161.322 MHz
(−15, +15 and +22 kHz); generator RF OFF, −100, −80, −70, −60 dBm, RF OFF;
external amplifier +20 dB nominal. RTL-SDR (NESDR SMArt v5, RTL2832U +
R820T2) at 2.4 MSPS and manual gain 0. Airspy R2 units A
(`0x637862DC2E602DD7`) and B (`0xB01861DC393A891F`) at 2.5 and 10 MSPS,
LNA/Mixer/VGA 0/0/8.

Reported (MEASURED, over 3 tones × 4 RF-ON levels):

| Comparison | Δcarrier (dB) | ΔC/N0 (dB) | Δnoise density (dB/Hz) |
|---|---:|---:|---:|
| R2-A 2.5M − RTL | −20.956 (σ 0.272) | — | — |
| R2-A 10M − RTL | −20.910 (σ 0.275) | −0.944 | −19.97 † |
| R2-B 2.5M − RTL | −20.916 (σ 0.268) | — | — |
| R2-B 10M − RTL | −20.824 (σ 0.234) | −1.639 | −19.19 † |
| R2-A 10M − 2.5M | +0.045 | +6.838 | −6.793 |
| R2-B 10M − 2.5M | +0.091 | +6.258 | −6.166 |
| R2 B − A, 2.5M | +0.040 | −0.114 | +0.154 |
| R2 B − A, 10M | +0.086 | −0.694 | +0.781 |

† Derived here as Δcarrier − ΔC/N0 from the reported means, not
reported directly.

Other reported figures: the frequency error was −5.0 to −5.3 ppm on every
receiver. Its being the same on three different crystals points to the
generator's reference (INFERRED). RF-OFF detections: RTL 0/6, R2-A 2.5M
2/6, R2-B 2.5M 0/6, R2-A 10M 6/6, R2-B 10M 6/6 (§10). Dropped-sample
reports on the R2 were intermittent, in transfer-sized multiples (§11).

What the numbers already establish (MEASURED):

1. The 20.9 dB is a **scale** factor. It is constant over 40 dB of input
   (σ ≈ 0.25 dB), over three tone offsets (so it is not an IF filter
   edge), over two R2 units (B − A ≈ +0.04 to +0.09 dB), and over both
   R2 sample rates (10M − 2.5M ≈ +0.05 to +0.09 dB).
2. It is **not an RF sensitivity loss**. At 10 MSPS the R2's C/N0 is
   within 0.9–1.6 dB of the RTL's. The noise density moves down with
   the carrier, by about 19–20 dB.

## 3. Definitions

| Term | Definition in Thrifty-X |
|---|---|
| host digital full scale | `raw_to_complex` output magnitude `|z| = 1` |
| RTL-SDR `|z| = 1` | `(x − 127.4)/128` for each uint8 I and Q word. `|z| = 1` is the **8-bit output word** of the RTL2832U, i.e. after its ADC and digital down-converter (DDC). |
| Airspy `|z| = 1` | int16 / 16384. `|z| = 1` is a **full-scale real sine at the 12-bit ADC** (2048 codes), i.e. before libairspy's host-side DDC. The int16 container itself reaches `|z| ≈ 2`. |
| ADC full scale | The ADC's clip level, in codes (Airspy: ±2048 around 2048; RTL2832U: UNKNOWN, not publicly documented) |
| dBFS | `10 log10(power)` with power in `|z|²`, so 0 dBFS = a complex tone of `|z| = 1` |
| `carrier_dbfs` | Welch-PSD tone power integrated over the peak ± 4 bins, minus the noise in those bins: `A²` for a tone `A e^{jωt}` |
| `noise_dbfs_hz` | Median PSD over 50–300 kHz from centre, corrected to the mean. Units: `|z|²`/Hz. |
| C/N0 | `carrier_dbfs − noise_dbfs_hz` (dB-Hz). Scale-free, so it compares across devices. |
| analog RF input level | dBm at the receiver's SMA connector (generator + amplifier − loss) |
| tuner IF output level | R820T2 differential IF output voltage, driving each receiver's ADC. **Not observable in this data.** |

Normalising each device's digital full scale to `|z| = 1` makes the
arithmetic of the DSP identical. It does **not** make equal RF input
produce equal `|z|`. That would also need equal RF-to-full-scale gain:
tuner IF output, the IF-to-ADC network, the ADC's input range and (RTL)
DDC word scaling. None of these is shared between the two designs. The
two "full scales" are not even taken at the same point of the chain (ADC
input vs DDC output).

## 4. Software path: Airspy INT16_IQ scaling

### 4.1 Source (SOURCE: libairspy `fc61ab6`)

- `airspy.c`: `SAMPLE_RESOLUTION 12`, `SAMPLE_ENCAPSULATION 16`,
  `SAMPLE_SHIFT 4`. `convert_samples_int16()` does
  `dest[i] = (src[i] - 2048) << SAMPLE_SHIFT`.
- For `AIRSPY_SAMPLE_INT16_IQ` the consumer thread runs
  `convert_samples_int16()` then `iqconverter_int16_process()` and
  halves the sample count. This is the only sample type the HAL requests
  (`airspy_mini.py`, `airspy_set_sample_type(INT16_IQ)`; the callback
  rejects any other type). Thrifty-X never calls
  `airspy_set_conversion_filter_*`, so libairspy's default
  `HB_KERNEL_INT16` is used.
- `iqconverter_int16_process()` = `remove_dc()` then `translate_fs_4()`:
  - `remove_dc()`: `y[n] = x[n] − x[n−1] + (32100/32768)·y[n−1]` in
    fixed point (a first-order DC blocker on the *real* ADC stream).
  - `translate_fs_4()`: multiplies samples 0..3 of every 4 by
    −1, −½, +1, +½ (`-s>>1`, `s>>1`). It then runs `fir_interleaved()`
    on the even lane (the half-band's non-centre taps, `acc >> 15`) and
    `delay_interleaved()` on the odd lane (the half-band's centre tap
    0.5, which is the `>> 1` above).
  - `HB_KERNEL_INT16`: 47 taps. The centre tap is 16384 (0.5 in Q15).
    The even-index (non-centre) taps sum to 16356, so the DC gain is
    32740/32768 = −0.0074 dB.
- Packing (`airspy_set_packing`): `unpack_samples()` restores each
  12-bit code (`& 0xfff`) into a uint16 before the same
  `convert_samples_int16()`. Firmware `pack()` in `airspy_m4.c` is pure
  bit-packing. **Packing cannot change the scale** (SOURCE).
- Firmware (`adchs.c`, `airspy_m4.c`): ADCHS samples go to the USB
  buffers by DMA with no arithmetic (SOURCE).

### 4.2 Derivation

Take an ADC tone `c[n] = 2048 + A cos(ωn)` at the real rate
`fs_adc = 2 × I/Q rate`.

1. The shift gives `x[n] = 16 A cos(ωn)`.
2. `remove_dc` has gain `G_dc(ω) = |1 − e^{−jω}| / |1 − 0.97961 e^{−jω}|`.
   At the IF, ω ≈ π/2: `G_dc = √2 / √(1 + 0.97961²) = 1.01024`
   (**+0.0885 dB**).
3. The pattern −1, −½, +1, +½ is `u[n] = −jⁿ · x[n]`, with the imaginary
   part halved to fold the half-band centre tap in. It shifts the
   spectrum by +fs/4. `A cos` splits into two halves of amplitude `A/2`:
   the one at −(fs/4 − δ) lands at +δ (passband); the one at
   +(fs/4 − δ) lands near fs/2 (stopband).
4. The half-band polyphase output at fs_adc/2 is `H(ω)` at DC,
   0.99915 (**−0.0074 dB**).
5. So `|I + jQ| = 16 · ½ · G_dc · H = 8 · 1.01024 · 0.99915 · A = 8.075 A`,
   and a full-scale ADC sine (A = 2048) gives **16 540**, not 16 384.
   The error of the constant is **20 log10(16540/16384) = +0.081 dB**.

### 4.3 Reproduction (TEST)

`scripts/airspy_scale_probe.sh` compiles libairspy's own
`iqconverter_int16.c` + `HB_KERNEL_INT16` at `fc61ab6` and feeds 12-bit
tones at the bench's IF offset. Result:

| rate | f / fs_adc | 64 codes | 256 | 1024 | 2000 | 2047 |
|---|---|---:|---:|---:|---:|---:|
| 10 MSPS | 0.24925 | +0.083 dB | +0.081 | +0.081 | +0.081 | −3.9 (wraps) |
| 2.5 MSPS | 0.24700 | +0.084 dB | +0.082 | +0.082 | +0.082 | −3.3 (wraps) |

(dB relative to 8 per code, i.e. to `/16384`.) The converter is linear
to about 2000 codes (−0.2 dBFS). Above ~2027 codes the int16 output of
`remove_dc` wraps, so the last ~0.1 dB below ADC full scale is unusable
on the host (SOURCE + TEST). The 10M − 2.5M difference of the converter
gain is 0.0007 dB.

`tests/unit/test_dbfs_scaling.py` models the same structure in Python.
It uses a locally designed half-band, and no upstream code or taps,
because libairspy's licence restricts reuse. The model matches the
closed form to 0.02 dB and the real libairspy output to 0.007 dB, which
is the upstream kernel's DC gain.

**Result:** `AIRSPY_INT16_FULL_SCALE = 16384` is correct to **+0.08 dB**
for the definition "full-scale ADC sine → `|z| = 1`". Class A
(normalisation bug) and class C (IQ conversion misunderstood) are
excluded: they cannot contribute more than 0.1 dB of the 20.9 dB.
The residual is not worth changing the constant for. Doing so would
break the bit-exact C/Python match that a power of two provides.

Note on the convention: libairspy's own `FLOAT32_IQ` path scales by
`1/2048` before the same conversion, so there a full-scale ADC sine is
`|z| = 0.5` (−6.02 dBFS). The Thrifty-X choice (ADC clip = 0 dBFS) gives
the Airspy the *highest* dBFS of the plausible conventions. A container
convention (`/32768`) would widen the RTL gap to ~26.9 dB.

## 5. Software path: RTL-SDR uint8 scaling

- `raw_to_complex(bit_depth=8)`: `(x − 127.4)/128`, inherited unchanged
  from original Thrifty (`thrifty/block_data.py`,
  `fastcard/rawconv.c`) (SOURCE). Codes 0 and 255 map to −0.995 and
  +0.997. So `|z| = 1` is the full range of the 8-bit output word.
- The 127.4 offset (instead of the mid-code 127.5) adds a DC term only.
  The tone (±15/22 kHz ± 3 kHz) and noise (50–300 kHz) bins exclude DC.
  `/128` vs `/127.5` is 0.03 dB. Class B (RTL normalisation bug) is
  excluded.
- `tests/unit/test_dbfs_scaling.py` quantises complex tones through
  `complex_to_raw`/`raw_to_complex` and reads −1, −20 and −40 dBFS back
  to 0.1 dB.

**What RTL 0 dBFS is physically: UNKNOWN.** With librtlsdr, the R820T is
run at a low IF. `rtlsdr_set_sample_rate(2.4 MSPS)` →
`r82xx_set_bandwidth(2.4 MHz)` → IF **1.815 MHz** (`reg_0b = 0x8F`),
`rtlsdr_set_if_freq`. The RTL2832U samples the real IF with its I-ADC
only (`demod 0:0x08 = 0x4d`, Zero-IF off `1:0xb1 = 0x1a`, spectral
inversion `1:0x15 = 0x01`). It mixes digitally, low-pass filters with
`fir_default` (coefficients sum to 4238; the fixed-point normalisation
is undocumented, and if it is 4096 that is +0.30 dB, INFERRED), resamples
to 2.4 MSPS (`rsamp_ratio`, `1:0x9f/0xa1`), and emits 8-bit I/Q. Neither
the ADC's input range nor the DDC's word scaling nor which bits are
emitted is publicly documented. A real→complex DDC would keep half a
real tone's amplitude unless it scales by 2 (INFERRED; the actual
factor is UNKNOWN). So the RTL's 0 dBFS may lie above, at or below its
own ADC clip.

## 6. Gains and AGC (SOURCE)

**RTL-SDR, `rtl_sdr -g 0.1` (the bench's `RtlSdr.gain_argument`).**
`rtl_sdr.c` parses tenths of a dB (`gain = 1`). Because `gain != 0` it
takes the manual path: `nearest_gain()` → 0 (the first entry of
`r82xx_gains`) → `verbose_gain_set(dev, 0)` →
`rtlsdr_set_tuner_gain_mode(1)` → `r82xx_set_gain(manual=1, 0)`. The
loop exits at once: LNA index 0, mixer index 0, LNA and mixer auto off
(`0x05[4] = 1`, `0x07[4] = 0`), VGA fixed `0x0C[3:0] = 8` (mask 0x9f).
`-g 0` would have selected AGC (`verbose_auto_gain`). A nonzero request
is therefore required to get the 0 dB manual entry. The bench refuses
to run if `rtl_sdr` reports anything else.

Downstream of the tuner, `rtlsdr_init_baseband()` writes: SDR mode with
DAGC off (`0:0x19 = 0x05`), `en_dagc` off (`1:0x11 = 0`), and the RF/IF
AGC loop off (`1:0x04 = 0`). `rtl_sdr` never calls `rtlsdr_set_agc_mode`,
which is the only switch for the RTL2832U digital AGC (`0x19 = 0x25`). No
AGC is active in the benchmark path. The DDC gain is fixed but, as
above, of unknown value.

**Airspy R2, 0/0/8.** `apply_gain_mode('manual', lna_agc=False,
mixer_agc=False)` sets the LNA and mixer codes to 0 and VGA to 8
(`r820t_set_vga_gain` writes `0x0C` mask 0x0f). The register dumps show
`0x0C = 0x48` at both rates.

**Tuner register comparison.** `scripts/r820t_register_model.py` replays
both drivers. At 161.3 MHz, RTL 2.4M gain 0 vs R2 10M 0/0/8, the
**gain codes are equal** (LNA 0, mixer 0, VGA 8). Several signal-path
bits still differ: 0x05[7], 0x06 (PDET/FILT_3DB/PW_LNA), 0x08[6], 0x0A
(filter power/Q/code), 0x0B[7] (narrow IF filter), **0x0C[5] (RTL 1,
Airspy 0)**, 0x19[5], 0x1A[3] and 0x1E. The vendor does not publish the
gain effect of these bits (UNKNOWN). "Same gain codes" therefore proves
equality at those fields only, not equal tuner IF output.

## 7. Airspy 2.5 vs 10 MSPS

SOURCE (`airspy_nos_conf.c`, `r820t.c`):

| | 10 MSPS | 2.5 MSPS |
|---|---|---|
| ADC real rate | 20 MHz (`adchs_idivb = 0`) | 5 MHz (`adchs_idivb = 3`) |
| R820T IF (`r820t_if_freq`) | 5.000 MHz | 1.250 MHz |
| `r820t_bw` | 59 = 0x3B | 0 |
| `r820t_set_if_bandwidth`: `0x0A = 0xB0 | opt[bw&15]` | 0xB4 | 0xBF |
| `0x0B = 0x0F | modes[bw>>4]` | 0x0F (widest coarse) | 0xEF (narrowest coarse) |
| `0x0C` (VGA) | 0x48 | 0x48 |

These match the reported register dumps exactly. So the IF filter is
reconfigured (0x0A/0x0B) and the VGA is not (0x0C).

- **Carrier unchanged** (+0.05/+0.09 dB, MEASURED). Explained by two
  facts. The host conversion gain is identical at both rates (4.3,
  0.0007 dB, TEST). And the tone sits in the IF passband at both
  settings (1.25 MHz − 15 kHz narrow, 5 MHz − 15 kHz wide). The
  benchmark's PSD arithmetic is rate-invariant too (§8).
  **The 20.9 dB is not a sample-rate effect.**
- **Noise density +6.2 to +6.8 dB at 2.5M** (MEASURED). Two mechanisms
  each predict about 10 log10(20/5) = **6.02 dB**, and both are INFERRED:
  (a) ADC-referred noise (quantisation, thermal, jitter) spread over
  2.5 MHz instead of 10 MHz of Nyquist band; (b) wideband analog noise
  (amplifier + tuner) beyond 2.5 MHz, not removed by the R820T's IF
  low-pass before 5 MHz sampling, aliasing in. The 0.2–0.8 dB excess
  and its unit dependence suggest a mix. **Discriminating test:** RF-OFF
  `noise_dbfs_hz` at 2.5M for VGA 0, 8 and 11. If it stays flat with
  VGA, the ADC dominates. If it follows the VGA dB for dB, analog noise
  is aliasing. The earlier finding that R2 needed VGA 10–11 to match
  RTL correlation SNR without an amplifier is consistent with a
  noticeable post-VGA (ADC-side) noise contribution at low gain
  (INFERRED).

## 8. Benchmark PSD arithmetic (TEST)

`scripts/bench_cw_level.py` `figures()`:

- PSD = `|FFT(w·z)|² / (rate · Σw²)`. By Parseval a tone `A e^{jωt}`
  integrates to exactly `A²`, and white noise of variance σ² reads
  σ²/rate per Hz, for any FFT size or rate.
- `TONE_HALF_WIDTH_BINS = 4`: a Hann-windowed tone keeps 99.998 % of its
  power within ±4 bins in the worst case (half-bin offset: −0.00008 dB).
- `n0 · bins · df` subtracts the noise inside those bins, so
  `carrier_dbfs` is noise-free power.
- The median-to-mean correction `k / gammaincinv(k, 0.5)` is right for
  the Gamma(k) distribution of a k-segment average. Without it the noise
  reads 0.5 dB low at k = 3.
- `fft_size()` gives 73–146 Hz bins at every rate.

Tests in `tests/unit/test_dbfs_scaling.py` (plus the existing
`test_figures_recover_tone_and_noise`):

- The same tone and noise read the same carrier (±0.05 dB), noise
  (±0.1 dB) and C/N0 at 2.4, 2.5 and 10 MSPS.
- The Airspy chain (ADC codes → libairspy-structured model → `/16384` →
  `figures`) reads `20 log10(A/2048) + 0.08 dB` at 2.5 and 10 MSPS,
  linearly from 16 to 1800 codes.
- A pure scale factor g moves carrier and noise by exactly g dB and
  leaves C/N0 and `detected` unchanged, which is the signature measured
  here.

## 9. Gain ledger

| Stage | RTL-SDR (NESDR v5) | Airspy R2 | Gain / scale | Status |
|---|---|---|---|---|
| RF input | common chain | common chain | 0 dB reference | MEASURED |
| R820T2 LNA | code 0, manual | code 0, manual | equal codes | SOURCE (+ R2 register dump) |
| R820T2 mixer | code 0, manual | code 0, manual | equal codes | SOURCE (+ dump) |
| R820T2 VGA | code 8 (`0x0C=0x68` predicted) | code 8 (`0x0C=0x48` dumped) | equal code, 0x0C[5] differs, effect unknown | SOURCE / UNKNOWN |
| other tuner signal-path bits | librtlsdr | airspy fw | 0x05, 0x06, 0x08, 0x0A, 0x0B, 0x19, 0x1A, 0x1E differ | SOURCE (bits) / UNKNOWN (dB) |
| tuner IF, filter | 1.815 MHz, LPF ≈ 2.43 MHz total (`0x0B=0x8F`) | 5 MHz wide (10M) / 1.25 MHz narrow (2.5M) | R2's own two settings agree within 0.1 dB | SOURCE; RTL effect INFERRED small |
| tuner IF output level | → RTL2832U ADC | → R2 IF network → LPC4370 ADCHS | ? | UNKNOWN |
| ADC | RTL2832U internal, 28.8 MHz, I-ADC only | LPC4370 ADCHS 12-bit, 20 / 5 MHz | input range ? | UNKNOWN (no public full-scale voltage for either path as built) |
| ADC → digital | DDC: mixer, `fir_default` (Σ 4238), resampler, 8-bit out | firmware: none (DMA) | RTL: ? · R2: 1 | UNKNOWN / SOURCE |
| AGC | DAGC off, RF/IF AGC loop off, tuner auto off | LNA/mixer AGC off | none active | SOURCE |
| host IQ conversion | none (hardware DDC) | libairspy INT16_IQ: 8.075 per code | R2: +0.081 dB vs 8 | SOURCE + TEST |
| host raw range | uint8 0..255 | int16, FS sine ±16 540 | — | SOURCE |
| Thrifty normalisation | `(x−127.4)/128` → word FS = 1 | `/16384` → ADC FS sine = 1 | correct for each definition | SOURCE + TEST |
| benchmark PSD | rate-invariant | rate-invariant | 0 dB | TEST |
| **RF → dBFS, net** | reference | **−20.9 dB** (≈ ×1/11.1 amplitude) | | MEASURED |

The software rows (host conversion, normalisation, PSD) sum to **< 0.1 dB**.
The whole 20.9 dB therefore lies in the rows marked UNKNOWN: tuner IF
output and the bits that differ, the IF-to-ADC networks, the two ADC
input ranges, and the RTL2832U DDC scaling. No split between them is
claimed.

## 10. Hypotheses

| Class | Verdict | Evidence |
|---|---|---|
| A. Thrifty-X Airspy normalisation bug | **Excluded** | §4: 16384 is right to +0.08 dB; libairspy's own code reproduced |
| B. RTL normalisation bug | **Excluded** | §5: `/128` is the word full scale, inherited from Thrifty; 127.4 affects DC only |
| C. libairspy IQ conversion misunderstood | **Excluded** | §4.2 closed form = measurement (8.075/code); packing is scale-neutral |
| D. RTL2832U fixed DDC gain / ADC-to-word mapping | **Possible contributor, unquantified** | Scaling undocumented; §5 |
| E. Airspy ADC full-scale mapping (IF network, ADCHS range) | **Possible contributor, unquantified** | No public R2 IF-to-ADC schematic or verified ADCHS range |
| F. Different tuner IF/filter configuration | **Unlikely to be large** | The R2's own narrow vs wide IF filter changes the carrier by < 0.1 dB; the same tone position across ±15/+22 kHz; other bits unquantified |
| G. Hidden AGC / gain stage | **Excluded for AGC** | §6: every AGC in both paths is off; scale constant over 40 dB (slope ≈ 1) |
| H. Different RF-to-digital-FS calibration despite equal tuner codes | **Supported** | Invariant, gain-independent offset; C/N0 nearly equal; software ruled out |
| I. Combination | **Supported as H = some mix of D, E (and a small F)** | Split UNKNOWN |

### Remaining unknowns

- The RTL2832U's ADC full-scale voltage, its DDC word scaling, and
  which bits reach the 8-bit output.
- The Airspy R2's IF-to-ADC network gain and the ADCHS full-scale
  voltage as configured (`POWER_CONTROL`, `CONFIG` in `adchs.c`).
- The R820T2 IF output level vs the undocumented register bits that
  differ between the drivers.
- Whether RTL-SDR clips at its output word or at its ADC first.

Measurements that would narrow them, each with existing tools:

1. **Clip-point sweep.** Raise the input on each receiver until
   `near_fs_frac` > 0 or the slope bends. The R2 must clip at ≈ 0 dBFS
   (its ADC). If the RTL bends below 0 dBFS, its ADC clips before its
   output word, and part of the gap is RTL DDC headroom (class D). Keep
   below the amplifier's P1dB and the −20 dBm guard (`--force` only
   with a verified chain).
2. **VGA sweep at both R2 rates, RF off and on** (§7). This separates
   ADC-limited from analog-limited noise. If the 20.9 dB changes with
   VGA code, the difference is before the VGA.
3. **Oscilloscope or spectrum analyser on the R820T2 IF output** of
   both boards at the same RF input. This would split the ledger into
   tuner and IF-to-ADC parts directly.

## 11. Answers

1. **What caused the ~20.9 dB?** Each receiver maps a different RF input
   power to its own digital full scale. The same RF input therefore lands
   20.9 dB lower relative to the Airspy's ADC full scale than relative to
   the RTL2832U's output word.
2. **Hardware vs software?** Software contributes +0.08 dB (the
   `remove_dc` gain in libairspy) and nothing else measurable. The
   remaining ~20.8 dB is hardware: the IF path, the ADCs and the RTL DDC.
   The split within the hardware is unresolved.
3. **Was `AIRSPY_INT16_FULL_SCALE = 16384` correct?** Yes, to +0.081 dB,
   for "full-scale ADC sine = `|z| = 1`".
4. **Was RTL `/128` correct?** Yes, for "full 8-bit output word =
   `|z| = 1`".
5. **Is dBFS comparable across devices as RF power?** No. It is
   device-relative. The old comment in `block_data.py` claimed otherwise
   and has been corrected.
6. **Why is the R2 carrier the same at 2.5M and 10M?** Same tuner gain
   codes, and the tone is in both IF passbands. The host converter gain
   is identical (0.0007 dB), and the bench PSD is rate-invariant.
7. **Why is noise/C/N0 6–7 dB worse at 2.5M?** A 4× lower ADC rate
   concentrates ADC-referred noise and/or aliases unfiltered wideband
   noise. Both predict 6.02 dB. Which one dominates is untested (§7).
8. **Is 0/0/8 a defensible match for RTL g0?** At the tuner gain-code
   level, yes: LNA 0 / mixer 0 / VGA 8 on both. It is not an equal
   end-to-end gain (§6, §9). For equal *sensitivity* at low gain, the
   earlier bench's per-unit settings (A 0/0/10, B 0/0/11) remain the
   empirical match.
9. **Which metric compares devices?** C/N0 (or correlation SNR), plus
   an RF-referenced level only with a per-device, per-setting
   calibration against a known input. Carrier and noise dBFS compare
   only within one device type and configuration.
10. **What had to change in production code?** No numerical behaviour.
    The normalisation comment in `block_data.py` / `rawconv.c`, the
    bench docstring, and the README / user guide / fastcapture README
    statements that full scale (and so absolute levels and threshold
    constants) are "the same on both" were corrected. Old `.card` and
    `.toad` files are unaffected.

Optional calibrated dBm is **not** added. A single bench point per
device and setting would be needed first, plus a check that the offset
is gain-code independent (measurement 2). Until then any dBm figure
would rest on an assumption.

## 12. Secondary: RF-OFF detections (fixed separately)

This issue is independent of the dBFS scale: the detection test is
invariant to any scale factor (TEST). The detector has since been
changed; see the follow-up PR and `tests/unit/test_bench_cw_detection.py`.
The findings that led to the change:

- The old rule was `excess > 5·σ`, with the excess taken over the
  **50–300 kHz** noise median and `σ = n0·df·√(bins/k)`, which assumes
  independent bins. Hann bins are correlated (power correlation 4/9
  between neighbours, 1/36 two apart). The true σ of a 9-bin sum is
  about 1.35× larger.
- With k = 381–732 segments (5 s), 5σ was only a 6–9 % (≈ 0.3–0.4 dB)
  excess. The peak is also the maximum of ~40–80 bins (±3 kHz), which
  biases the excess upward.
- Simulated through the real windowed FFT (exact bin correlation) at
  2.4 MSPS, k = 732: flat white noise gave 0/20 detections, but a
  noise floor +0.3 dB higher within ±40 kHz of centre gave **20/20**.
  So did a floor +1 dB at DC falling to 0 dB at 60 kHz. A floor near
  centre a few tenths of a dB above the 50–300 kHz band is therefore
  enough to explain 6/6 at R2 10M. The actual R2 10M spectrum was not
  available, so which structure it has (raised floor or skirt, or a
  spur inside the search window) is still INFERRED.

The detector now measures the excess over the noise **around** the
tone: the median of a ring on each side of the search window,
interpolated in dB to the tone bins. Its σ counts the window's bin
correlation and the local estimate's own error. In the same simulations
z is N(0, 1) on white noise, and raised or sloped floors give no
detections. A C/N0 of ~22 dB-Hz is still detected at 2.4 MSPS / 5 s.
A narrowband spur inside the search window is real power and is still
reported. The new `excess_sigma` and `local_noise_dbfs_hz` columns, and
the RF-OFF table of `scripts/bench_cw_audit.py`, show which of the two
an RF-off detection is.

## 13. Secondary: intermittent R2 dropped samples (separate issue)

This issue is unrelated to amplitude. Any block with a drop, and the
queued gap behind it, is excluded before the PSD (`Airspy.capture`), so
a drop cannot bias carrier or noise. Two observations, both INFERRED and
not fixed here:

- libairspy counts a loss when its 16-deep internal queue is full, and
  reports it with the next delivered transfer. A queue that fills in
  transfer-sized multiples points to the consumer thread (the Python
  callback, which needs the GIL) stalling for more than ~80–100 ms. For
  example, it could stall while the main thread runs `Accumulator.add`
  on 8 × 131 072 samples.
- A loss that occurred while buffering was paused (during the
  register-read USB transfers) but is reported by the first transfer
  after `resume_buffering()` is counted in that row's `dropped`. This
  inflates the per-row count without affecting the data used.
