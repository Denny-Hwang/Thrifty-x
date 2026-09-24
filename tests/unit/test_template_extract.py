# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""template_extract: which block it extracts from, and its output file."""

import io
import sys
import types

import numpy as np
import pytest

from thriftyx import block_data, gold, template_extract
from thriftyx.exceptions import DetectionError
from thriftyx.template_generate import resample

FS = 6_000_000
CHIP_RATE = 0.999707e6
BLOCK, HISTORY = 32768, 12349


def _detection(block, energy, offset):
    corr_info = types.SimpleNamespace(energy=energy, offset=offset, sample=0)
    result = types.SimpleNamespace(block=block, corr_info=corr_info)
    return True, result, np.full(4, block, dtype=complex), None


def test_block_edge_partial_is_never_extracted():
    """The block before or after a burst detects it from part of the
    code at a few percent of its energy.  When no complete burst had a
    small enough offset, that partial used to be extracted -- a template
    starting thousands of chips into the code, reported as a success."""
    detections = [_detection(1, 550.0, 0.4), _detection(2, 46.0, -0.15),
                  _detection(3, 540.0, -0.3)]
    with pytest.raises(DetectionError, match='complete burst'):
        template_extract.best_detection(iter(detections), 0.2)

    detections.append(_detection(4, 530.0, 0.1))
    _, result = template_extract.best_detection(iter(detections), 0.2)
    assert result.block == 4


def _card_with_one_burst():
    rng = np.random.default_rng(7)
    # On/off keyed exactly as the template samples the code, so the
    # correlation peak falls on a whole sample.
    keyed = (resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE) + 1) / 2
    block = (rng.normal(0, 0.01, BLOCK)
             + 1j * rng.normal(0, 0.01, BLOCK)).astype(np.complex64)
    start = HISTORY + 1500
    n = np.arange(len(keyed))
    block[start:start + len(keyed)] += (
        0.1 * keyed * np.exp(2j * np.pi * 50e3 * n / FS))
    buf = io.StringIO()
    block_data.write_card_header(buf, bit_depth=12, sample_rate=FS,
                                 block_size=BLOCK, block_history=HISTORY)
    block_data.card_writer(buf, 1_700_000_000.0, 5, block, bit_depth=12)
    return buf.getvalue()


def _extract(monkeypatch, tmp_path, *args):
    (tmp_path / 'rx0.card').write_text(_card_with_one_burst())
    monkeypatch.chdir(tmp_path)  # no detector.cfg; header supplies geometry
    monkeypatch.setattr(sys, 'argv', ['template_extract', 'rx0.card',
                                      *args])
    template_extract._main()


def test_output_may_be_the_input_template(monkeypatch, tmp_path):
    """-o was opened (truncated) by argparse before the template was
    read: `-o template.npy` destroyed the working template."""
    base = resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE)
    np.save(tmp_path / 'template.npy', base)
    _extract(monkeypatch, tmp_path, '-o', 'template.npy')
    extracted = np.load(tmp_path / 'template.npy')
    assert len(extracted) == len(base)
    assert not np.array_equal(extracted, base)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'rx0.card', 'template.npy']


def test_failed_extraction_keeps_the_previous_output(monkeypatch, tmp_path):
    np.save(tmp_path / 'template.npy',
            resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE))
    (tmp_path / 'captured.npy').write_bytes(b'previous')
    with pytest.raises(DetectionError):
        _extract(monkeypatch, tmp_path, '-u', '100000*snr',
                 '-o', 'captured.npy')
    assert (tmp_path / 'captured.npy').read_bytes() == b'previous'
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'captured.npy', 'rx0.card', 'template.npy']
