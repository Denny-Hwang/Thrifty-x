# RTL-SDR vs Airspy R2 RF-equivalence qualification

## 1. Purpose

This protocol qualifies the Thrifty-X receiver path for the practical claim:

> With the same RF stimulus and a controlled receiver configuration, RTL-SDR
> and Airspy R2 measurements are internally linear and reproducible, and
> Thrifty-X can transform their device-relative outputs into an equivalent
> input-referred measurement without hiding real hardware sensitivity
> differences.

The qualification is designed around the actual bench:

- Agilent/Keysight N9310A CW generator.
- AIS filtered preamplifier near 162 MHz, labelled 5 V / 50 mA,
  gain >20 dB.
- Nooelec NESDR SMArTee-family RTL-SDR, RTL2832U + R820T/R820T2.
- Airspy R2, serial 0x637862DC2E602DD7.
- Common RF centre 161.300 MHz.
- Receiver-side bias power for the inline preamplifier.
- RTL deployment setting: manual tuner gain 0 dB.
- Airspy deployment match at the R820T gain-code level: LNA/Mixer/VGA
  0/0/8.
- Airspy rates: 2.5 and 10 MSPS.

The original Thrifty implementation detects the carrier from the FFT peak
relative to an estimated noise power. This qualification therefore records
both absolute device-relative carrier level and scale-free/noise-referenced
figures rather than judging receiver equivalence from raw magnitude alone.

## 2. What “the same measurement” means

Raw dBFS is **not** required to be numerically equal across receiver types.
The two digitizers use different ADC/DDC paths and different definitions of
digital full scale. Existing controlled measurements show that the same RF
input is approximately 21 dB lower in Airspy device-relative dBFS than in
RTL-SDR device-relative dBFS, while the offset is nearly constant over input
power. That is a calibration offset, not automatically a sensitivity loss.

The primary cross-device quantities are therefore:

1. **Linearity:** a +5 dB generator step should produce approximately +5 dB
   carrier change within each receiver configuration.
2. **Input-referred calibrated level:** after fitting a receiver-specific
   transfer function on calibration levels, held-out RF levels should be
   reconstructed consistently.
3. **C/N0:** carrier-to-noise-density ratio, which is invariant to a pure
   digital scale factor. A remaining C/N0 difference is a real receiver/noise
   path difference and must not be calibrated away.
4. **Frequency estimate:** tone frequency/error should agree within the
   repeatability of the shared generator/reference setup.
5. **Detection outcome:** above the clean operating threshold, both paths
   should detect the same controlled CW. Near threshold, detection
   probability is reported rather than forced to match.
6. **Streaming integrity:** dropped samples, near-full-scale events and
   broadband/RMS bursts invalidate a row for RF equivalence fitting.
7. **Tuner gain-code equivalence:** matched R820T gain fields are compared
   across several manual gain points, not only the deployment point.

A publication should report both the uncalibrated dBFS difference and the
calibrated input-referred agreement. Hiding the device-relative dBFS offset
would make the result less reproducible, not more.

## 3. Current evidence motivating the protocol

The current bench has already established:

- RTL manual gain 0 uses R820T LNA index 0, mixer index 0, and fixed VGA
  code 8.
- Airspy 0/0/8 therefore matches the three R820T gain codes.
- This does **not** make all tuner registers identical: IF/filter and other
  signal-path bits differ between the librtlsdr and Airspy firmware paths.
- Airspy carrier dBFS is nearly unchanged between 2.5 and 10 MSPS at a
  fixed RF input, while its noise density differs by about 6-7 dB.
- A repeated three-condition stream diagnostic found the 2.5 MSPS condition
  clean, 10 MSPS with register reads clean in the reproduced run, and rare
  10 MSPS transport anomalies even when register reads were disabled.
  Register reads are therefore not established as the root cause.
- Clean 2.5 and 10 MSPS points show approximately 1 dB/dB carrier response
  to generator level.

These observations are hypotheses/previous measurements to reproduce, not
assumptions to bake into acceptance.

## 4. Fixed physical setup

Keep the following unchanged for the RTL and R2 runs:

    N9310A RF OUT
        |
        | same cable/adapters
        v
    AIS filtered preamplifier
        |
        | same cable/adapters
        v
    receiver under test

Only the receiver is swapped.

The amplifier is powered from the receiver side:

- NESDR SMArTee hardware bias output on the RTL run.
- Airspy R2 software bias tee ON on the R2 run.

Do not move the generator-side cable, amplifier, attenuators or adapters
between receivers. If an adapter must change, record it and measure its
loss separately.

For absolute receiver-input dBm, the preamplifier gain and cable/adapter
loss must be measured with calibrated RF instrumentation. Until then,
generator output dBm is the traceable controlled stimulus and
receiver-input dBm is nominal metadata only.

## 5. Independent and controlled variables

### Fixed

- RF centre: 161.300 MHz.
- Manual gain only; all AGC disabled.
- External preamplifier physically identical between receivers.
- N9310A and RF path unchanged.
- Capture duration and generator settle time identical.
- Airspy packing ON for the deployment-representative test.
- Airspy bias tee ON.

### Primary swept factors

- Generator power: -120 to -60 dBm in **5 dB steps**.
- Tone offset from centre: -30, -15, +15, +30 kHz.
- Sweep direction: ascending and descending, alternated by repetition.
- Independent repetition: default 3; use at least 5 for a final
  publication-quality dataset.
- Receiver/rate:
  - RTL: 2.4 MSPS.
  - R2: 2.5 MSPS.
  - R2: 10 MSPS.

The 5 dB grid is preferred over 10 dB for the qualification because it gives
enough points to detect slope curvature and local deviations without making
the run unnecessarily long. A **2.5 dB follow-up** is recommended only
around a detected transition (sensitivity knee, detection threshold or
compression knee).

The tone offsets stay below the benchmark's 50-300 kHz noise-density band
and avoid the centre/DC region.

## 6. Phase structure

### Phase A — deployment-setting transfer function

Purpose: direct qualification of the intended operating settings.

RTL:

- 2.4 MSPS
- manual gain 0 dB only

R2:

- 2.5 MSPS, 0/0/8
- 10 MSPS, 0/0/8

For every rate and tone offset:

1. RF OFF baseline.
2. Sweep -120 ... -60 dBm in 5 dB steps.
3. RF OFF baseline.
4. Repeat with the opposite power direction.
5. Repeat independently.

Outputs per point:

- carrier_dbfs
- noise_dbfs_hz
- local_noise_dbfs_hz
- cn0_dbhz
- detected
- tone_offset_hz / freq_error_ppm
- rms_dbfs
- near_fs_frac
- dropped samples
- segment count
- R820T register dump where available

### Phase B — R820T gain-code mapping

Purpose: validate that Thrifty-X's RTL-to-Airspy gain-code mapping is correct
at more than one point.

Selected RTL manual gains and corresponding Airspy gain fields:

| RTL requested gain | RTL supported gain | Airspy equivalent LNA/Mixer/VGA |
|---:|---:|---:|
| 0 dB | 0.0 dB | 0/0/8 |
| 12.5 dB | 12.5 dB | 4/3/8 |
| 20.7 dB | 20.7 dB | 6/6/8 |
| 29.7 dB | 29.7 dB | 8/8/8 |
| 40.2 dB | 40.2 dB | 11/11/8 |

This mapping follows the same gain-step algorithm represented in
`thriftyx/hal/r820t.py`. The primary experiment still uses gain 0;
the higher points are a validation of the mapping and signal path.

Use a conservative RF grid for this phase:

    off, -115, -110, -105, -100, -95, off dBm

This avoids unnecessary high-level drive at large tuner gain. Any point with
near-full-scale samples or clear compression is excluded and reported.

For each matched pair compare:

- response slope
- carrier dBFS offset versus RTL
- C/N0 difference
- noise-density difference
- frequency estimate
- register model / Airspy real register dump

Do **not** claim full-register identity. Equal LNA/Mixer/VGA fields prove
only gain-code identity; filter, IF and other signal-path registers remain
driver-specific.

### Phase C — Airspy stage sensitivity

Purpose: explain why a particular R2 setting matches or differs from RTL and
separate ADC-side noise from tuner-stage noise.

At one reference tone (+15 kHz), measure RF OFF and two low/moderate RF
levels for one-factor-at-a-time stage changes.

VGA sweep:

    0/0/0, 0/0/4, 0/0/8, 0/0/12, 0/0/15

LNA sweep:

    0/0/8, 4/0/8, 8/0/8, 12/0/8, 14/0/8

Mixer sweep:

    0/0/8, 0/4/8, 0/8/8, 0/12/8, 0/15/8

Run at both 2.5 and 10 MSPS. The default RF levels are:

    off, -105, -95, off dBm

This phase is diagnostic and is not used to redefine the primary
deployment match.

### Phase D — optional fine threshold sweep

After Phase A, identify the range in which detection probability changes
rapidly. Re-run only that interval in 2.5 dB steps with at least 20
independent captures per level if detection probability is a publication
endpoint.

CW detection probability in this bench is not identical to full
OOK/Gold-code Thrifty detection performance. A separate modulated-signal
experiment is required for that claim.

## 7. Randomisation, repetition and independence

- Alternate ascending/descending power order on successive repetitions.
- Rotate tone-offset order between repetitions.
- Open a new receiver session for each independent sweep file.
- Keep the physical RF chain untouched during all repetitions for one
  receiver.
- Run the RTL and R2 experiments in the same session if possible.
- For the final paper, repeat the complete receiver-swap experiment on
  multiple days and, ideally, with more than one unit of each receiver type.

The existing R2-A/R2-B work is useful for between-unit variation; final
publication claims should not be based on one unit if the claim is about the
receiver family rather than one implementation.

## 8. Data-quality exclusion rules

A measurement row is not used in transfer-function fitting when any of the
following is true:

- dropped > 0
- near_fs_frac > 0
- carrier not detected at a level intended for linearity fitting
- obvious broadband/RMS burst relative to the receiver's clean baseline
- generator readback does not equal the requested level/frequency
- hardware configuration changed unexpectedly

Excluded rows remain in the raw dataset and are reported. They are never
silently deleted.

RF-OFF rows are used for noise/spur/stream health, not for carrier linearity.

## 9. Statistical analysis

### 9.1 Within-configuration linearity

Fit for each receiver/rate/gain/tone:

    carrier_dBFS = a + b * P_generator_dBm

Report:

- slope b
- intercept a
- R^2
- residual RMS
- maximum absolute residual
- 95% confidence interval of b
- ascending vs descending difference

A nominally linear receiver should have b close to 1 dB/dB.

### 9.2 Raw cross-device offset

At matched RF levels calculate:

    Delta_carrier = carrier_R2_dBFS - carrier_RTL_dBFS

Report mean, standard deviation, range, and dependence on input level and
tone offset. A constant offset supports a calibration-factor interpretation;
a level-dependent offset indicates nonlinearity or configuration mismatch.

### 9.3 C/N0 equivalence

Calculate:

    Delta_C/N0 = C/N0_R2 - C/N0_RTL

Do not force this value to zero. It measures real receiver/noise-path
performance after cancellation of an arbitrary pure scale factor.

Report 2.5 and 10 MSPS separately.

### 9.4 Held-out input-referred calibration

To test the statement “the same input is measured the same” without circular
calibration:

1. Sort the generator levels.
2. Use alternating levels as calibration points.
3. Fit carrier dBFS -> generator dBm for each receiver configuration.
4. Predict the held-out levels.
5. Report MAE, bias, RMSE and maximum absolute error.
6. Compare the independently reconstructed input levels between RTL and R2.

This yields an input-referred measurement even though raw dBFS is not shared.

A calibration derived and tested on the same points is not accepted as an
equivalence demonstration.

### 9.5 Agreement analysis for a paper

For a final dataset also report:

- Bland-Altman bias and 95% limits of agreement for calibrated input level.
- Repeatability standard deviation.
- Between-day and between-unit components when available.
- Bootstrap confidence intervals or a mixed-effects model if unit/day are
  included as random effects.
- A documented uncertainty budget.

## 10. Provisional engineering acceptance criteria

These are project qualification criteria, not claimed RF standards:

- linearity slope: 0.98 to 1.02 dB/dB over the accepted linear range
- residual RMS: <= 0.5 dB
- held-out input-referred bias: |mean| <= 0.5 dB
- held-out input-referred MAE: <= 0.5 dB
- 95% of held-out errors: <= 1.0 dB absolute
- raw R2-vs-RTL carrier offset: standard deviation <= 0.5 dB within one
  matched configuration
- 10 MSPS matched-setting C/N0 difference: report and target <= 2 dB based
  on current bench evidence; do not impose this target on 2.5 MSPS until its
  rate-dependent noise mechanism is understood
- accepted linearity rows: dropped = 0 and near_fs_frac = 0
- frequency estimate: no receiver-dependent systematic shift larger than
  repeatability/measurement uncertainty

If the data justify tighter limits, tighten them after the qualification;
do not choose criteria after seeing a desired outcome.

## 11. Uncertainty and traceability

For publication, retain:

- N9310A model, serial, firmware, calibration date/certificate and specified
  amplitude/frequency uncertainty.
- preamplifier model/serial, supply method and measured gain-vs-frequency.
- cable/adapter identity and measured insertion loss.
- receiver serials and firmware/library versions.
- USB topology and WSL/usbipd versions.
- Thrifty-X git commit.
- room temperature and warm-up duration.
- exact raw CSV/log files and analysis script version.

Because the preamplifier is common to both receiver paths, its fixed gain
largely cancels in a paired cross-device comparison. It does not cancel from
an absolute receiver-input-power claim.

## 12. Script workflow

Use one shared RUN name. Run RTL first, swap only the receiver, then run R2.

    RUN=equiv_YYYYMMDD_01 bash scripts/bench/run_rtl_equivalence_validation.sh

Swap RTL -> R2 only, leaving the generator, preamp and common cable fixed:

    RUN=equiv_YYYYMMDD_01 bash scripts/bench/run_r2_equivalence_validation.sh

Then:

    python scripts/bench/compare_receiver_equivalence.py bench/equiv_YYYYMMDD_01

The runners write independent raw CSV/log files under:

    bench/<RUN>/rtl/
    bench/<RUN>/r2/

and the comparison script writes:

    bench/<RUN>/comparison.md
    bench/<RUN>/comparison_points.csv
    bench/<RUN>/calibration.json

## 13. Interpretation rules

A successful qualification is **not** “RTL dBFS equals R2 dBFS.”

A successful qualification shows that:

- each receiver is linear and repeatable;
- the raw device-relative dBFS difference is stable and explainable as a
  transfer offset;
- a calibration obtained from independent calibration levels predicts
  held-out known inputs without receiver-dependent bias;
- C/N0 and detection differences are separately quantified as receiver
  performance, not hidden by scale calibration;
- the R820T gain-code mapping is reproducible across multiple gain points;
- the intended RTL gain-0 / R2 0/0/8 deployment pair is explicitly
  qualified at 2.4, 2.5 and 10 MSPS paths;
- transport anomalies are identified and excluded with full traceability.

This distinction is essential if the results will later support a
peer-reviewed hardware/software validation claim.
