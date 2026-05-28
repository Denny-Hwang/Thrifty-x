"""Reproducer: carrier sub_offset can exceed the [-0.5, 0.5] bound.

These tests document the current behaviour of the carrier sub-bin
interpolator (``thrifty.carrier_sync.make_dirichlet_interpolator`` and
``thriftyx.carrier_sync.make_dirichlet_interpolator``) when applied to
noisy signals or signals whose spectrum contains energy spread across
multiple adjacent bins.

The Krueger dissertation specifies sub-sample offsets in [-0.5, 0.5)
(Section 3.2 Eqs 3.4-3.6 for correlation peak, Section 4.4.2 for carrier
peak). The C++ ``fastdet`` implementation enforces this bound explicitly
(see ``fastdet/corr_detector.cpp:97-98``). The Python implementation does
not - ``curve_fit`` is invoked without ``bounds`` and the returned offset
is propagated unmodified through ``Synchronizer.sync``.

Field-test observation that motivated this reproducer:
    Airspy R2 at 161.3 MHz with ext. 20 dB LNA, internal gain 0/0/0
    produced carrier offsets up to 0.845 on TX1 bins 101 and 102.

These tests are PASSING expectation-style tests: each one asserts the
*observed* behaviour. When a fix lands (clipping at +-0.5 or
``bounds=`` on curve_fit), the appropriate assertions will need to be
inverted to assert the bound is respected.

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

from thrifty import carrier_sync as upstream_cs
from thriftyx import carrier_sync as thriftyx_cs
from thrifty.soa_estimator import _clip_offset


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

@pytest.mark.parametrize("interp_module", [upstream_cs, thriftyx_cs])
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


@pytest.mark.parametrize("interp_module", [upstream_cs, thriftyx_cs])
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

@pytest.mark.parametrize("interp_module", [upstream_cs, thriftyx_cs])
def test_noisy_carrier_can_exceed_half_bin_bound(interp_module):
    """Under moderate noise, the Dirichlet curve_fit returns |offset| > 0.5.

    This reproduces the field-observed 0.845 carrier offset on TX1 with
    Airspy R2 + 20 dB external LNA.

    Sweep: 200 trials, each with a random true offset in [-0.5, 0.5] and
    additive complex Gaussian noise at amplitude 0.5 (carrier amplitude 1.0).
    """
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    rs = np.random.RandomState(42)

    excursions = []
    max_abs_offset = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # scipy OptimizeWarning is expected
        for _ in range(200):
            true_offset = rs.uniform(-0.5, 0.5)
            signal = _make_carrier(101, true_offset)
            noise_amp = 0.5
            noise = (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * noise_amp
            fft_mag = np.abs(np.fft.fft(signal + noise))
            peak_idx = _argmax_in_window(fft_mag)
            got = interp(fft_mag, peak_idx)
            if abs(got) > 0.5:
                excursions.append(got)
            max_abs_offset = max(max_abs_offset, abs(got))

    # Currently expected (bug): at least some trials exceed the half-bin bound.
    assert len(excursions) > 0, (
        "Expected the interpolator to return |offset|>0.5 in noisy trials, "
        "but it never did. If this assertion fails, the bug may already be "
        "fixed - in that case, invert this assertion to assert that the "
        "interpolator always stays within [-0.5, 0.5]."
    )
    # And the largest excursion is similar in magnitude to the field-observed
    # 0.845 value. Document the headroom rather than asserting an exact value.
    assert max_abs_offset > 0.6, (
        "Largest |offset| was {:.4f}; expected something close to the "
        "field-observed 0.845 (with this seed/noise/parameters the "
        "reference run produces max |offset| ~ 1.02).".format(max_abs_offset)
    )


@pytest.mark.parametrize("interp_module", [upstream_cs, thriftyx_cs])
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


@pytest.mark.parametrize("interp_module", [upstream_cs, thriftyx_cs])
def test_dual_bin_plus_noise_exceeds_bound(interp_module):
    """TX1-like pattern (energy split across bins 101 and 102) + noise
    pushes the carrier interpolator past |0.5|.

    This is the closest synthetic analogue to the observed BatRF TX1
    behaviour: carrier energy bridging two adjacent bins together with
    realistic noise.
    """
    interp = interp_module.make_dirichlet_interpolator(BLOCK_LEN, CARRIER_LEN)
    freq1 = 101.0 * CARRIER_LEN / BLOCK_LEN
    freq2 = 102.0 * CARRIER_LEN / BLOCK_LEN
    arr = np.arange(CARRIER_LEN)
    c1 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq1)
    c2 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq2)

    excursions = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rs = np.random.RandomState(7)
        for _ in range(200):
            ratio = rs.uniform(0.7, 1.3)
            phase = rs.uniform(0, 2 * np.pi)
            sig_t = c1 + ratio * c2 * np.exp(1j * phase)
            signal = np.concatenate([sig_t, np.zeros(BLOCK_LEN - CARRIER_LEN)])
            noise = (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * 0.3
            fft_mag = np.abs(np.fft.fft(signal + noise))
            peak_idx = _argmax_in_window(fft_mag)
            got = interp(fft_mag, peak_idx)
            if abs(got) > 0.5:
                excursions.append(got)

    assert len(excursions) > 0, (
        "Expected dual-bin + noise to push the interpolator past |0.5| in "
        "at least one trial out of 200; got zero."
    )


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
    boundary excursions. See ``thrifty/soa_estimator.py:16``.
    """
    assert _clip_offset(raw) == expected
