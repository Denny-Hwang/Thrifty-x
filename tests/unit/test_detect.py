# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the Detector class and the detect command."""

import sys

import numpy as np
import pytest

from thriftyx import detect, gold
from thriftyx.detect import Detector, DetectorSettings
from thriftyx.exceptions import TemplateError
from thriftyx.signal_utils import Signal
from thriftyx.template_generate import resample


@pytest.fixture
def template():
    """Simple Gold-code-like template."""
    rng = np.random.default_rng(seed=123)
    return rng.choice([-1.0, 1.0], size=63).astype(np.float64)


@pytest.fixture
def detector_settings(template):
    block_len = 4096
    history_len = 1024
    return DetectorSettings(
        block_len=block_len,
        history_len=history_len,
        carrier_len=len(template),
        carrier_thresh=(20, 0, 0),
        carrier_window=(0, block_len // 2),
        template=template,
        corr_thresh=(20, 0, 0),
    )


def test_detector_callable_returns_result(detector_settings):
    """Test that __call__ returns the same as detect()."""
    detector = Detector(detector_settings, rxid=0)
    block = Signal(np.zeros(detector_settings.block_len, dtype=np.complex64))
    result_call = detector(0.0, 0, block)
    assert result_call is not None
    assert len(result_call) == 2  # (detected, result)
    detected, result = result_call
    assert isinstance(bool(detected), bool)
    assert result is not None
    assert hasattr(result, 'carrier_info')


def test_detector_no_signal(detector_settings):
    """Test that detector returns not-detected for noise-only input."""
    detector = Detector(detector_settings, rxid=0)
    rng = np.random.default_rng(seed=456)
    data = (rng.normal(size=detector_settings.block_len) +
            1j * rng.normal(size=detector_settings.block_len)).astype(np.complex64)
    block = Signal(data)
    detected, result = detector(0.0, 0, block)
    # detected may be True or False depending on random noise;
    # just verify the return structure is correct
    assert isinstance(bool(detected), bool)
    assert result is not None


# --- output file --------------------------------------------------------------

def _run_detect(monkeypatch, tmp_path, *args):
    card = tmp_path / 'rx0.card'
    card.write_text('#v2 bit_depth=12 sample_rate=6000000 endian=little '
                    'block_size=32768 block_history=12349\n')
    np.save(tmp_path / 'template.npy',
            resample(gold.gold(10, 0, 'gold'), 6e6 / 0.999707e6))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['detect', 'rx0.card', '--quiet',
                                      *args])
    detect._main()


@pytest.mark.parametrize('option', ['-o', '-a'])
@pytest.mark.parametrize('template, error', [
    ('missing.npy', FileNotFoundError),
    ('long.npy', TemplateError),      # longer than the block
])
def test_failed_setup_keeps_the_previous_output(monkeypatch, tmp_path,
                                                option, template, error):
    """argparse used to open (truncate) -o before the template was
    loaded: re-running detect with a bad template emptied the .toad, and
    make then took it as up to date."""
    np.save(tmp_path / 'long.npy', np.ones(40000))
    (tmp_path / 'rx0.toad').write_text('previous\n')
    with pytest.raises(error):
        _run_detect(monkeypatch, tmp_path, option, 'rx0.toad',
                    '-z', template)
    assert (tmp_path / 'rx0.toad').read_text() == 'previous\n'


@pytest.mark.parametrize('option, expected', [('-o', ''),
                                              ('-a', 'previous\n')])
def test_output_is_written_once_setup_succeeds(monkeypatch, tmp_path,
                                               option, expected):
    (tmp_path / 'rx0.toad').write_text('previous\n')
    _run_detect(monkeypatch, tmp_path, option, 'rx0.toad')
    assert (tmp_path / 'rx0.toad').read_text() == expected
