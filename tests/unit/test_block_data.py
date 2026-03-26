# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for block_data module — 8-bit legacy and 12-bit Airspy."""

import io
import base64

import numpy as np
import pytest

from thriftyx.block_data import (raw_to_complex, complex_to_raw, block_reader,
                                   card_reader, card_writer, write_card_header)


# ──────────────────────────────────────────────────────────────────────────────
# 8-bit legacy tests (preserve all original behavior)
# ──────────────────────────────────────────────────────────────────────────────

class TestRawToComplex8bit:
    def test_dc_offset(self):
        """Input of 127 should map close to 0."""
        data = np.full(2, 127, dtype=np.uint8)
        result = raw_to_complex(data, bit_depth=8)
        np.testing.assert_allclose(result[0], -0.4/128 + (-0.4/128)*1j,
                                    atol=1e-4)

    def test_max_value(self):
        """Input of 255 should give positive values."""
        data = np.array([255, 255], dtype=np.uint8)
        result = raw_to_complex(data, bit_depth=8)
        assert result[0].real > 0

    def test_output_dtype(self):
        data = np.zeros(4, dtype=np.uint8)
        result = raw_to_complex(data, bit_depth=8)
        assert result.dtype == np.complex64


class TestRawToComplex12bit:
    def test_zero_input(self):
        """Zero input should give zero output."""
        data = np.zeros(4, dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(np.abs(result[0]), 0.0, atol=1e-6)

    def test_max_positive(self):
        """2047 I, 0 Q → real ≈ 1.0, imag ≈ 0."""
        data = np.array([2047, 0], dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(result[0].real, 2047/2048, rtol=1e-5)
        np.testing.assert_allclose(result[0].imag, 0.0, atol=1e-5)

    def test_max_negative(self):
        """-2048 I → real ≈ -1.0."""
        data = np.array([-2048, 0], dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(result[0].real, -1.0, rtol=1e-5)

    def test_output_dtype(self):
        data = np.zeros(4, dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        assert result.dtype == np.complex64

    def test_invalid_bit_depth(self):
        data = np.zeros(4, dtype=np.int16)
        with pytest.raises(ValueError, match="Unsupported bit depth"):
            raw_to_complex(data, bit_depth=16)


class TestComplexToRaw12bit:
    def test_roundtrip(self):
        """12-bit conversion round-trip should be lossless."""
        original = np.array([100, -200, 500, -1000], dtype=np.int16)
        complex_vals = raw_to_complex(original, bit_depth=12)
        recovered = complex_to_raw(complex_vals, bit_depth=12)
        np.testing.assert_array_equal(original, recovered)

    def test_roundtrip_8bit(self):
        """8-bit conversion round-trip."""
        original = np.array([50, 100, 200, 127], dtype=np.uint8)
        complex_vals = raw_to_complex(original, bit_depth=8)
        recovered = complex_to_raw(complex_vals, bit_depth=8)
        np.testing.assert_array_almost_equal(original, recovered, decimal=0)


class TestBlockReader:
    def test_history_overlap(self):
        """Blocks should contain 'history' samples from previous block."""
        size = 8
        history = 4
        # Create enough raw data for 3 blocks worth of new samples
        num_new = size - history
        raw_samples = np.zeros(num_new * 3 * 2, dtype=np.int16)
        stream = io.BytesIO(raw_samples.tobytes())
        blocks = list(block_reader(stream, size, history, bit_depth=12))
        assert len(blocks) >= 2
        # Each block should have 'size' samples
        for ts, idx, data in blocks:
            assert len(data) == size


class TestCardReader:
    def _make_v1_card(self, num_blocks=2):
        """Create a v1 .card file in memory."""
        lines = []
        for i in range(num_blocks):
            timestamp = float(i)
            raw = np.zeros(32, dtype=np.uint8)
            encoded = base64.b64encode(raw.tobytes()).decode('ascii')
            lines.append(f"{timestamp:.6f} {i} {encoded}\n")
        return io.StringIO(''.join(lines))

    def _make_v2_card(self, num_blocks=2):
        """Create a v2 .card file in memory."""
        lines = ['#v2 bit_depth=12 sample_rate=6000000\n']
        for i in range(num_blocks):
            timestamp = float(i)
            raw = np.zeros(32, dtype=np.int16)
            encoded = base64.b64encode(raw.tobytes()).decode('ascii')
            lines.append(f"{timestamp:.6f} {i} {encoded}\n")
        return io.StringIO(''.join(lines))

    def test_v1_backward_compat(self):
        """v1 .card files (uint8 RTL-SDR) still readable."""
        stream = self._make_v1_card()
        blocks = list(card_reader(stream))
        assert len(blocks) == 2
        ts, idx, data = blocks[0]
        assert idx == 0

    def test_v2_format(self):
        """v2 .card files (int16 Airspy) readable."""
        stream = self._make_v2_card()
        blocks = list(card_reader(stream))
        assert len(blocks) == 2

    def test_auto_detect_v1(self):
        """Auto-detect v1 format (no header → 8-bit)."""
        stream = self._make_v1_card()
        blocks = list(card_reader(stream, bit_depth=None))
        assert len(blocks) == 2

    def test_auto_detect_v2(self):
        """Auto-detect v2 format from header."""
        stream = self._make_v2_card()
        blocks = list(card_reader(stream, bit_depth=None))
        assert len(blocks) == 2

    def test_explicit_bit_depth_override(self):
        """Explicit bit_depth overrides auto-detection."""
        stream = self._make_v1_card()
        # Forcing 8-bit on v1 file should still work
        blocks = list(card_reader(stream, bit_depth=8))
        assert len(blocks) == 2


class TestCardWriter:
    def test_roundtrip_v2(self):
        """Write then read a v2 .card block."""
        buf = io.StringIO()
        write_card_header(buf, bit_depth=12, sample_rate=6_000_000)

        block = np.zeros(16, dtype=np.complex64)
        card_writer(buf, 1.0, 0, block, bit_depth=12)

        buf.seek(0)
        blocks = list(card_reader(buf))
        assert len(blocks) == 1
        ts, idx, data = blocks[0]
        assert idx == 0
