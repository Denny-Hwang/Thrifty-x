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
    """Airspy 12-bit normalization: divide by 2048 (12-bit signed full scale)."""

    def test_zero_input(self):
        """Zero input should give zero output."""
        data = np.zeros(4, dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(np.abs(result[0]), 0.0, atol=1e-6)

    def test_max_positive_12bit(self):
        """+2047 I (12-bit max) → real ≈ +1.0."""
        data = np.array([2047, 0], dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(result[0].real, 2047 / 2048.0, rtol=1e-5)
        np.testing.assert_allclose(result[0].imag, 0.0, atol=1e-5)

    def test_max_negative_12bit(self):
        """-2048 I (12-bit min) → real ≈ -1.0."""
        data = np.array([-2048, 0], dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        np.testing.assert_allclose(result[0].real, -1.0, rtol=1e-5)

    def test_int16_envelope_no_overflow(self):
        """FIR overshoot up to int16 limits must not corrupt the output dtype.

        libairspy can briefly emit values beyond the raw 12-bit range due to
        FIR filtering; conversion must still produce finite complex64 values.
        """
        data = np.array([32767, -32768], dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        assert np.isfinite(result).all()
        # Magnitude > 1 is acceptable here (FIR overshoot envelope).
        np.testing.assert_allclose(result[0].real, 32767 / 2048.0, rtol=1e-5)
        np.testing.assert_allclose(result[0].imag, -32768 / 2048.0, rtol=1e-5)

    def test_output_dtype(self):
        data = np.zeros(4, dtype=np.int16)
        result = raw_to_complex(data, bit_depth=12)
        assert result.dtype == np.complex64

    def test_invalid_bit_depth(self):
        data = np.zeros(4, dtype=np.int16)
        with pytest.raises(ValueError, match="Unsupported bit depth"):
            raw_to_complex(data, bit_depth=16)


class TestComplexToRaw12bit:
    """Inverse of raw_to_complex 12-bit: multiply by 2048, clip to int16."""

    def test_roundtrip_within_12bit_range(self):
        """Round-trip within 12-bit range is lossless."""
        original = np.array([100, -200, 500, -1000], dtype=np.int16)
        complex_vals = raw_to_complex(original, bit_depth=12)
        recovered = complex_to_raw(complex_vals, bit_depth=12)
        np.testing.assert_array_equal(original, recovered)

    def test_roundtrip_full_12bit_extremes(self):
        """±2048 (12-bit extremes) round-trip exactly."""
        original = np.array([-2048, 0, 0, 2047], dtype=np.int16)
        complex_vals = raw_to_complex(original, bit_depth=12)
        recovered = complex_to_raw(complex_vals, bit_depth=12)
        np.testing.assert_array_equal(original, recovered)


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
        for _ts, _idx, data in blocks:
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

    def test_v2_header_wins_over_stale_bit_depth_arg(self):
        """A #v2 header overrides a conflicting explicit bit_depth.

        Regression: the detect/analyze_detect CLIs always pass the
        configured bit_depth (default 8); a 12-bit Airspy card must
        still decode as 12-bit per its header, not as uint8 garbage
        of twice the length.
        """
        stream = self._make_v2_card()
        blocks = list(card_reader(stream, bit_depth=8))
        assert len(blocks) == 2
        for _, _, data in blocks:
            assert len(data) == 16  # 32 int16 = 16 complex samples

    def test_v1_headerless_uses_bit_depth_arg(self):
        """Headerless (v1) files honour the explicit bit_depth arg."""
        stream = self._make_v1_card()
        blocks = list(card_reader(stream, bit_depth=12))
        # 32 uint8 bytes reinterpreted as 16 int16 = 8 complex samples
        _, _, data = blocks[0]
        assert len(data) == 8


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


class TestCardReaderRobustness:
    """Regression tests for MINOR data-path findings: truncated tail
    lines, header-prefix tolerance, sample-rate mismatch warning, and
    the history=0 block_reader guard."""

    @staticmethod
    def _v2_lines(num_blocks=3, header='#v2 bit_depth=12 sample_rate=6000000\n'):
        lines = [header]
        for i in range(num_blocks):
            raw = np.full(32, i + 1, dtype=np.int16)
            encoded = base64.b64encode(raw.tobytes()).decode('ascii')
            lines.append(f"{float(i):.6f} {i} {encoded}\n")
        return lines

    def test_truncated_final_line_salvages_complete_blocks(self, caplog):
        import logging
        lines = self._v2_lines(3)
        # Simulate a power loss mid-write: chop the final line's payload.
        lines[-1] = lines[-1][:len(lines[-1]) // 2].rstrip() + '\n'
        stream = io.StringIO(''.join(lines))
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream))
        assert [idx for _, idx, _ in blocks] == [0, 1]
        assert any('truncated' in r.message or 'corrupt' in r.message
                   for r in caplog.records)

    def test_tab_separated_v2_header_recognized(self):
        stream = io.StringIO(''.join(
            self._v2_lines(1, header='#v2\tbit_depth=12\tsample_rate=6000000\n')))
        blocks = list(card_reader(stream))
        assert len(blocks) == 1
        # int16 payload of 32 values -> 16 complex samples (12-bit decode);
        # an 8-bit misread would have produced 32 samples.
        assert len(blocks[0][2]) == 16

    def test_bare_v2_header_recognized_as_header(self):
        # A bare '#v2' line has no metadata but must not decode as v1
        # data lines either; the file's data is still readable via the
        # bit_depth argument.
        lines = self._v2_lines(1, header='#v2\n')
        stream = io.StringIO(''.join(lines))
        blocks = list(card_reader(stream, bit_depth=12))
        assert len(blocks) == 1
        assert len(blocks[0][2]) == 16

    def test_sample_rate_mismatch_warns(self, caplog):
        import logging
        stream = io.StringIO(''.join(self._v2_lines(1)))
        with caplog.at_level(logging.WARNING):
            list(card_reader(stream, expected_sample_rate=2.4e6))
        assert any('sample_rate' in r.message for r in caplog.records)

    def test_sample_rate_match_no_warning(self, caplog):
        import logging
        stream = io.StringIO(''.join(self._v2_lines(1)))
        with caplog.at_level(logging.WARNING):
            list(card_reader(stream, expected_sample_rate=6e6))
        assert not any('captured at' in r.message for r in caplog.records)


class TestBlockReaderZeroHistory:
    def test_history_zero_keeps_block_size_constant(self):
        raw = np.zeros(8 * 2 * 4, dtype=np.uint8)  # 4 blocks of 8 pairs
        stream = io.BytesIO(raw.tobytes())
        blocks = list(block_reader(stream, size=8, history=0, bit_depth=8))
        assert len(blocks) == 4
        assert all(len(data) == 8 for _, _, data in blocks)


class TestV2HeaderMetadata:
    """Info-level findings: endian/block_size recorded in the header,
    endian mismatch warning, and first-header-wins for bit_depth."""

    def test_header_records_endian_and_block_size(self):
        buf = io.StringIO()
        write_card_header(buf, bit_depth=12, sample_rate=6_000_000,
                          block_size=16384)
        line = buf.getvalue()
        assert 'endian=little' in line
        assert 'block_size=16384' in line

    def test_header_block_size_optional(self):
        buf = io.StringIO()
        write_card_header(buf, bit_depth=12, sample_rate=6_000_000)
        assert 'block_size' not in buf.getvalue()

    def test_foreign_endian_warns(self, caplog):
        import logging
        raw = np.zeros(32, dtype=np.int16)
        encoded = base64.b64encode(raw.tobytes()).decode('ascii')
        stream = io.StringIO(
            '#v2 bit_depth=12 sample_rate=6000000 endian=big\n'
            f'0.000000 0 {encoded}\n')
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream))
        assert len(blocks) == 1
        assert any('endian' in r.message for r in caplog.records)

    def test_midfile_bit_depth_change_ignored(self, caplog):
        import logging
        raw12 = np.zeros(32, dtype=np.int16)
        enc12 = base64.b64encode(raw12.tobytes()).decode('ascii')
        stream = io.StringIO(
            '#v2 bit_depth=12 sample_rate=6000000\n'
            f'0.000000 0 {enc12}\n'
            '#v2 bit_depth=8 sample_rate=2400000\n'
            f'1.000000 1 {enc12}\n')
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream))
        # Both blocks decode as int16 (16 complex samples each); the
        # mid-file header did not switch the tail to 8-bit (32 samples).
        assert [len(b[2]) for b in blocks] == [16, 16]
        assert any('mid-file' in r.message for r in caplog.records)


class TestCorruptCardSummary:
    """R7 polish: capped per-line warnings + end-of-stream summary, and
    short-block detection via the header's recorded block_size."""

    @staticmethod
    def _line(idx, n_int16, fill=1):
        raw = np.full(n_int16, fill, dtype=np.int16)
        encoded = base64.b64encode(raw.tobytes()).decode('ascii')
        return f"{float(idx):.6f} {idx} {encoded}\n"

    def test_all_corrupt_file_logs_error_summary(self, caplog):
        import logging
        lines = ['#v2 bit_depth=12 sample_rate=6000000\n']
        for i in range(8):
            good = self._line(i, 32)
            lines.append(good[:len(good) // 2].rstrip() + '\n')
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(io.StringIO(''.join(lines))))
        assert blocks == []
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any('NO valid blocks' in r.message for r in errors)
        # Per-line warnings are capped (5) + 1 summary, not 8 warnings.
        warns = [r for r in caplog.records
                 if 'corrupt/truncated .card line for block' in r.message]
        assert len(warns) == 5

    def test_short_block_caught_by_header_block_size(self, caplog):
        import logging
        lines = ['#v2 bit_depth=12 sample_rate=6000000 block_size=16\n',
                 self._line(0, 32),   # 16 complex samples: OK
                 self._line(1, 12)]   # 6 samples: "lucky" truncation
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(io.StringIO(''.join(lines))))
        assert [idx for _, idx, _ in blocks] == [0]
        assert any('block_size=16' in r.message for r in caplog.records)

    def test_no_block_size_header_accepts_any_length(self):
        lines = ['#v2 bit_depth=12 sample_rate=6000000\n',
                 self._line(0, 12)]
        blocks = list(card_reader(io.StringIO(''.join(lines))))
        assert len(blocks) == 1 and len(blocks[0][2]) == 6


class TestHeaderEdgeWarnings:
    """Nit polish: v1+v2 concatenation warning and garbage header
    values tolerated with a warning instead of raising."""

    @staticmethod
    def _v1_line(idx, n_uint8=32):
        raw = np.zeros(n_uint8, dtype=np.uint8)
        encoded = base64.b64encode(raw.tobytes()).decode('ascii')
        return f"{float(idx):.6f} {idx} {encoded}\n"

    @staticmethod
    def _v2_line(idx, n_int16=32):
        raw = np.zeros(n_int16, dtype=np.int16)
        encoded = base64.b64encode(raw.tobytes()).decode('ascii')
        return f"{float(idx):.6f} {idx} {encoded}\n"

    def test_v1_then_v2_concat_warns_and_switches(self, caplog):
        import logging
        stream = io.StringIO(
            self._v1_line(0, n_uint8=24)
            + '#v2 bit_depth=12 sample_rate=6000000\n'
            + self._v2_line(1))
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream))
        # v1 section decodes as 8-bit (24 bytes -> 12 complex samples),
        # v2 tail as 12-bit (32 int16 -> 16 complex) — per-section
        # correct, but no longer silent.
        assert [len(b[2]) for b in blocks] == [12, 16]
        assert any('after headerless' in r.message for r in caplog.records)

    def test_garbage_bit_depth_ignored_with_warning(self, caplog):
        import logging
        stream = io.StringIO('#v2 bit_depth=twelve sample_rate=6000000\n'
                             + self._v1_line(0))
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream))
        assert len(blocks) == 1          # falls back to 8-bit decoding
        assert any('unparseable' in r.message for r in caplog.records)

    def test_garbage_sample_rate_ignored_with_warning(self, caplog):
        import logging
        stream = io.StringIO('#v2 bit_depth=12 sample_rate=fast\n'
                             + self._v2_line(0))
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream, expected_sample_rate=6e6))
        assert len(blocks) == 1
        assert any('unparseable' in r.message for r in caplog.records)

    def test_v1_then_v2_concat_switches_even_with_cli_fallback(self, caplog):
        # Regression (Codex review on PR #68): detect/analyze_detect
        # always pass an integer bit_depth fallback, which must NOT be
        # confused with a header-derived width — the FIRST real header
        # still wins at the v1+v2 seam.
        import logging
        stream = io.StringIO(
            self._v1_line(0, n_uint8=24)
            + '#v2 bit_depth=12 sample_rate=6000000\n'
            + self._v2_line(1))
        with caplog.at_level(logging.WARNING):
            blocks = list(card_reader(stream, bit_depth=8))
        assert [len(b[2]) for b in blocks] == [12, 16]
        assert any('after headerless' in r.message for r in caplog.records)
