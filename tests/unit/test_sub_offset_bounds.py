import numpy as np

from thrifty import carrier_sync


def _fft_mag_for_tone(block_len, carrier_len, bin_pos):
    n = np.arange(block_len)
    freq = bin_pos * carrier_len / block_len
    signal = np.exp(2j * np.pi * freq * n / carrier_len)
    signal[carrier_len:] = 0
    return np.abs(np.fft.fft(signal))


def test_dirichlet_interpolator_half_bin_offset_is_bounded():
    block_len = 256
    carrier_len = 128
    peak_idx = 101
    fft_mag = _fft_mag_for_tone(block_len, carrier_len, peak_idx + 0.5)

    interpolator = carrier_sync.make_dirichlet_interpolator(block_len, carrier_len, width=6)
    offset = interpolator(fft_mag, peak_idx)

    assert -0.5 <= offset <= 0.5


def test_dirichlet_interpolator_dual_bin_straddle_reproduces_out_of_bounds_offset():
    block_len = 256
    carrier_len = 128
    peak_idx = 101
    fft_mag = _fft_mag_for_tone(block_len, carrier_len, peak_idx + 0.845)

    # ensure synthetic resembles two-bin split around bins 101/102
    assert fft_mag[101] > 0
    assert fft_mag[102] > 0

    interpolator = carrier_sync.make_dirichlet_interpolator(block_len, carrier_len, width=6)
    offset = interpolator(fft_mag, peak_idx)

    assert offset > 0.5
    np.testing.assert_allclose(offset, 0.845, atol=1e-3, rtol=0)
