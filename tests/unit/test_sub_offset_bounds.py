"""Carrier sub-bin offset: accurate, and re-centred to [-0.5, 0.5].

History: PR #39 saw the Dirichlet fit return |offset| up to ~1.07 on
noisy CW (0.845 in the field on R2 TX1) and clipped the fit to
[-0.5, 0.5].  Those offsets were not a fitting bug: the carrier's main
lobe is ``block_len / carrier_len`` bins wide (6.4 at R2's 10 Msps) and
flat on top, so noise often makes a neighbour the largest bin and the
carrier really is more than half a bin from it.  Clipping biased every
such estimate by up to the lobe width.

Now the fit may range over its window, and ``Synchronizer.sync``
re-centres on the nearest bin, so ``CarrierSyncInfo.offset`` (the
``.toad`` column) still lies in [-0.5, 0.5] while ``bin + offset``
tracks the true carrier frequency.

The correlation-peak side is unchanged: ``soa_estimator._clip_offset``
bounds it to +/-0.6.

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
from thriftyx.signal_utils import Signal
from thriftyx.soa_estimator import (_clip_offset, parabolic_interpolation,
                                    gaussian_interpolation)

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
# Re-centring and accuracy under noise.
# ----------------------------------------------------------------------

class _Stub:
    """Synchronizer parts with a fixed peak and interpolated offset."""

    def __init__(self, peak_idx, offset):
        self.peak_idx, self.offset = peak_idx, offset
        self.shift = None

    def detector(self, _fft_mag):
        return True, self.peak_idx, 1.0, 0.1

    def interpolator(self, _fft_mag, _peak_idx):
        return self.offset

    def shifter(self, _signal, shift):
        self.shift = shift
        return np.zeros(8)


@pytest.mark.parametrize("peak_idx, fitted, want_bin, want_offset", [
    (100, 0.3, 100, 0.3),
    (100, 1.3, 101, 0.3),
    (100, -0.7, 99, 0.3),
    (100, -2.2, 98, -0.2),
    (0, -0.7, BLOCK_LEN - 1, 0.3),   # wraps like the FFT bins
])
def test_sync_recentres_on_nearest_bin(peak_idx, fitted, want_bin,
                                       want_offset):
    stub = _Stub(peak_idx, fitted)
    sync = thriftyx_cs.Synchronizer(stub.detector, stub.interpolator,
                                    stub.shifter)
    signal = Signal(np.zeros(BLOCK_LEN, dtype=np.complex64))
    _, info = sync(signal)
    assert info.bin == want_bin
    np.testing.assert_allclose(info.offset, want_offset, atol=1e-12)
    # The shift applied is unchanged by re-centring (mod N).
    assert (stub.shift + peak_idx + fitted) % BLOCK_LEN == \
        pytest.approx(0, abs=1e-9)


def _noisy_trials(noise_amp, trials=60, seed=42):
    """Estimate errors and reported offsets for noisy R2 transmissions."""
    sync = thriftyx_cs.DefaultSynchronizer((0, 0, 0), (7, 124),
                                           BLOCK_LEN, CARRIER_LEN)
    rs = np.random.RandomState(seed)
    errors, offsets = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _ in range(trials):
            true = 101 + rs.uniform(-0.5, 0.5)
            start = rs.randint(0, BLOCK_LEN - CARRIER_LEN)
            n = np.arange(CARRIER_LEN)
            x = np.zeros(BLOCK_LEN, dtype=complex)
            x[start:start + CARRIER_LEN] = np.exp(
                2j * np.pi * true * (start + n) / BLOCK_LEN)
            x += (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * noise_amp
            _, info = sync(Signal(x))
            errors.append(info.bin + info.offset - true)
            offsets.append(info.offset)
    return np.array(errors), np.array(offsets)


def test_noisy_carrier_frequency_is_accurate():
    """Against ground truth: with the fit clipped to +/-0.5 this sweep
    had RMS error 0.20 bin and worst case 0.56 bin; unclipped and
    re-centred it is 0.075 / 0.19."""
    errors, offsets = _noisy_trials(noise_amp=1.0)
    assert np.all(np.abs(offsets) <= 0.5)
    assert np.sqrt(np.mean(errors ** 2)) < 0.12
    assert np.max(np.abs(errors)) < 0.35


def test_noisy_offsets_exercise_the_full_range():
    """The re-centred offsets are not stuck near zero."""
    _, offsets = _noisy_trials(noise_amp=0.5)
    assert np.max(np.abs(offsets)) > 0.3


@pytest.mark.parametrize("interp_module", [thriftyx_cs])
def test_dual_bin_equal_peaks_fit_the_midpoint(interp_module):
    """Two equal coherent carriers at adjacent bins fit at their midpoint
    (offset ~ +/-0.5 from either bin)."""
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
    np.testing.assert_allclose(peak_idx + got, 101.5, atol=0.05)


def test_dual_bin_plus_noise_reports_offsets_within_half_bin():
    """TX1-like pattern (energy split across bins 101+102) + noise: the
    reported offset stays in [-0.5, 0.5], and the typical estimate lies
    between the two carriers.  (With near-opposite phases the two lobes
    cancel between the carriers and the largest bin moves outside them;
    such a spectrum has no single carrier frequency to recover.)"""
    sync = thriftyx_cs.DefaultSynchronizer((0, 0, 0), (7, 124),
                                           BLOCK_LEN, CARRIER_LEN)
    freq1 = 101.0 * CARRIER_LEN / BLOCK_LEN
    freq2 = 102.0 * CARRIER_LEN / BLOCK_LEN
    arr = np.arange(CARRIER_LEN)
    c1 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq1)
    c2 = np.exp(2j * np.pi * arr / CARRIER_LEN * freq2)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rs = np.random.RandomState(7)
        estimates = []
        for trial in range(60):
            ratio = rs.uniform(0.7, 1.3)
            phase = rs.uniform(0, 2 * np.pi)
            sig_t = c1 + ratio * c2 * np.exp(1j * phase)
            signal = np.concatenate([sig_t, np.zeros(BLOCK_LEN - CARRIER_LEN)])
            noise = (rs.randn(BLOCK_LEN) + 1j * rs.randn(BLOCK_LEN)) * 0.3
            _, info = sync(Signal(signal + noise))
            assert -0.5 <= info.offset <= 0.5, trial
            estimates.append(info.bin + info.offset)
    assert abs(np.median(estimates) - 101.5) < 0.1


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
