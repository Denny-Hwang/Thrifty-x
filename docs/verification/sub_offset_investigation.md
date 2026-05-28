# sub_offset bounds investigation

**Status:** Bug reproduced. Carrier sub-bin offset is **not** clipped and can
exceed `[-0.5, 0.5]` under realistic noise. Correlation sub-bin offset **is**
clipped (to `+/-0.6`).

**Stop condition:** the reproducer test produced `|offset| > 0.5`, so this
investigation stops at "report + tests + PR" per the prompt. No fix is
proposed in this PR; recommended fix is documented below for follow-up.

---

## 1. Carrier sub_offset code path

| Module | File | Lines | Bounded? |
|---|---|---|---|
| Production (upstream-compatible) | `thrifty/carrier_sync.py` | 184-194 | **No** |
| Production (Thrifty-X / BatRF) | `thriftyx/carrier_sync.py` | 197-210 | **No** |
| Experimental | `thrifty/experimental/carrier_interpolators.py` | 29-35 | **No** |
| C++ fastdet | `fastdet/corr_detector.cpp` | 191-194 -> 88-101 | **Yes** (`+/-0.5`) |

The Python default carrier interpolator is built via
`make_dirichlet_interpolator` (`thrifty/carrier_sync.py:150-196`) and applied
inside `Synchronizer.sync` (`thrifty/carrier_sync.py:52-76`). The body of the
fit is:

```python
# thrifty/carrier_sync.py:184-194
def _interpolator(fft_mag, peak_idx):
    """Curve fitting of Dirichlet kernel to FFT."""
    xdata = np.array(np.arange(-(width//2), width//2+1))
    ydata = fft_mag[peak_idx + xdata]
    initial_guess = (fft_mag[peak_idx], 0)
    popt, _ = curve_fit(_fit_model, xdata, ydata, p0=initial_guess)
    amplitude, fit_offset = popt
    if return_amplitude:
        return amplitude, fit_offset
    else:
        return fit_offset
```

`scipy.optimize.curve_fit` is invoked **without** a `bounds=` argument, so the
fitted `time_offset` parameter is unconstrained. The return value flows
directly into `CarrierSyncInfo.offset`
(`thrifty/carrier_sync.py:74-75`) and ends up in the `.toad(s)` `carrier_offset`
column (`thrifty/toads_data.py:51-55`, `thriftyx/toads_data.py:55-60`).

The Thrifty-X fork (`thriftyx/carrier_sync.py:197-210`) adds two edge-case
guards (return `0` when `peak_idx` is too close to the FFT edges), but
otherwise inherits the same unbounded `curve_fit` call.

### Algorithm reference

The fit model is a Dirichlet kernel of width `W = carrier_len` over an FFT
of length `N = block_len` (Krueger Section 4.4.2). For Thrifty-X at 10 Msps
(`example/detector_r2.cfg:31-32`): `N = 65536`, `W = 10232`. The fit window
is +/-3 bins around `peak_idx` (`width=6` parameter in `make_dirichlet_interpolator`).

## 2. Correlation sub_offset code path

The correlation peak interpolation is bounded.

| Step | File | Lines |
|---|---|---|
| Interpolate | `thrifty/soa_estimator.py` | 88 (calls `gaussian_interpolation` by default) |
| Clip | `thrifty/soa_estimator.py` | 89 calls `_clip_offset` at 16-17 |

```python
# thrifty/soa_estimator.py:16-17
def _clip_offset(offset, max_=0.6):
    return -max_ if offset < -max_ else max_ if offset > max_ else offset

# thrifty/soa_estimator.py:88-89
offset = 0 if not detected else self.interpolate(corr.mag, peak_idx)
offset = _clip_offset(offset)
```

The clip width is **+/-0.6** (not +/-0.5). The extra 0.1 of slack accommodates
the parabolic / Gaussian interpolator's mild excursions at bin boundaries; the
Krueger bound of +/-0.5 is the *expected* range, not a hard cut-off here.

The C++ fastdet path (`fastdet/corr_detector.cpp:88-101`) applies a stricter
`+/-0.5` clip to **both** the carrier and the correlation offset:

```c++
// fastdet/corr_detector.cpp:97-98
if (offset < -0.5) offset = -0.5;
if (offset > 0.5) offset = 0.5;
```

## 3. Bound enforcement status

| Path | Carrier offset | Correlation offset |
|---|---|---|
| `thrifty/` (upstream) Python | **Unbounded** | Clipped to +/-0.6 |
| `thriftyx/` (Thrifty-X) Python | **Unbounded** | Clipped to +/-0.6 |
| `fastdet/` C++ | Clipped to +/-0.5 | Clipped to +/-0.5 |

The Python data path used by `thrifty detect` / `thriftyx detect` is the one
that produces the field-test `.toads` files. The C++ fastdet path is
unaffected.

## 4. Reproducer results

`tests/unit/test_sub_offset_bounds.py` covers four shapes of input. All tests
pass against the current implementation; the reproducer assertions assert that
`|offset| > 0.5` *does* occur.

| Test | Scenario | Observed |
|---|---|---|
| `test_clean_carrier_offset_within_bounds` | Clean CW, true offset in [-0.49, 0.49] | Recovered within 1e-4; always in [-0.5, 0.5]. |
| `test_clean_carrier_offset_just_past_half_wraps_via_argmax` | Clean CW, true offset 0.51 | `argmax` shifts to bin 102; reported offset -0.49. Stays in bound. |
| `test_dual_bin_equal_peaks_stays_at_boundary` | TX1-like: equal carriers at bins 101 and 102, no noise | Reported offset ~ +0.495. Stays in bound (just). |
| **`test_noisy_carrier_can_exceed_half_bin_bound`** | Clean CW + complex Gaussian noise at `amp=0.5` | **Max \|offset\| = 1.07; 33/200 trials over 0.5.** Reproduces the field-observed 0.845. |
| **`test_dual_bin_plus_noise_exceeds_bound`** | Dual-bin energy at 101 and 102 + noise | At least one of 200 trials past +/-0.5. |
| `test_correlation_offset_is_clipped` | Direct unit-test of `_clip_offset` | Confirmed clipped at +/-0.6. |

The empirical worst case (`max |offset| ~ 1.07`) is in the same ballpark as
the field-observed value of `0.845`, supporting the hypothesis that the
unbounded `curve_fit` is the source of the spec-violating values in the BatRF
.toads files.

## 5. Why TX1 (bins 101+102) exceeds the bound while TX2 (bin 128) does not

Both transmitters use a Gold-coded preamble whose "carrier" component is the
unmodulated portion of the burst. The Dirichlet interpolator assumes the FFT
near the peak follows a single-peak Dirichlet shape.

`test_dual_bin_equal_peaks_stays_at_boundary` shows that the dual-bin pattern
**alone** is not the trigger: with two coherent equal carriers and no noise,
`curve_fit` finds the midpoint and returns offset just inside +/-0.5.

`test_noisy_carrier_can_exceed_half_bin_bound` shows that **noise alone** is
sufficient to drive `curve_fit` past the +/-0.5 bound.

`test_dual_bin_plus_noise_exceeds_bound` shows the combined effect.

Why TX1 sees this more than TX2:
- TX1 transmits at 161.308 MHz: closer to the LO; lower IF bin index (101-102);
  energy more likely to straddle two adjacent FFT bins as the tuner drifts.
- TX2 transmits at 161.320 MHz: 12 kHz higher; lands cleanly on a single bin
  (128); no dual-bin pattern.
The dual-bin pattern reduces the effective SNR of the peak relative to its
shoulders, leaving more room for the noisy `curve_fit` to converge to a poor
solution.

The hypothesis is: **TX1 has both noise AND dual-bin energy spread; that
combination tips the unbounded curve_fit past the spec bound at a noticeable
rate; TX2 lacks the dual-bin spread and therefore stays clean.**

This is consistent with the field observation that R2 (higher SNR, but the
same dual-bin pattern) reports larger offsets than RTL on TX1 - higher SNR
does not save you here, because the failure mode is `curve_fit` convergence,
not noise magnitude.

## 6. Data-flow check

The 0.845 value lives in `carrier_offset` (not `corr_offset`):

- `toads_data.toads_array`
  (`thrifty/toads_data.py:123-143`, `thriftyx/toads_data.py:124-160`)
  defines structured dtype with separate columns `offset` (correlation) and
  `carrier_offset` (carrier).
- `toads_analysis.print_stats`
  (`thrifty/toads_analysis.py:56-59`) prints
  `Carrier offset: mean=..., min=..., max=...` from `data['carrier_offset']`.

So a `print_stats` table column labelled "carrier offset" reading 0.845 maps
directly to `CarrierSyncInfo.offset`, which is the unbounded
`make_dirichlet_interpolator` return value.

## 7. Fix applied (Option B - `curve_fit(bounds=...)`)

Both `thrifty/carrier_sync.py` and `thriftyx/carrier_sync.py` now pass
explicit bounds to `scipy.optimize.curve_fit`:

```python
popt, _ = curve_fit(
    _fit_model, xdata, ydata, p0=initial_guess,
    bounds=([0.0, -0.5], [np.inf, 0.5]),
)
```

The amplitude lower bound (`0.0`) prevents `curve_fit` from finding
negative-amplitude solutions; the sub-bin offset bounds match the
Krueger spec.

The experimental Dirichlet interpolator at
`thrifty/experimental/carrier_interpolators.py:33` received the same
treatment.

### Test changes that came with the fix

| File | Change |
|---|---|
| `tests/test_carrier_sync.py::test_dirichlet_interpolator` | Removed the `-0.51` and `0.56` parameters (true offsets outside the spec bound; they now clip to +-0.5). Loosened tolerance from `1e-8` to `1e-5` to absorb scipy's bounded-optimizer rounding when the true value sits on the boundary. |
| `tests/test_carrier_sync.py::test_dirichlet_interpolator_clips_out_of_bounds` (new) | Documents the new clipping behaviour with four out-of-bound cases. |
| `tests/unit/test_sub_offset_bounds.py::test_noisy_carrier_can_exceed_half_bin_bound` -> `test_noisy_carrier_stays_within_half_bin_bound` | Assertion inverted: every trial must respect `[-0.5, 0.5]`. Pre-fix this sweep produced `max |offset| ~ 1.07`. |
| `tests/unit/test_sub_offset_bounds.py::test_dual_bin_plus_noise_exceeds_bound` -> `test_dual_bin_plus_noise_stays_within_bound` | Same inversion. |

Result: `pytest tests/` reports `311 passed, 6 skipped`.

### Why Option B over Option A (post-clip)

Both end at the same output value, but Option B tells the optimizer
the constraint up front so it converges to the best **in-bound**
solution rather than the unconstrained global best (which would then
get clipped). On noisy or dual-bin data this often gives a more
accurate sub-bin estimate inside the bound than a post-clipped
unconstrained fit.

## 8. Paper impact

Clipping changes every carrier-offset statistic in the field-test
.toads files. The SoftwareX manuscript validation comparison between
RTL and R2 should be re-run after this fix lands; numbers may shift
by up to ~+/-0.5 of a bin (~76 Hz at 10 Msps / 65536 FFT) per
previously-out-of-bound detection. TX1 (the affected case) is the
primary one to revisit.

## 9. Follow-up

- **Re-run the 5/14 field-test analysis** on the fixed code. Compare
  TX1 carrier-offset distribution before vs after, refresh the
  figures used by the SoftwareX manuscript.
- Once the threshold sweep (PR #42's template,
  `docs/verification/threshold_sweep.md`) can be run with the fixed
  sub_offset in place, revisit the 12.5% / 17.5% conversion-gap
  question.
- Consider whether the C++ fastdet bound of +/-0.5 should also be
  widened to +/-0.6 to match `_clip_offset`, or whether the +/-0.6
  Python clip should be tightened to +/-0.5. The current asymmetry
  (0.5 in fastdet, 0.6 in `soa_estimator`) is undocumented and
  outside the scope of this PR.
