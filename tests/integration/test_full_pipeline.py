# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""End-to-end pipeline tests with synthetic signals."""

import io
import base64

import numpy as np
import pytest

from thriftyx.block_data import (raw_to_complex, complex_to_raw,
                                   card_reader, card_writer, write_card_header)
from tests.mocks.mock_device import MockSDRDevice
from tests.mocks.signal_generator import generate_carrier, add_noise, complex_to_int16


def test_12bit_conversion_pipeline():
    """MockSDR → block_data(12-bit) → complex and back."""
    dev = MockSDRDevice(sample_rate=6_000_000, noise_amplitude=50.0)
    dev.open()
    raw = dev.read_sync(1024)
    dev.close()

    assert raw.dtype == np.int16
    assert len(raw) == 1024 * 2

    # Convert to complex
    complex_data = raw_to_complex(raw, bit_depth=12)
    assert complex_data.dtype == np.complex64
    assert len(complex_data) == 1024

    # Values should be in normalized range
    assert np.max(np.abs(complex_data)) <= 1.01  # slight rounding tolerance


def test_backward_compat_v1_card():
    """Read v1 .card file (uint8 RTL-SDR) through new pipeline."""
    # Create v1 card data
    raw = np.full(64, 127, dtype=np.uint8)  # DC-only signal
    encoded = base64.b64encode(raw.tobytes()).decode('ascii')
    card_content = f"1.0 0 {encoded}\n"

    stream = io.StringIO(card_content)
    blocks = list(card_reader(stream))

    assert len(blocks) == 1
    ts, idx, data = blocks[0]
    assert idx == 0
    # DC offset subtraction should yield values near zero
    assert np.max(np.abs(data)) < 0.01


def test_card_writer_reader_roundtrip_12bit():
    """Write then read a v2 (12-bit) .card block."""
    buf = io.StringIO()
    write_card_header(buf, bit_depth=12, sample_rate=6_000_000)

    # Generate a synthetic carrier
    carrier = generate_carrier(100_000, 6_000_000, 256)
    block = carrier.astype(np.complex64)
    card_writer(buf, 1.5, 0, block, bit_depth=12)

    buf.seek(0)
    blocks = list(card_reader(buf))

    assert len(blocks) == 1
    ts, idx, data = blocks[0]
    assert abs(ts - 1.5) < 0.001
    assert idx == 0
    assert len(data) == len(block)


def test_mock_device_capture_loop():
    """MockSDR can generate multiple blocks continuously."""
    dev = MockSDRDevice(
        sample_rate=6_000_000,
        carrier_freq_offset=100_000,
        carrier_amplitude=500.0,
        noise_amplitude=50.0
    )
    dev.open()

    blocks = []
    for _ in range(5):
        raw = dev.read_sync(512)
        complex_data = raw_to_complex(raw, bit_depth=12)
        blocks.append(complex_data)

    dev.close()

    assert len(blocks) == 5
    for block in blocks:
        assert block.dtype == np.complex64
        assert len(block) == 512


def test_signal_generator_carrier():
    """generate_carrier produces expected frequency content."""
    freq = 50_000.0
    fs = 6_000_000.0
    n = 1024
    carrier = generate_carrier(freq, fs, n)

    assert carrier.dtype == np.complex64
    assert len(carrier) == n

    # FFT should show peak at freq bin
    fft = np.abs(np.fft.fft(carrier))
    peak_bin = np.argmax(fft[:n // 2])
    expected_bin = int(freq / fs * n)
    assert abs(peak_bin - expected_bin) <= 2


def test_complex_to_int16_range():
    """complex_to_int16 should produce values in 12-bit range."""
    from tests.mocks.signal_generator import complex_to_int16
    signal = np.exp(1j * np.linspace(0, 2*np.pi, 64)).astype(np.complex64)
    raw = complex_to_int16(signal)
    assert raw.dtype == np.int16
    assert np.max(np.abs(raw)) <= 2047 + 1  # slight tolerance
