# Threshold sweep results (template)

**Status:** template / instructions only. Actual sweep results need
.card files from the original 5/14 field test, which are not present
in this repository. Once the captures are restored, follow §1 to
populate the tables.

The path investigation in
`docs/verification/threshold_path.md` showed that the threshold
formula is device-agnostic; this sweep is the empirical confirmation,
and exists so that a future maintainer can rerun the comparison
deterministically.

---

## 1. How to run the sweep

```bash
# Pick one R2 .card and one RTL .card from the same TX-illumination
# session. The two .cards should be paired (same minute / same
# transmitter) so that the comparison is apples-to-apples.

R2_CARD=path/to/r2_capture.card
RTL_CARD=path/to/rtl_capture.card

mkdir -p sweep_out
for THRESH in "8*snr" "10*snr" "12*snr" "15*snr" "18*snr"; do
    for CARD in "$R2_CARD" "$RTL_CARD"; do
        NAME=$(basename "$CARD" .card)
        thrifty detect "$CARD" \
            --corr-threshold "$THRESH" \
            -o "sweep_out/${NAME}_${THRESH//\*/x}.toad" \
            --quiet 2> "sweep_out/${NAME}_${THRESH//\*/x}.log"
        COUNT=$(grep -c "" "sweep_out/${NAME}_${THRESH//\*/x}.toad")
        echo "$NAME  $THRESH  $COUNT"
    done
done
```

Then transcribe counts into the table in §2.

## 2. Sweep results

Fill in once the sweep above has been run. The shape of the table is
the contract this PR establishes.

### R2 .card (gain=...; document the gain stages and date here)

| `corr_threshold` | Detections (out of N carrier hits) | Notes |
|---|---|---|
| `8*snr` |  |  |
| `10*snr` |  |  |
| `12*snr` |  |  |
| `15*snr` (default) |  |  |
| `18*snr` |  |  |

### RTL .card (gain=...; document the gain stages and date here)

| `corr_threshold` | Detections (out of N carrier hits) | Notes |
|---|---|---|
| `8*snr` |  |  |
| `10*snr` |  |  |
| `12*snr` |  |  |
| `15*snr` (default) |  |  |
| `18*snr` |  |  |

## 3. Per-block debug logging (optional, for deeper analysis)

If the sweep results above don't disambiguate the cause of the
12.5% / 17.5% gap, add the following one-shot debug print to
`thriftyx/soa_estimator.py:SoaEstimator.soa_estimate` and re-run on
the first 10 carrier-detected blocks:

```python
# Insert after line 84 (`threshold = self.calculate_threshold(...)`)
import logging
if logging.getLogger().level <= logging.DEBUG:
    logging.debug(
        "device=%s  carrier_noise_rms=%.4f  carrier_thresh=%.4f  "
        "corr_noise_rms=%.4f  corr_thresh=%.4f  peak_mag=%.4f  detected=%s",
        getattr(self, '_device_type', 'unknown'),
        getattr(self, '_carrier_noise_rms', float('nan')),
        getattr(self, '_carrier_thresh', float('nan')),
        noise_rms, threshold, peak_mag, peak_mag > threshold)
```

(Device-type tagging requires plumbing the cfg's `device_type` into
the detector - that's a separate small refactor. For now the comparison
can be done by running R2 and RTL detections back-to-back and
filtering on filename.)

Use `--log-level DEBUG` or the appropriate `thriftyx` flag to enable
the output.

## 4. Decision matrix (to be completed after the sweep)

Once §2 is populated, fill this in:

- [ ] The detection plateau for R2 is reached at `corr_threshold = ___`.
- [ ] The detection plateau for RTL is reached at `corr_threshold = ___`.
- [ ] **If the plateaus are the same threshold**, the default `15*snr`
      is fine and the user_guide does not need a device-specific note.
- [ ] **If R2 plateaus at a lower threshold than RTL**, document the
      recommended R2 threshold in `docs/user_guide.md`
      (no code change). Keep the default at `15*snr`.
- [ ] **If R2 plateaus at a HIGHER threshold than RTL** (unlikely given
      R2's higher SNR), reopen this investigation - that result would
      contradict the threshold-path analysis and suggest a per-device
      bug elsewhere.

## 5. Linked artefacts

- `docs/verification/threshold_path.md` - code path proof.
- `docs/verification/sub_offset_investigation.md` (PR #39) - the
  sub_offset bound issue that may be confounding the R2 detection
  count. Wait for that fix before drawing conclusions from this sweep.
