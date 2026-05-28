"""
Unit tests for carrier_sync module.
"""

import pytest
import numpy as np

from thriftyx import carrier_sync
from thriftyx.signal_utils import Signal


FREQ_SHIFT_TESTDATA = [
    (128, 0, 0),
    (128, -32, 32),
    (128, 32, 16),
    (128, -10.5, 0.5),
    (128, 8.3, -8.3),
]


@pytest.mark.parametrize("size,freq,shift", FREQ_SHIFT_TESTDATA)
def test_freq_shift(size, freq, shift):
    """Test freq_shift() with sinusoidal signals."""
    signal = np.exp(2j*np.pi*np.arange(size)/size*freq)

    expected = np.exp(2j*np.pi*np.arange(size)/size*(freq+shift))
    expected_fft = np.fft.fft(expected)

    got = carrier_sync.freq_shift(Signal(signal), shift)

    # import matplotlib.pyplot as plt
    # plt.plot(np.abs(signal_fft), label="Signal")
    # plt.plot(np.abs(expected_fft), label="Expected")
    # plt.plot(np.abs(got), label="Got")
    # plt.legend()
    # plt.show()

    np.testing.assert_allclose(np.abs(got), np.abs(expected_fft),
                               atol=1e-6, rtol=1e-6)


def test_dirichlet_kernel():
    """Test dirichlet_kernel with specific parameters."""
    expected = np.array([-0.1711, 0.0164, 0.3164, 0.6468, 0.9034, 1.,
                         0.9034, 0.6468, 0.3164, 0.0164, -0.1711])
    got = carrier_sync.dirichlet_kernel(np.arange(-5, 6), 8192, 2015)
    np.testing.assert_allclose(got, expected, rtol=2e-3)


# In-bound offsets: the interpolator recovers them within numerical
# precision. The bound is [-0.5, 0.5] per Krueger Section 4.4.2; the
# tolerance is set wide enough to absorb scipy's bounded-optimizer
# rounding when the true value sits exactly on the boundary.
INTERPOLATOR_OFFSETS = [-0.5, -0.25, -0.1263, -0.1, 0.,
                        0.001, 0.2, 0.4995, 0.5]


@pytest.mark.parametrize("offset", INTERPOLATOR_OFFSETS)
def test_dirichlet_interpolator(offset):
    """Test Dirichlet interpolator with signal with different freq shifts."""
    peak_idx, width, block_len, carrier_len = 10, 6, 8192, 2024
    freq = (1.*offset+peak_idx)*carrier_len/block_len
    carrier = np.exp(2j*np.pi*np.arange(carrier_len)/carrier_len*freq)
    signal = np.concatenate([carrier, np.zeros(block_len-carrier_len)])
    signal_fft = np.abs(np.fft.fft(signal))
    interpolator = carrier_sync.make_dirichlet_interpolator(
        block_len, carrier_len, width)
    got = interpolator(signal_fft, peak_idx)
    np.testing.assert_allclose(got, offset, atol=1e-5, rtol=1e-5)


@pytest.mark.parametrize("true_offset,expected", [
    (-0.51, -0.5),  # below the bound -> clipped to -0.5
    (0.56,  0.5),   # above the bound -> clipped to +0.5
    (-1.0,  -0.5),  # far below
    (1.0,   0.5),   # far above
])
def test_dirichlet_interpolator_clips_out_of_bounds(true_offset, expected):
    """Offsets outside [-0.5, 0.5] clip to the boundary.

    The carrier interpolator now passes ``bounds=([0.0, -0.5], [np.inf, 0.5])``
    to ``scipy.optimize.curve_fit`` (see ``carrier_sync.py``). This test
    documents that clipping behaviour: when the algorithm is fed a peak
    whose true sub-bin position falls outside the spec bound, the
    returned offset is clamped to the nearest spec edge rather than
    drifting arbitrarily. Without bounds, real field captures saw the
    interpolator return |offset| > 1.0 (PR #39 reproducer).
    """
    peak_idx, width, block_len, carrier_len = 10, 6, 8192, 2024
    freq = (true_offset + peak_idx) * carrier_len / block_len
    carrier = np.exp(2j * np.pi * np.arange(carrier_len) / carrier_len * freq)
    signal = np.concatenate([carrier, np.zeros(block_len - carrier_len)])
    signal_fft = np.abs(np.fft.fft(signal))
    interpolator = carrier_sync.make_dirichlet_interpolator(
        block_len, carrier_len, width)
    got = interpolator(signal_fft, peak_idx)
    np.testing.assert_allclose(got, expected, atol=1e-5, rtol=1e-5)
