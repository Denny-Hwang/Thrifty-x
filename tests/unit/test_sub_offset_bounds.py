"""Regression tests: carrier sub_offset is bound-clipped to [-0.5, 0.5].

These tests started life (PR #39) as a REPRODUCER for an unbounded
carrier sub-bin interpolator that returned ``|offset|`` up to ~1.07 on
synthetic noisy CW, consistent with the field-observed 0.845 from R2.

After the fix landed (``curve_fit(bounds=([0.0, -0.5], [np.inf, 0.5]))``
in ``thrifty/carrier_sync.py`` and ``thriftyx/carrier_sync.py``), the
assertions are inverted to assert the bound is now respected. The
synthetic stress cases are preserved as regression tests so any future
change that drops the bounds would resurrect the bug and fail here.

Bound: ``[-0.5, 0.5]`` per Krueger Section 4.4.2 (carrier peak) and
Section 3.2 Eqs 3.4-3.6 (correlation peak). The C++ ``fastdet``
implementation enforces the same bound (``fastdet/corr_detector.cpp:97-98``);
the Python correlation path uses ``soa_estimator._clip_offset`` with
``max_=0.6`` (slightly wider than spec for parabolic/Gaussian
interpolators).

Parameters (block_len=65536, carrier_len=10232) match Thrifty-X's
Airspy R2 configuration at 10 Msps - see ``example/detector_r2.cfg``.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import warnings

import numpy as np
import pytest

from thriftyx import carrier_sync as thriftyx_cs
from thriftyx.soa_estimator import (_clip_offset, parabolic_interpolation,
                                    gaussian_interpolation)

# The shipped package is ``thriftyx``. The legacy ``thrifty`` package is a
# frozen upstream reference (not packaged, not imported by the active suite)
# - see README. These tests therefore exercise ``thriftyx`` only.


BLOCK_LEN = 65536
CARRIER_LEN = 10232


def _make_carrier(peak_idx, offset, block_len=BLOCK_LEN, carrier_len=CARRIER_LEN):
    """Synthesize a clean CW transmission with the given sub-bin offset."""
    freq = (peak_idx + offset) * carrier_len / block_len
    carrier = np.exp(2j * np.pi * np.arange(carrier_len) / carrier_len * freq)
    return np.concatenate([carrier, np.zeros(block_len - carrier_len)])


def _argmax_in_window(fft_mag, window=(7, 124)):
    """Mirror what carrier_detect.detect does (default carrier_window 7-124)."""
    start, stop = window
    return int(np.argmax(fft_mag[start:stop + 1]) + start)


# ----------------------------------------------------------------------
# Sanity tests: behaviour on clean signals matches the algorithm spec.
# These are EXPECTED to keep passing both before and after a fix lands.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("interp_module", [thriftyx_cs])
@pytest.mark.parametrize("true_offset", [-0.49, -0.25, 0.0, 0.25, 0.49])
def test_clean_carrier_offset_within_bounds(interp_module, true_offset):
    """A clean CW carrier produces an offset within [-0.5, 0.5]."""
    signal = _make_carrier(101, true_offset)
    fft_mag = np.abs(np.fft.fft(signal))
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    peak_idx = _argmax_in_window(fft_mag)
    got = interp(fft_mag, peak_idx)
    np.testing.assert_allclose(got, true_offset, atol=1e-4)
    assert -0.5 <= got <= 0.5


@pytest.mark.parametrize("interp_module", [thriftyx_cs])
def test_clean_carrier_offset_just_past_half_wraps_via_argmax(interp_module):
    """Offsets just beyond +/-0.5 are absorbed by argmax picking the neighbour bin."""
    signal = _make_carrier(101, 0.51)
    fft_mag = np.abs(np.fft.fft(signal))
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    peak_idx = _argmax_in_window(fft_mag)
    assert peak_idx == 102  # argmax rolled to the next bin
    got = interp(fft_mag, peak_idx)
    np.testing.assert_allclose(got, -0.49, atol=1e-4)
    assert -0.5 <= got <= 0.5


# ----------------------------------------------------------------------
# Reproducer tests: the carrier interpolator IS NOT bound-clipped, and
# under realistic noise it returns |offset| > 0.5.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("interp_module", [thriftyx_cs])
def test_noisy_carrier_stays_within_half_bin_bound(interp_module):
    """Under moderate noise, the bounded curve_fit stays within [-0.5, 0.5].

    Pre-fix behaviour: this same sweep produced |offset| up to ~1.07,
    matching the field-observed 0.845 on R2 TX1. After the bounded
    curve_fit landed, every trial must respect the spec bound.

    Sweep: 200 trials, each with a random true offset in [-0.5, 0.5] and
    additive complex Gaussian noise at amplitude 0.5 (carrier amplitude 1.0).
    """
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    rs = np.random.RandomState(42)

    max_abs_offset = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _ in range(200):
            true_offset = rs.uniform(-0.5, 0.5)
            signal = _make_carrier(101, true_offset)
            noise_amp = 0.5
            noise = (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * noise_amp
            fft_mag = np.abs(np.fft.fft(signal + noise))
            peak_idx = _argmax_in_window(fft_mag)
            got = interp(fft_mag, peak_idx)
            assert -0.5 <= got <= 0.5, (
                "Bound violation: noisy CW trial returned offset {:.6f} "
                "outside [-0.5, 0.5]. The bounded curve_fit must have been "
                "removed from {}.".format(got, interp_module.__name__))
            max_abs_offset = max(max_abs_offset, abs(got))

    # Sanity: the algorithm is actually exercising its full range, not
    # stuck at zero - we want to confirm the bound is active, not
    # vacuous.
    assert max_abs_offset > 0.3, (
        "All offsets stayed within +/-0.3; the bound assertion above is "
        "vacuous. Check that the noise injection is actually perturbing "
        "the curve_fit away from the true offset.")


@pytest.mark.parametrize("interp_module", [thriftyx_cs])
def test_dual_bin_equal_peaks_stays_at_boundary(interp_module):
    """Two coherent carriers at adjacent bins do NOT by themselves break the bound.

    With perfectly equal amplitudes the Dirichlet fit picks the midpoint
    (offset ~ +-0.5). This test documents that the dual-bin pattern alone
    is not the trigger - noise plus dual-bin energy together drive the
    fit out of bounds.
    """
    freq1 = 101.0 * CARRIER_LEN / BLOCK_LEN
    freq2 = 102.0 * CARRIER_LEN / BLOCK_LEN
    arr = np.arange(CARRIER_LEN)
    c1 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq1)
    c2 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq2)
    signal = np.concatenate([c1 + c2, np.zeros(BLOCK_LEN - CARRIER_LEN)])
    fft_mag = np.abs(np.fft.fft(signal))
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    peak_idx = _argmax_in_window(fft_mag)
    got = interp(fft_mag, peak_idx)
    assert abs(got) < 0.5  # stays inside (just)
    assert abs(got) > 0.4  # but right at the edge


@pytest.mark.parametrize("interp_module", [thriftyx_cs])
def test_dual_bin_plus_noise_stays_within_bound(interp_module):
    """TX1-like pattern (energy split across bins 101+102) + noise still
    yields ``|offset| <= 0.5``.

    This is the closest synthetic analogue to the observed BatRF TX1
    behaviour. The bounded curve_fit must keep every trial inside the
    spec bound even under the dual-bin + noise stressor.
    """
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    freq1 = 101.0 * CARRIER_LEN / BLOCK_LEN
    freq2 = 102.0 * CARRIER_LEN / BLOCK_LEN
    arr = np.arange(CARRIER_LEN)
    c1 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq1)
    c2 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq2)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rs = np.random.RandomState(7)
        for trial in range(200):
            ratio = rs.uniform(0.7, 1.3)
            phase = rs.uniform(0, 2 * np.pi)
            sig_t = c1 + ratio * c2 * np.exp(1j * phase)
            signal = np.concatenate([sig_t, np.zeros(BLOCK_LEN - CARRIER_LEN)])
            noise = (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * 0.3
            fft_mag = np.abs(np.fft.fft(signal + noise))
            peak_idx = _argmax_in_window(fft_mag)
            got = interp(fft_mag, peak_idx)
            assert -0.5 <= got <= 0.5, (
                "Trial {}: dual-bin+noise returned offset {:.6f} outside "
                "[-0.5, 0.5] from {}.".format(trial, got,
                                                interp_module.__name__))


# ----------------------------------------------------------------------
# Reference test: the correlation interpolator IS bounded via _clip_offset.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (-1.0, -0.6),
    (-0.6, -0.6),
    (-0.5, -0.5),
    (0.0, 0.0),
    (0.5, 0.5),
    (0.6, 0.6),
    (1.0, 0.6),
])
def test_correlation_offset_is_clipped(raw, expected):
    """``soa_estimator._clip_offset`` enforces the correlation-peak bound.

    Note: the clip width is +-0.6, not +-0.5. The extra 0.1 of slack is
    deliberate to accommodate the parabolic/Gaussian interpolator's mild
    boundary excursions. See ``thriftyx/soa_estimator.py:20``.
    """
    assert _clip_offset(raw) == expected


# ----------------------------------------------------------------------
# Case C (prompt): a correlation peak straddling two adjacent samples
# interpolates within the spec bound. Exercises the actual
# parabolic/Gaussian interpolators (not just _clip_offset).
# ----------------------------------------------------------------------

@pytest.mark.parametrize("interp_fn", [parabolic_interpolation,
                                       gaussian_interpolation])
@pytest.mark.parametrize("true_off", [-0.49, -0.25, 0.0, 0.25, 0.49])
def test_correlation_straddle_within_bound(interp_fn, true_off):
    """A correlation peak centred at ``k + true_off`` recovers a sub-sample
    offset inside [-0.5, 0.5].

    Builds a Gaussian-shaped peak (the despread autocorrelation of a
    band-limited code is locally Gaussian near its maximum), straddling
    samples ``k`` and ``k+1``. Both 3-point interpolators must return a
    bounded offset; the Gaussian interpolator is exact for a Gaussian.
    """
    k, n, sigma = 50, 101, 1.2
    idx = np.arange(n)
    corr_mag = np.exp(-((idx - (k + true_off)) ** 2) / (2 * sigma ** 2))
    peak_idx = int(np.argmax(corr_mag))
    assert peak_idx == k  # |true_off| < 0.5 keeps the argmax at k

    got = interp_fn(corr_mag, peak_idx)
    assert -0.5 <= got <= 0.5, (
        "{} returned {:.6f} outside [-0.5, 0.5] for a straddle at "
        "k+{:.2f}".format(interp_fn.__name__, got, true_off))
    # And the production clip is a no-op inside the bound.
    assert _clip_offset(got) == got

    if interp_fn is gaussian_interpolation:
        np.testing.assert_allclose(got, true_off, atol=1e-6)
