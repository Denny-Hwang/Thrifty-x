"""Magnitude-scale checks for Airspy INT16_IQ normalization in block_data.

libairspy's INT16_IQ path left-shifts each 12-bit ADC code by 4 and
converts the real stream to I/Q with a unity-gain half-band filter, so a
tone of A ADC codes arrives as |I + jQ| = 8 * A (measured on libairspy's
own code by scripts/airspy_scale_probe.sh: 8.08 per code).  ADC full
scale (2048 codes) is therefore int16 16384, and block_data divides by
16384 so it maps to |z| = 1, the same as RTL-SDR's (x - 127.4) / 128.

The synthetic captures below are specified in ADC codes and converted
with that x8 gain, so a divisor that drifts from the libairspy scale
(e.g. back to 2048, which made full scale |z| = 8) fails them.
"""

import io

import numpy as np
import pytest

from thriftyx.block_data import (AIRSPY_INT16_FULL_SCALE, raw_to_complex,
                                 complex_to_raw, card_reader, card_writer,
                                 write_card_header)

LIBAIRSPY_GAIN_PER_CODE = 8  # << 4, then x0.5 from real -> complex


def _synth_capture(num_samples, carrier_codes, noise_codes, carrier_bin=20,
                   block_len=65536, seed=0):
    """int16 I/Q as libairspy would deliver a tone of `carrier_codes`."""
    rs = np.random.RandomState(seed)
    t = np.arange(num_samples)
    carrier = np.exp(2j * np.pi * t * carrier_bin / block_len)
    noise = (rs.randn(num_samples) + 1j * rs.randn(num_samples)) / np.sqrt(2)
    sig = LIBAIRSPY_GAIN_PER_CODE * (carrier_codes * carrier
                                     + noise_codes * noise)
    interleaved = np.empty(num_samples * 2, dtype=np.float64)
    interleaved[0::2] = sig.real
    interleaved[1::2] = sig.imag
    return np.clip(np.rint(interleaved), -32768, 32767).astype(np.int16)


def test_full_scale_constant_matches_libairspy_gain():
    assert AIRSPY_INT16_FULL_SCALE == 2048 * LIBAIRSPY_GAIN_PER_CODE


@pytest.mark.parametrize("codes", [16, 256, 1024, 2048])
def test_tone_magnitude_is_fraction_of_adc_full_scale(codes):
    """A tone of A ADC codes normalizes to |z| = A / 2048."""
    iq = _synth_capture(4096, carrier_codes=codes, noise_codes=0)
    mag = np.abs(raw_to_complex(iq, bit_depth=12))
    np.testing.assert_allclose(mag.mean(), codes / 2048, rtol=2e-3)


def test_libairspy_saturation_maps_to_about_two():
    """int16 extremes are libairspy saturation, about 2x ADC full scale."""
    out = raw_to_complex(np.array([32767, -32768], dtype=np.int16),
                         bit_depth=12)
    np.testing.assert_allclose(out[0].real, 2.0, rtol=1e-4)
    np.testing.assert_allclose(out[0].imag, -2.0, rtol=1e-4)


def test_roundtrip_is_lossless_across_int16():
    original = np.array([0, 1, -1, 100, -100, 16384, -16384, 32767, -32768,
                         0], dtype=np.int16)
    rec = complex_to_raw(raw_to_complex(original, bit_depth=12),
                         bit_depth=12)
    np.testing.assert_array_equal(original, rec)


@pytest.mark.parametrize("carrier_codes,noise_codes,name", [
    (1.0, 2.0, "low_gain_weak_signal"),   # std ~16 int16, as seen at 0/0/0
    (20.0, 6.0, "mid_gain"),
    (600.0, 40.0, "high_gain_strong_signal"),
])
def test_operational_captures_stay_below_full_scale(carrier_codes,
                                                     noise_codes, name):
    """Unclipped captures normalize to |z| < 1, like RTL-SDR data."""
    iq = _synth_capture(4096, carrier_codes, noise_codes)
    mag = np.abs(raw_to_complex(iq, bit_depth=12))
    assert 1e-4 < np.median(mag) < 1.0, name


def test_v2_card_roundtrip_preserves_normalization():
    """A v2 .card written and read back keeps the same magnitude scale."""
    iq = _synth_capture(2048, carrier_codes=600, noise_codes=40)
    sig = raw_to_complex(iq, bit_depth=12)

    buf = io.StringIO()
    write_card_header(buf, bit_depth=12, sample_rate=10_000_000)
    card_writer(buf, 0.0, 0, sig, bit_depth=12)
    buf.seek(0)
    blocks = list(card_reader(buf))
    assert len(blocks) == 1
    np.testing.assert_array_equal(np.asarray(blocks[0][2]), sig)
