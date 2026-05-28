# sub_offset bound re-verification (post-fix)

**Status:** Re-verification of the carrier/correlation sub-sample offset
bounds on current `master`. The original investigation
(`docs/verification/sub_offset_investigation.md`) and the fix are
**already merged** (PR #39 reproducer, PR #44 fix). This document
confirms the bound is now enforced at the current line numbers and adds
the correlation-peak straddle coverage (prompt Case C) that the original
reproducer did not include.

No code fix here — the fix is in master. This is a confirming re-check
plus added test coverage.

---

## 1. Carrier sub_offset — bounded

`CarrierSyncInfo.offset` comes from the Dirichlet-kernel interpolator in
`make_dirichlet_interpolator` (`thriftyx/carrier_sync.py:163`). The
`curve_fit` call is now bounded:

```python
# thriftyx/carrier_sync.py:209-210
popt, _ = curve_fit(_fit_model, xdata, ydata, p0=initial_guess,
                    bounds=([0.0, -0.5], [np.inf, 0.5]))
```

- amplitude lower bound `0.0` (no negative-amplitude fits),
- offset bound `[-0.5, 0.5]` per Krueger Section 4.4.2.

The returned offset flows unmodified into `CarrierSyncInfo.offset`
(`carrier_sync.py:78-79`), so the value written to the `carrier_offset`
column of `.toad(s)` is now guaranteed in `[-0.5, 0.5]`.

## 2. Correlation sub_offset — bounded

`CorrDetectionInfo.offset` comes from `parabolic_interpolation`
(`thriftyx/soa_estimator.py:174`) or `gaussian_interpolation`
(`:187`), then clipped:

```python
# thriftyx/soa_estimator.py:107-108
offset = 0 if not detected else self.interpolate(corr.mag, peak_idx)
offset = _clip_offset(offset)            # _clip_offset: line 20, max_=0.6
```

For a valid peak (`b >= a, c`) the 3-point parabolic/Gaussian estimate
is analytically bounded to `[-0.5, 0.5]`; `_clip_offset` (`max_=0.6`) is
a safety net for degenerate triplets. Note the documented asymmetry:
carrier clips at `+-0.5` (via curve_fit bounds), correlation at `+-0.6`.

## 3. Which column produced the field 0.845

Per the original investigation: `carrier_offset` (not `corr_offset`).
It was the unbounded carrier Dirichlet fit. With the bounds= fix in
place, the synthetic reproducer that previously reached
`max |offset| ~ 1.07` now stays within `[-0.5, 0.5]`.

## 4. Tests (current `master` + this branch)

`tests/unit/test_sub_offset_bounds.py`:

| Test | Case | Asserts |
|---|---|---|
| `test_clean_carrier_offset_within_bounds` | A (clean CW, between bins) | offset in [-0.5, 0.5] and recovers truth |
| `test_dual_bin_equal_peaks_stays_at_boundary` | B (TX1 split, no noise) | offset in (-0.5, 0.5) |
| `test_noisy_carrier_stays_within_half_bin_bound` | A + noise | every trial in [-0.5, 0.5] (was ~1.07 pre-fix) |
| `test_dual_bin_plus_noise_stays_within_bound` | B + noise | every trial in [-0.5, 0.5] |
| `test_correlation_offset_is_clipped` | C (clip net) | `_clip_offset` bounds to +-0.6 |
| **`test_correlation_straddle_within_bound`** (added) | **C (real interpolators)** | parabolic & Gaussian on a Gaussian straddle stay in [-0.5, 0.5]; Gaussian is exact |

```
$ python -m pytest tests/unit/test_sub_offset_bounds.py -q
26 passed
```

## 5. Paper relevance

The violating column was the **carrier** offset, which feeds the
frequency-shift step (`carrier_sync.sync` → `freq_shift_integer`).
With the default integer shift, a pre-fix offset > 0.5 made
`round(peak_idx + offset)` select the wrong bin, leaving up to ~0.7-bin
of residual carrier during correlation and smearing the SoA peak on
exactly the R2 TX1 detections where the bug manifested. The fix removes
that confound, so the "R2 SoA residual vs RTL SoA residual" comparison
in the manuscript is now meaningful. Re-run the 5/14 field analysis on
fixed `master` and compare the TX1 carrier-offset distribution before
vs after.
