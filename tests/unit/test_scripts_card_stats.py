# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/card_stats.py reports magnitudes against ADC full scale."""

import importlib.util
import io
from pathlib import Path

import numpy as np

from thriftyx import block_data

_SPEC = importlib.util.spec_from_file_location(
    'card_stats', Path(__file__).parents[2] / 'scripts' / 'card_stats.py')
card_stats = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(card_stats)


def _card(amplitude, bit_depth=12, n=4096):
    tone = amplitude * np.exp(2j * np.pi * 0.01 * np.arange(n))
    buf = io.StringIO()
    block_data.write_card_header(buf, bit_depth=bit_depth,
                                 sample_rate=6_000_000, block_size=n)
    block_data.card_writer(buf, 0.0, 0, tone.astype(np.complex64),
                           bit_depth=bit_depth)
    buf.seek(0)
    return buf


def test_half_scale_airspy_tone():
    stats = card_stats.card_stats(_card(0.5))
    assert stats['blocks'] == 1
    np.testing.assert_allclose(stats['peak_magnitude'], 0.5, atol=1e-3)
    np.testing.assert_allclose(stats['peak_magnitude']
                               * stats['full_scale_raw'], 8192, atol=20)
    assert stats['samples_at_limit'] == 0


def test_saturation_is_counted():
    stats = card_stats.card_stats(_card(3.0))  # beyond the int16 container
    assert stats['samples_at_limit'] > 0


def test_cli_prints_dbfs(capsys, tmp_path):
    path = tmp_path / 'rx0.card'
    path.write_text(_card(0.5).getvalue())
    assert card_stats.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "6 MSPS" in out and "-6.0 dBFS" in out
