# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""The .card v2 header drives detection settings.

A card records how it was captured (sample rate, block geometry, bit
depth).  ``detect`` must process it with those values even when the
config says otherwise; the README workflow (capture at 6 MSPS, then
``thriftyx detect rx0.card`` with no config) used to crash with a
block-length ValueError traceback.
"""

import base64
import io
import logging
import sys

import numpy as np
import pytest

from thriftyx import block_data, detect, settings
from thriftyx.exceptions import FileFormatError


def _card_text(sample_rate=6_000_000, block_size=32768, block_history=12278,
               n_blocks=2, header=True):
    buf = io.StringIO()
    if header:
        block_data.write_card_header(buf, bit_depth=12,
                                     sample_rate=sample_rate,
                                     block_size=block_size,
                                     block_history=block_history)
    rng = np.random.default_rng(1)
    for idx in range(n_blocks):
        iq = rng.integers(-2048, 2047, size=2 * block_size, dtype=np.int16)
        buf.write("{}.000000 {} {}\n".format(
            1_700_000_000 + idx, idx,
            base64.b64encode(iq.tobytes()).decode()))
    return buf.getvalue()


def _config(explicit=(), **overrides):
    values = settings.load()
    values.update(overrides)
    config = settings.Namespace(values)
    config.explicit_keys = frozenset(explicit)
    return config


# ---------------------------------------------------------------------------
# peek_card_header
# ---------------------------------------------------------------------------

def test_write_card_header_records_block_history():
    buf = io.StringIO()
    block_data.write_card_header(buf, bit_depth=12, sample_rate=6_000_000,
                                 block_size=32768, block_history=12278)
    assert buf.getvalue() == ("#v2 bit_depth=12 sample_rate=6000000 "
                              "endian=little block_size=32768 "
                              "block_history=12278\n")


def test_peek_returns_header_without_consuming_blocks():
    text = _card_text()
    header, stream = block_data.peek_card_header(io.StringIO(text))
    assert header['sample_rate'] == '6000000'
    assert header['block_history'] == '12278'
    peeked = list(block_data.card_reader(stream))
    direct = list(block_data.card_reader(io.StringIO(text)))
    assert [b[1] for b in peeked] == [b[1] for b in direct] == [0, 1]
    np.testing.assert_array_equal(peeked[1][2], direct[1][2])


def test_peek_headerless_card_replays_first_data_line():
    text = _card_text(header=False)
    header, stream = block_data.peek_card_header(io.StringIO(text))
    assert header == {}
    assert len(list(block_data.card_reader(stream, bit_depth=12))) == 2


def test_peek_skips_leading_comments_and_reads_bytes_streams():
    text = "# produced by a tool\n" + _card_text()
    header, stream = block_data.peek_card_header(
        io.BytesIO(text.encode()))
    assert header['block_size'] == '32768'
    assert len(list(block_data.card_reader(stream))) == 2


# ---------------------------------------------------------------------------
# settings.apply_card_header
# ---------------------------------------------------------------------------

def test_header_values_replace_defaults():
    header = {'sample_rate': '6000000', 'block_size': '32768',
              'block_history': '12278', 'bit_depth': '12'}
    config = settings.apply_card_header(_config(), header)
    assert (config.sample_rate, config.block_size, config.block_history,
            config.bit_depth) == (6e6, 32768, 12278, 12)
    assert {'sample_rate', 'block_size', 'block_history',
            'bit_depth'} <= config.explicit_keys


def test_header_overrides_explicit_setting_with_warning(caplog):
    config = _config(explicit={'sample_rate'}, sample_rate=3e6)
    with caplog.at_level(logging.WARNING):
        config = settings.apply_card_header(
            config, {'sample_rate': '6000000', 'block_size': '32768',
                     'block_history': '12278'})
    assert config.sample_rate == 6e6
    assert "sample_rate=6000000 recorded in the .card header overrides " \
           "the configured sample_rate=3000000" in caplog.text


def test_matching_explicit_setting_does_not_warn(caplog):
    config = _config(explicit={'block_size'}, block_size=32768)
    with caplog.at_level(logging.WARNING):
        settings.apply_card_header(config, {'block_size': '32768'})
    assert "overrides" not in caplog.text


def test_old_header_without_history_rederives_default_history():
    """v2 files written before block_history was recorded."""
    config = settings.apply_card_header(
        _config(), {'sample_rate': '6000000', 'block_size': '32768'})
    assert config.block_history == 12278  # capture-side default at 6 MSPS


def test_explicit_history_survives_old_header():
    config = _config(explicit={'block_history'}, block_history=15000)
    config = settings.apply_card_header(
        config, {'sample_rate': '6000000', 'block_size': '32768'})
    assert config.block_history == 15000


def test_unknown_rate_zero_is_ignored():
    """fastcapture writes sample_rate=0 when re-emitting a file input."""
    config = settings.apply_card_header(
        _config(sample_rate=6e6), {'sample_rate': '0'})
    assert config.sample_rate == 6e6


def test_empty_header_returns_config_unchanged():
    config = _config()
    assert settings.apply_card_header(config, {}) is config


# ---------------------------------------------------------------------------
# Detector / CLI
# ---------------------------------------------------------------------------

def test_block_length_mismatch_is_a_file_format_error():
    template = np.ones(64, dtype=np.float32)
    dsettings = detect.DetectorSettings(
        block_len=1024, history_len=128, carrier_len=64,
        carrier_thresh=(0, 15, 0), carrier_window=(0, 512),
        template=template, corr_thresh=(0, 15, 0))
    detector = detect.Detector(dsettings)
    with pytest.raises(FileFormatError, match="set --bit-depth 12"):
        detector(0.0, 0, np.zeros(2048, dtype=np.complex64))


def _run_detect(monkeypatch, tmp_path, card_text, *extra, template_len=6139):
    card = tmp_path / "rx0.card"
    card.write_text(card_text)
    toad = tmp_path / "rx0.toad"
    template = tmp_path / "template.npy"
    np.save(template, np.ones(template_len, dtype=np.float32))
    monkeypatch.chdir(tmp_path)  # no detector.cfg in scope
    monkeypatch.setattr(sys, 'argv', [
        'detect', str(card), '-o', str(toad), '-z', str(template),
        '--quiet', *extra])
    detect._main()


def test_detect_cli_processes_6msps_card_without_config(monkeypatch,
                                                         tmp_path):
    """The README workflow: capture at 6 MSPS, detect with no config."""
    _run_detect(monkeypatch, tmp_path, _card_text())


def test_detect_cli_reports_headerless_mismatch_cleanly(monkeypatch,
                                                         tmp_path):
    with pytest.raises(FileFormatError, match="block 0 holds"):
        _run_detect(monkeypatch, tmp_path, _card_text(header=False),
                    '--bit-depth', '12', template_len=2455)
