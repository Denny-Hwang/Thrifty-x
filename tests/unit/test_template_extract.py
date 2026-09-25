# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""template_extract: which block it extracts from, and its output file."""

import io
import os
import sys
import types

import numpy as np
import pytest

from thriftyx import block_data, gold, template_extract
from thriftyx.exceptions import ConfigValidationError, DetectionError
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
                  _detection(3, 540.0, -0.3),
                  _detection(7, 30.0, 0.05), _detection(8, 520.0, 0.45),
                  _detection(9, 25.0, 0.0)]
    with pytest.raises(DetectionError, match='part of a burst'):
        template_extract.best_detection(iter(detections), 0.2)

    detections.append(_detection(12, 530.0, 0.1))
    _, result = template_extract.best_detection(iter(detections), 0.2)
    assert result.block == 12


def test_a_weaker_transmitters_complete_burst_is_extracted():
    """Candidates used to need half the strongest detection's energy,
    which also rejected every burst of a weaker transmitter sending the
    same code (told apart by carrier frequency), blaming a block edge."""
    detections = [_detection(block, 537.0, -0.48)
                  for block in (1, 5, 9, 13)]
    detections.insert(3, _detection(11, 215.0, -0.10))
    _, result = template_extract.best_detection(iter(detections), 0.2)
    assert result.block == 11


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


def test_output_symlink_is_written_through(monkeypatch, tmp_path):
    """The output was saved to a temporary file and renamed over -o: a
    symlink became a regular file and the file's mode was reset."""
    base = resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE)
    np.save(tmp_path / 'template.npy', base)
    os.chmod(tmp_path / 'template.npy', 0o640)
    os.symlink('template.npy', tmp_path / 'link.npy')
    _extract(monkeypatch, tmp_path, '-o', 'link.npy')
    assert os.readlink(tmp_path / 'link.npy') == 'template.npy'
    assert not np.array_equal(np.load(tmp_path / 'template.npy'), base)
    assert (tmp_path / 'template.npy').stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'link.npy', 'rx0.card', 'template.npy']


def test_missing_output_directory_fails_first(monkeypatch, tmp_path):
    """It was reported after the whole extraction, under the temporary
    file's name."""
    np.save(tmp_path / 'template.npy',
            resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE))
    # Reading the card at all is too late.
    monkeypatch.setattr(template_extract.detect, 'open_card', None)
    with pytest.raises(FileNotFoundError) as exc_info:
        _extract(monkeypatch, tmp_path, '-o', 'nodir/x.npy')
    assert exc_info.value.filename == 'nodir/x.npy'


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


def test_window_outside_the_fft_is_a_config_error(monkeypatch, tmp_path):
    """Used to end in a ValueError traceback from the first block."""
    np.save(tmp_path / 'template.npy',
            resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE))
    with pytest.raises(ConfigValidationError, match='carrier_window'):
        _extract(monkeypatch, tmp_path, '--carrier-window', '1000-40000')


def test_template_is_checked_at_the_configured_chip_rate(monkeypatch,
                                                         tmp_path):
    """chip_rate was not among template_extract's settings, so its
    template check assumed 0.999707M: a template for the configured chip
    rate was reported to match no code."""
    np.save(tmp_path / 'template.npy', np.ones(10))
    rates = []

    def load_template(_path, _sample_rate, chip_rate=None, report=None):
        rates.append(chip_rate)
        raise DetectionError("stop here")

    monkeypatch.setattr(template_extract.detect, 'load_template',
                        load_template)
    (tmp_path / 'rx.cfg').write_text('chip_rate: 1.05M\n')
    with pytest.raises(DetectionError, match='stop here'):
        _extract(monkeypatch, tmp_path, '-c', 'rx.cfg')
    with pytest.raises(DetectionError, match='stop here'):
        _extract(monkeypatch, tmp_path, '--chip-rate', '1.02M')
    assert rates == [1.05e6, 1.02e6]
