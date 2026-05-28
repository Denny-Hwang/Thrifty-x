# sub_offset bound violation investigation

## Scope
Investigated Prompt 1 only, on branch `verify/sub-offset-bounds`.

## 1) Carrier `sub_offset` algorithm and code path

- Carrier offset is computed by `DefaultSynchronizer` via a Dirichlet-kernel curve fit interpolator created by `make_dirichlet_interpolator(...)`. The interpolator is wired into the synchronizer in `DefaultSynchronizer.__init__`. The offset stored in `CarrierSyncInfo.offset` is the raw `fit_offset` returned by `scipy.optimize.curve_fit(...)`. No constraints are passed to `curve_fit` and no post-fit clipping is applied in this function.
- Relevant code path:
  - `thrifty/carrier_sync.py:110` creates the Dirichlet interpolator for default carrier sync.
  - `thrifty/carrier_sync.py:184-194` fits and returns `fit_offset`.
  - `thrifty/carrier_sync.py:189` calls `curve_fit(..., p0=initial_guess)` with no `bounds=`.
  - `thrifty/carrier_sync.py:63-74` stores this offset into `CarrierSyncInfo`.

Conclusion: **Carrier sub_offset is Dirichlet-kernel least-squares fit, unconstrained**.

## 2) Bound enforcement status for carrier offset

- No `np.clip`, `min`, `max`, or explicit range check is applied on `fit_offset` in carrier interpolation path.
- The returned value is used directly for frequency shifting and serialization.
- Evidence:
  - `thrifty/carrier_sync.py:184-194` (return raw `fit_offset`).
  - `thrifty/carrier_sync.py:69-74` (offset propagated to `CarrierSyncInfo`).

Conclusion: **Not enforced**.

## 3) Correlation `sub_offset` algorithm and code path

- Correlation offset is computed in `SoaEstimator.soa_estimate(...)` by calling `self.interpolate(...)`, defaulting to `gaussian_interpolation`.
- Supported interpolation methods are at least parabolic and gaussian (`parabolic_interpolation`, `gaussian_interpolation`).
- After interpolation, `offset = _clip_offset(offset)` is always applied in SoA estimation path.
- Evidence:
  - `thrifty/soa_estimator.py:74` sets default interpolation to gaussian.
  - `thrifty/soa_estimator.py:88-90` computes then clips offset before storing `CorrDetectionInfo.offset`.
  - `thrifty/soa_estimator.py:146-170` parabolic and gaussian formulas.
  - `thrifty/soa_estimator.py:16-17` `_clip_offset(offset, max_=0.6)`.

Conclusion: **Correlation sub_offset is interpolation + explicit clipping (currently ±0.6, not ±0.5)**.

## 4) Reproduction test and result

Added `tests/unit/test_sub_offset_bounds.py` with two synthetic cases:
1. Half-bin tone (`peak_idx + 0.5`) expected to remain within `[-0.5, 0.5]`.
2. Dual-bin straddle case (`peak_idx + 0.845`) mimicking TX1 split around bins 101/102, expected to reproduce an out-of-bound fit result.

Command run:

```bash
pytest -q tests/unit/test_sub_offset_bounds.py
```

Observed result:
- Case 1 passed (in-bound half-bin behavior).
- Case 2 passed by reproducing out-of-bound result `0.8449999999999926` (> 0.5).

This reproduces the spec-violating behavior in a controlled synthetic setup.

## 5) Dirichlet fit-specific findings

- Dirichlet interpolator uses unconstrained `curve_fit` with only an initial guess `(fft_mag[peak_idx], 0)`.
- Because no `bounds` are specified and no post-fit clipping is applied, least-squares can converge to `|offset| > 0.5` in adjacent-bin split cases.
- Evidence: `thrifty/carrier_sync.py:188-190`.

## 6) Data-flow trace: what table column likely contains 0.845

- Detection serialization includes both `corr_info.offset` (`po`) and `carrier_info.offset` (`co`).
  - `thrifty/toads_data.py:51-55`.
- Structured arrays expose these as `offset` (corr) and `carrier_offset` (carrier).
  - `thrifty/toads_data.py:139-142`.
- Stats utility prints both separately, including min/max.
  - carrier: `thrifty/toads_analysis.py:56-59`
  - corr: `thrifty/toads_analysis.py:73-76`

Given the observed value (`0.845`) and reproduced carrier interpolator output, the per-bin `sub_offset` outlier is consistent with the **carrier offset field** (`carrier_offset` / `co`), not the clipped correlation offset path.

## Hypothesis for TX1 vs TX2 behavior

- TX1 spread across adjacent bins (101/102) creates a bimodal local FFT neighborhood where unconstrained Dirichlet fitting can place the optimum beyond ±0.5.
- TX2 concentrated primarily in one bin (128) keeps local fit well-conditioned near the primary lobe center, resulting in offsets that remain within nominal sub-bin bounds.
- Evidence chain:
  1. Carrier interpolation is unconstrained (`curve_fit` without bounds).
  2. Synthetic adjacent-bin straddle reproduces `~0.845`.
  3. Correlation offsets are clipped and therefore unlikely to exceed stated bounds.

## Recommended fix (not implemented in this PR)

Per stop condition, no fix is implemented in this PR. A follow-up fix PR can choose one of:

1. **Bounded fit** (preferred): pass `bounds=([0, -0.5], [np.inf, 0.5])` (or symmetric amplitude bounds as needed) to `curve_fit`.
2. **Post-fit clipping**: `fit_offset = np.clip(fit_offset, -0.5, 0.5)` before returning.

Illustrative diff snippet:

```diff
--- a/thrifty/carrier_sync.py
+++ b/thrifty/carrier_sync.py
@@
-        popt, _ = curve_fit(_fit_model, xdata, ydata, p0=initial_guess)
+        popt, _ = curve_fit(
+            _fit_model,
+            xdata,
+            ydata,
+            p0=initial_guess,
+            bounds=([0.0, -0.5], [np.inf, 0.5]),
+        )
         amplitude, fit_offset = popt
```

## Stop condition outcome

- The unit reproducer confirms `>0.5` offset in the dual-bin case.
- Investigation stops here per instruction; no implementation fix included.
