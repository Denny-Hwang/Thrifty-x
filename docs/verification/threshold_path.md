# Detector threshold computation path

**Status:** Threshold formula path is **device-agnostic**. The same
`15*snr` expression produces the same multiplier of the measured
`noise_rms` for every device. The asymmetric carrier-to-correlation
conversion rates (RTL 12.5% loss vs R2 17.5% loss) reported in the
field test cannot be explained by the threshold path.

---

## 1. Setting → coefficients

| Step | File | Lines |
|---|---|---|
| Parse `corr_threshold: 15 * snr` from .cfg | `thrifty/setting_parsers.py` / `thriftyx/setting_parsers.py` | `141-185` |
| Store as `(constant, snr, stddev)` tuple | same | `185` (returns `(0.0, 15.0, 0.0)` for `"15*snr"`) |
| Pass into `DetectorSettings.corr_thresh` | `thriftyx/detect.py` | `27-37, 61` |
| Hand to `SoaEstimator.__init__(thresh_coeffs=...)` | `thriftyx/detect.py` | `59-64` |

Same flow for `carrier_threshold` → `DefaultSynchronizer(thresh_coeffs=...)`.

The setting parser is identical across devices; the parser does not
inspect `device_type`, `sample_rate`, or any other context.

## 2. Coefficients → absolute threshold

### Carrier path

```python
# thrifty/carrier_detect.py:110-115  (thriftyx/carrier_detect.py:111-116 identical)
def _calculate_threshold(fft_mag, thresh_coeffs, noise_rms):
    thresh_const, thresh_snr, thresh_stddev = thresh_coeffs
    stddev = np.std(fft_mag) if thresh_stddev else 0
    thresh = (thresh_const + thresh_snr * noise_rms**2
              + thresh_stddev * stddev**2)
    return np.sqrt(thresh)
```

For `15*snr`: `thresh_const=0`, `thresh_snr=15`, `thresh_stddev=0`,
so `threshold = sqrt(15 * noise_rms^2) = sqrt(15) * noise_rms
≈ 3.873 * noise_rms`.

### Correlation path

```python
# thrifty/soa_estimator.py:127-134  (thriftyx/soa_estimator.py:128-135 identical)
def calculate_threshold(corr, noise_rms, thresh_coeffs):
    thresh_const, thresh_snr, thresh_stddev = thresh_coeffs
    stddev = np.std(corr.mag) if thresh_stddev else 0
    thresh = (thresh_const +
              thresh_snr * noise_rms**2 +
              thresh_stddev * stddev**2)
    return np.sqrt(thresh)
```

Same formula. For `15*snr`: same `sqrt(15) * noise_rms` multiplier.

### noise_rms

| Path | File | Lines | Source of noise_rms |
|---|---|---|---|
| Carrier | `thrifty/carrier_detect.py` | `99-107` | `sqrt((fft_energy - 2*peak_power) / (N-1))` |
| Correlation | `thrifty/soa_estimator.py` | `108-120` | `sqrt((signal_corr_energy - peak_power) / N)` |

Both estimators compute noise from the signal energy minus the peak.
Neither references device type, sample rate, or bit depth. The result
is purely a function of the per-block FFT and the detected peak.

## 3. Device-specific branching check

```
$ grep -rn "device_type\|airspy\|rtl_sdr\|rtl-sdr" \
    thriftyx/detect.py thriftyx/soa_estimator.py \
    thriftyx/carrier_detect.py thriftyx/carrier_sync.py
(no matches)
```

Confirmed: no device-specific code anywhere in the detection or
threshold evaluation path. The threshold pipeline `cfg → parser →
namedtuple → DetectorSettings → calculate_threshold` is identical
for RTL-SDR and Airspy.

## 4. Default settings comparison

All three example configs use the same `15*snr` for both thresholds:

| Config | `carrier_threshold` | `corr_threshold` |
|---|---|---|
| `example/detector.cfg` (RTL-SDR) | `15 * snr` | `15 * snr` |
| `example/detector_mini.cfg` (Airspy Mini) | `15 * snr` | `15 * snr` |
| `example/detector_r2.cfg` (Airspy R2) | `15 * snr` | `15 * snr` |

The default is uniform across devices.

## 5. What could explain the 12.5% / 17.5% conversion gap?

If the threshold path is identical for both devices, then the
asymmetric carrier-to-correlation conversion rate must come from
*signal* differences, not threshold differences. Candidates worth
investigating in separate work:

1. **Template-signal mismatch**: R2 records at 10 Msps (different
   chip timing than the 2.4 Msps RTL template). If the template
   used for both is RTL-trained, the R2 correlation peak's
   SNR-relative magnitude will be lower than RTL's, depressing
   `peak_mag / threshold` and rejecting more candidates. This is
   `template.npy` selection at the deployment level - not a code
   bug, but a calibration concern. See Prompt 5 for the multi-
   template design proposal.
2. **Carrier sub_offset bug** (PR #39): if R2's carrier-frequency
   estimate is biased outside `[-0.5, 0.5]` (max observed 0.845),
   the frequency-domain shift applied in `Synchronizer.sync` is
   wrong. A wrong shift smears the correlation peak across bins,
   so peak_mag drops and the carrier-to-corr conversion fails more
   often. **Verify PR #39's fix first before drawing any
   conclusions on the threshold front.**
3. **TX1 dual-bin pattern** alone: when R2 fans out energy across
   bins 101 and 102, even a perfect shift leaves residual energy
   in the wrong bin, depressing peak_mag relative to noise.

## 6. Decision

| Question | Answer |
|---|---|
| Does the threshold expression differ across devices? | No - identical code path, identical default. |
| Could the *evaluation* differ across devices? | No - no device-aware code anywhere in the path. |
| Could a lower default recover R2 detections? | Potentially - but treating the symptom; the *cause* is likely template mismatch or sub_offset bias. |
| Should we change `settings.py` defaults to be device-dependent? | **No.** The right place for site-specific tuning is the .cfg file, not the source code default. |
| Should the user_guide document recommended thresholds per device? | **Yes** (documentation-only follow-up). |

## 7. Follow-up

1. **Wait for PR #39 (sub_offset) resolution** before re-analysing the
   conversion gap. The 0.845 carrier offset is a confounder.
2. Run the threshold sweep
   (`docs/verification/threshold_sweep.md`) on the same .card files
   used for the original field test once the sub_offset fix is in.
3. Document per-device recommended thresholds in
   `docs/user_guide.md` and `_ko.md` only if the sweep shows
   a consistent device-relative pattern that survives the
   sub_offset fix.
4. **Do not** touch `settings.py` defaults - the config file is
   the right surface for this kind of tuning.
