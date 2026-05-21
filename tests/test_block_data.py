"""
Unit tests for block_data module.
"""

import io

import numpy as np
import numpy.testing as npt
import pytest

from thriftyx import block_data


def test_raw_to_complex():
    """Test raw-to-complex conversion."""
    raw_list = [0, 0, 127, 128, 255, 255]
    complex_list = [-0.9953-0.9953j, -0.0031+0.0047j, 0.9969+0.9969j]
    raw = np.array(raw_list, dtype=np.uint8)
    expected_result = np.array(complex_list, dtype=np.complex64)
    result = block_data.raw_to_complex(raw)
    npt.assert_allclose(result, expected_result, rtol=1e-2)


def test_complex_to_raw():
    """Test complex-to-raw conversion."""
    raw_list = [0, 0, 127, 128, 255, 255]
    complex_list = [-0.9953-0.9953j, -0.0031+0.0047j, 0.9969+0.9969j]
    raw = np.array(raw_list, dtype=np.uint8)
    complex_array = np.array(complex_list, dtype=np.complex64)
    actual_raw = block_data.complex_to_raw(complex_array)
    npt.assert_array_equal(actual_raw, raw)


def test_raw_to_complex_inverse():
    """Test that raw-to-complex is inverse of complex-to-raw."""
    expected = np.arange(256, dtype=np.uint8)
    actual = block_data.complex_to_raw(block_data.raw_to_complex(expected))
    npt.assert_array_equal(actual, expected)


def test_block_reader():
    """Test block_reader size and history."""
    stream = io.BytesIO(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
                        b"\x0c\x0d")
    blocks = list(block_data.block_reader(stream, 3, 1))

    indices = [b[1] for b in blocks]
    data = [b[2] for b in blocks]
    raw = [list(block_data.complex_to_raw(d)) for d in data]
    expected_raw = [[0x7f, 0x7f, 0x00, 0x01, 0x02, 0x03],
                    [0x02, 0x03, 0x04, 0x05, 0x06, 0x07],
                    [0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b]]
    # Note: The last few samples, [0x0a, 0x0b, 0x0c, 0x0d], will be skipped
    # since it does not fill an entire block_data.

    assert raw == expected_raw
    assert indices == list(range(len(data)))


def test_raw_to_complex_8bit_basic():
    """8-bit interleaved I/Q is paired and DC-offset is removed."""
    raw = np.array([127, 127, 130, 124, 100, 150], dtype=np.uint8)
    expected = np.array([
        (-0.4 - 0.4j) / 128.0,
        (2.6 - 3.4j) / 128.0,
        (-27.4 + 22.6j) / 128.0,
    ], dtype=np.complex64)
    result = block_data.raw_to_complex(raw, bit_depth=8)
    assert result.dtype == np.complex64
    assert result.shape == (3,)
    assert result.flags['C_CONTIGUOUS']
    npt.assert_allclose(result, expected, rtol=1e-5, atol=1e-7)


def test_raw_to_complex_12bit_basic():
    """12-bit int16 interleaved I/Q is paired and normalized by 2048."""
    raw = np.array([0, 0, 1024, -1024, -2048, 2047], dtype=np.int16)
    expected = np.array([
        0.0 + 0.0j,
        0.5 - 0.5j,
        -1.0 + (2047 / 2048.0) * 1j,
    ], dtype=np.complex64)
    result = block_data.raw_to_complex(raw, bit_depth=12)
    assert result.dtype == np.complex64
    assert result.shape == (3,)
    npt.assert_allclose(result, expected, rtol=1e-6, atol=1e-7)


def test_raw_to_complex_8bit_odd_length_raises():
    """Odd-length input must raise ValueError instead of silently misaligning."""
    raw = np.array([0, 1, 2], dtype=np.uint8)
    with pytest.raises(ValueError):
        block_data.raw_to_complex(raw, bit_depth=8)


def test_raw_to_complex_12bit_odd_length_raises():
    """Odd-length input must raise ValueError for 12-bit too."""
    raw = np.array([0, 1, 2], dtype=np.int16)
    with pytest.raises(ValueError):
        block_data.raw_to_complex(raw, bit_depth=12)


def test_raw_to_complex_round_trip_8bit():
    """Random uint8 length-2N array round-trips within ±1 LSB."""
    rng = np.random.default_rng(seed=12345)
    raw = rng.integers(0, 256, size=2048, dtype=np.uint8)
    recovered = block_data.complex_to_raw(
        block_data.raw_to_complex(raw, bit_depth=8), bit_depth=8)
    diff = recovered.astype(np.int16) - raw.astype(np.int16)
    assert np.max(np.abs(diff)) <= 1


def test_raw_to_complex_round_trip_12bit():
    """Random int16 within 12-bit range round-trips within ±1 LSB."""
    rng = np.random.default_rng(seed=12345)
    raw = rng.integers(-2048, 2048, size=2048, dtype=np.int16)
    recovered = block_data.complex_to_raw(
        block_data.raw_to_complex(raw, bit_depth=12), bit_depth=12)
    diff = recovered.astype(np.int32) - raw.astype(np.int32)
    assert np.max(np.abs(diff)) <= 1


def test_raw_to_complex_int16_envelope_finite():
    """int16 extremes (FIR overshoot envelope) yield finite complex64."""
    raw = np.array([-32768, 32767, 32767, -32768], dtype=np.int16)
    result = block_data.raw_to_complex(raw, bit_depth=12)
    assert result.dtype == np.complex64
    assert np.isfinite(result).all()


def test_card_reader():
    """Basic test for card_reader"""
    stream = io.BytesIO(b"# Some comments\n"
                        b"# more comments\n"
                        b"1000.5425 10 r0+Om5==\n"
                        b"1000.5442 20 aaaaaa==")
    blocks = list(block_data.card_reader(stream))
    timestamps, indices, data = zip(*blocks)
    chars = [tuple(block_data.complex_to_raw(x)) for x in data]

    assert timestamps == (1000.5425, 1000.5442)
    assert indices == (10, 20)
    assert chars == [(175, 79, 142, 155), (105, 166, 154, 105)]
