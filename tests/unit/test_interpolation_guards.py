# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Regression tests for NaN/degenerate-peak guards in the interpolators.

Covers the staff-level review findings N3 (parabolic zero-denominator +
NaN-safe clip on the *default* SoA path) and N8 (Dirichlet curve_fit
failure must not abort a detect run).
"""

import numpy as np

from thriftyx import soa_estimator
from thriftyx.carrier_sync import make_dirichlet_interpolator
from thriftyx.soa_estimator import (_clip_offset, gaussian_interpolation,
                                    parabolic_interpolation)


class TestClipOffset:
    def test_clips_positive(self):
        assert _clip_offset(1.5) == 0.6

    def test_clips_negative(self):
        assert _clip_offset(-1.5) == -0.6

    def test_passes_in_range(self):
        assert _clip_offset(0.25) == 0.25

    def test_nan_is_forced_to_zero(self):
        # NaN compares False against both bounds; the clip must not let
        # it through into the .toad output.
        assert _clip_offset(float('nan')) == 0.0

    def test_inf_is_clipped(self):
        assert _clip_offset(float('inf')) == 0.6
        assert _clip_offset(float('-inf')) == -0.6


class TestParabolicInterpolation:
    def test_flat_peak_returns_zero(self):
        # a == b == c (e.g. clipped/saturated correlation): the legacy
        # formula divided by zero and produced NaN/inf.
        corr_mag = np.array([1.0, 5.0, 5.0, 5.0, 1.0])
        assert parabolic_interpolation(corr_mag, 2) == 0

    def test_result_is_finite_on_flat_neighbourhood(self):
        corr_mag = np.ones(7)
        offset = parabolic_interpolation(corr_mag, 3)
        assert np.isfinite(offset)

    def test_normal_peak_unchanged(self):
        # Symmetric peak -> offset 0; skewed peak -> offset toward the
        # larger neighbour.
        sym = np.array([0.0, 1.0, 2.0, 1.0, 0.0])
        assert parabolic_interpolation(sym, 2) == 0
        skew = np.array([0.0, 1.0, 2.0, 1.8, 0.0])
        assert 0 < parabolic_interpolation(skew, 2) < 1

    def test_matches_gaussian_guard_behaviour(self):
        # Both interpolators must agree that a flat peak yields 0.
        corr_mag = np.array([2.0, 2.0, 2.0])
        assert parabolic_interpolation(corr_mag, 1) == 0
        assert gaussian_interpolation(corr_mag, 1) == 0


class TestSoaEstimatorNaNPath:
    def test_flat_correlation_never_emits_nan(self):
        """End-to-end guard: a constant-magnitude correlation through the
        estimator's interpolate + clip must produce a finite offset."""
        est = soa_estimator.SoaEstimator(
            template=np.ones(4, dtype=np.complex64),
            thresh_coeffs=(0.0, 0.0, 0.0),
            block_len=16,
            history_len=5,
            interpolation_method='parabolic')
        corr_mag = np.ones(13)
        offset = soa_estimator._clip_offset(est.interpolate(corr_mag, 6))
        assert np.isfinite(offset)


class TestDirichletFitFallback:
    def test_nan_in_window_falls_back_to_zero(self):
        interp = make_dirichlet_interpolator(block_len=64, carrier_len=16)
        fft_mag = np.ones(32)
        fft_mag[16] = 10.0    # peak
        fft_mag[15] = np.nan  # poisons the curve_fit window
        assert interp(fft_mag, 16) == 0

    def test_nan_fallback_with_amplitude(self):
        interp = make_dirichlet_interpolator(block_len=64, carrier_len=16,
                                             return_amplitude=True)
        fft_mag = np.ones(32)
        fft_mag[16] = 10.0
        fft_mag[17] = np.nan
        amplitude, offset = interp(fft_mag, 16)
        assert amplitude == 10.0
        assert offset == 0

    def test_clean_peak_still_fits(self):
        block_len, carrier_len = 64, 16
        interp = make_dirichlet_interpolator(block_len, carrier_len)
        # Synthesize a carrier exactly on a bin: offset must be ~0.
        t = np.arange(block_len)
        signal = np.zeros(block_len, dtype=np.complex128)
        signal[:carrier_len] = np.exp(2j * np.pi * 8 * t[:carrier_len]
                                      / block_len)
        fft_mag = np.abs(np.fft.fft(signal))
        offset = interp(fft_mag, 8)
        assert abs(offset) < 0.1
