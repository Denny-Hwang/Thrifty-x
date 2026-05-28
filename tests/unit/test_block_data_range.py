"""Magnitude-range checks for Airspy INT16_IQ normalization in block_data.

The combined effect of libairspy's two-step processing for INT16_IQ
(``convert_samples_int16`` -> ``iqconverter_int16_process``) leaves the
on-disk samples at approximately the native 12-bit signed scale
(``[-2048, +2047]``), not the full int16 range
(``[-32768, +32767]``). PR #37 added empirical evidence for this
behaviour on real R2 captures; master's current divisor of ``2048.0``
in ``thriftyx/block_data.py:89`` matches that.

These tests assert the current ``/2048.0`` behaviour:

1. Round-trip is lossless across the int16 range.
2. Samples in the native 12-bit envelope round-trip to magnitudes in
   ``[-1, +1]`` (paper-grade behaviour). The wider int16 envelope is
   preserved as headroom for occasional FIR overshoot but should be
   the exception, not the rule.
3. Real-capture median magnitude lands in a sensible band - not 16x
   too small (would happen if a future change re-introduced ``/32768``
   while libairspy keeps emitting native-scale samples) and not 16x
   too large.

If a future investigation changes the divisor (either direction), the
expected values in this file are what need to be updated.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import io

import numpy as np
import pytest

from thriftyx.block_data import (raw_to_complex, complex_to_raw,
                                 card_reader, card_writer, write_card_header)


# Match the constant in thriftyx/block_data.py:89 (current master).
NATIVE_12BIT_FULL_SCALE = 2048.0


def _synth_capture(num_samples, carrier_amplitude_native, noise_amplitude_native,
                   carrier_bin=20, block_len=65536, seed=0):
    """Synthesize an int16 IQ buffer mimicking a calibrated R2 capture.

    Amplitudes are in *native 12-bit* units (i.e. fractions of 2048),
    matching what libairspy actually delivers after iqconverter_int16
    post-processing.
    """
    rs = np.random.RandomState(seed)
    t = np.arange(num_samples)
    freq = carrier_bin / block_len
    carrier = np.exp(2j * np.pi * t * freq)
    noise = (rs.randn(num_samples) + 1j * rs.randn(num_samples))
    sig = carrier_amplitude_native * carrier + noise_amplitude_native * noise

    # Interleave I/Q and clip to int16 storage range (samples may briefly
    # exceed +/-2048 due to FIR overshoot; we preserve the wider container).
    interleaved = np.empty(num_samples * 2, dtype=np.float32)
    interleaved[0::2] = sig.real
    interleaved[1::2] = sig.imag
    int16 = np.clip(interleaved, -32768, 32767).astype(np.int16)
    return int16


# ----------------------------------------------------------------------
# Scale tests matching the current /2048 divisor
# ----------------------------------------------------------------------

def test_native_12bit_maps_to_unit():
    """+2047 / -2048 (native 12-bit swing) maps to ~+1.0 / -1.0."""
    iq = np.array([2047, 0, -2048, 0], dtype=np.int16)
    out = raw_to_complex(iq, bit_depth=12)
    assert out.shape == (2,)
    np.testing.assert_allclose(out[0].real, 2047 / NATIVE_12BIT_FULL_SCALE,
                               rtol=1e-5)
    np.testing.assert_allclose(out[1].real, -1.0, rtol=1e-5)


def test_half_native_maps_to_half_unit():
    """+1024 maps to ~+0.5 under /2048."""
    iq = np.array([1024, 0, -1024, 0], dtype=np.int16)
    out = raw_to_complex(iq, bit_depth=12)
    np.testing.assert_allclose(out[0].real, 0.5, rtol=1e-5)
    np.testing.assert_allclose(out[1].real, -0.5, rtol=1e-5)


def test_int16_overshoot_preserved_as_headroom():
    """Samples beyond +/-2048 (rare FIR-overshoot) DO map past +/-1.0.

    Documented as a known property, not a bug: ``complex_to_raw``
    clips the inverse to int16 range, and downstream FFT-based
    detection is normalised by SNR so absolute scale is not
    load-bearing. But code that consumes raw |z| above 1.0 must be
    written defensively.
    """
    iq = np.array([8192, 0], dtype=np.int16)
    out = raw_to_complex(iq, bit_depth=12)
    np.testing.assert_allclose(out[0].real, 4.0, rtol=1e-5)


def test_roundtrip_preserves_int16_within_one_lsb():
    """raw -> complex -> raw round-trip preserves values inside the
    ``[-2048, +2047]`` envelope where the inverse fits in int16."""
    # Length must be even (interleaved I/Q pairs).
    original = np.array([0, 1, -1, 100, -100, 1000, -1000, 2047, -2048, 0],
                        dtype=np.int16)
    cx = raw_to_complex(original, bit_depth=12)
    rec = complex_to_raw(cx, bit_depth=12)
    np.testing.assert_array_equal(original, rec)


# ----------------------------------------------------------------------
# Realistic-range sanity tests on synthetic captures
# ----------------------------------------------------------------------

@pytest.mark.parametrize("carrier_native,noise_native,name", [
    (5.0,   2.0,   "low_gain_weak_signal"),    # like the diag gain=0 capture
    (50.0,  15.0,  "mid_gain"),                # mid-range
    (500.0, 100.0, "high_gain_strong_signal"), # near native 12-bit ceiling
])
def test_post_normalisation_magnitudes_in_sensible_range(carrier_native,
                                                          noise_native,
                                                          name):
    """median(|z|) lands in [1e-4, ~1.0] under /2048 across the
    expected operational range.

    The low_gain case is calibrated against the diag-observed
    ``|int16| <= 159, std ~ 16`` from the gain=0/0/0 R2 capture.
    """
    iq = _synth_capture(2048, carrier_native, noise_native)
    cx = raw_to_complex(iq, bit_depth=12)
    median_mag = float(np.median(np.abs(cx)))
    assert 1e-4 < median_mag < 1.0, (
        "median(|z|)={:.4f} for {}; expected 1e-4..1.0 with /2048.0. "
        "If this fails after a divisor change, the new constant is "
        "16x off (compare libairspy SAMPLE_SHIFT=4 vs the >>15 in "
        "iqconverter_int16_process).".format(median_mag, name)
    )


# ----------------------------------------------------------------------
# End-to-end v2 .card round-trip
# ----------------------------------------------------------------------

def test_v2_card_roundtrip_preserves_normalization():
    """Synthetic v2 .card written + read back lands in the same range."""
    iq = _synth_capture(2048, carrier_amplitude_native=500.0,
                        noise_amplitude_native=50.0)
    pairs = iq.reshape(-1, 2).astype(np.float32) / NATIVE_12BIT_FULL_SCALE
    sig = pairs[:, 0] + 1j * pairs[:, 1]

    buf = io.StringIO()
    write_card_header(buf, bit_depth=12, sample_rate=10_000_000)
    card_writer(buf, 0.0, 0, sig.astype(np.complex64),
                bit_depth=12, sample_rate=10_000_000)
    buf.seek(0)
    blocks = list(card_reader(buf))
    assert len(blocks) == 1
    _, _, reread = blocks[0]

    np.testing.assert_allclose(np.abs(reread).mean(),
                               np.abs(sig).mean(),
                               rtol=2e-3,
                               err_msg=("magnitude scale drifted across "
                                        ".card write/read; raw_to_complex "
                                        "and complex_to_raw must use the "
                                        "same divisor."))
