# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the Detector class and the detect command."""

import sys

import numpy as np
import pytest

from thriftyx import block_data, detect, gold
from thriftyx.detect import Detector, DetectorSettings
from thriftyx.exceptions import (ConfigValidationError, FileFormatError,
                                 TemplateError)
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
        carrier_thresh=(0, 15, 0),        # 15*snr, the default
        carrier_window=(0, block_len // 2),
        template=template,
        corr_thresh=(0, 15, 0),
    )


def _noise(settings, rng, sigma=1.0):
    n = settings.block_len
    return sigma * (rng.normal(size=n) + 1j * rng.normal(size=n))


def test_detector_callable_is_detect(detector_settings, template):
    """__call__ gives what detect() gives, on a block with a burst: an
    on-off keyed carrier holding the template's chips at sample START."""
    start = 2000
    rng = np.random.default_rng(seed=789)
    data = _noise(detector_settings, rng, sigma=0.05)
    n = np.arange(start, start + len(template))
    data[n] += (template > 0) * np.exp(2j * np.pi * 300 / 4096 * n)
    block = Signal(data.astype(np.complex64))
    detector = Detector(detector_settings, rxid=4)

    called = detector(1.5, 3, block)
    direct = detector.detect(1.5, 3, block)

    assert called[0] and direct[0]
    assert vars(called[1]) == vars(direct[1])
    result = called[1]
    assert (result.timestamp, result.block, result.rxid) == (1.5, 3, 4)
    new_len = detector_settings.block_len - detector_settings.history_len
    assert result.soa == pytest.approx(3 * new_len + start, abs=0.5)


def test_detector_no_signal(detector_settings):
    """Noise only: nothing detected with the default 15*snr thresholds
    (the constant threshold 20 the test used to set detects this very
    noise, and the test accepted either outcome)."""
    detector = Detector(detector_settings, rxid=0)
    rng = np.random.default_rng(seed=456)
    block = Signal(_noise(detector_settings, rng).astype(np.complex64))
    detected, result = detector(0.0, 0, block)
    assert not detected
    assert result.carrier_info is not None
    assert result.corr_info is None and result.soa is None


# --- output file --------------------------------------------------------------

def _run_detect(monkeypatch, tmp_path, *args, blocks=(), header=True):
    """Run detect on a card of *blocks*: a 12-bit v2 card, or without
    *header* an 8-bit v1 card, given its geometry as options."""
    card = tmp_path / 'rx0.card'
    with open(card, 'w') as stream:
        if header:
            stream.write('#v2 bit_depth=12 sample_rate=6000000 '
                         'endian=little block_size=32768 '
                         'block_history=12349\n')
        else:
            args += ('-s', '6M', '-b', '32768', '-y', '12349')
        for idx, block in enumerate(blocks):
            block_data.card_writer(stream, 100.0 + idx, idx, block,
                                   bit_depth=12 if header else 8)
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


def _card_noise(size=32768):
    rng = np.random.default_rng(size)
    return (0.01 * (rng.normal(size=size) + 1j * rng.normal(size=size))
            ).astype(np.complex64)


@pytest.mark.parametrize('option', ['-o', '-a'])
def test_first_block_failure_keeps_the_previous_output(monkeypatch,
                                                       tmp_path, option):
    """-o was opened before the first block: a card failing there (a
    12-bit card that lost its header, read as 8-bit, has blocks twice
    too long) emptied the previous run's .toad."""
    (tmp_path / 'rx0.toad').write_text('previous\n')
    with pytest.raises(FileFormatError, match='block 0 holds 65536'):
        _run_detect(monkeypatch, tmp_path, option, 'rx0.toad',
                    blocks=[_card_noise(2 * 32768)], header=False)
    assert (tmp_path / 'rx0.toad').read_text() == 'previous\n'


def test_empty_input_keeps_the_previous_output(monkeypatch, tmp_path,
                                               caplog):
    """`capture - -c bad.cfg | detect - -o rx0.toad`: a capture that
    fails at once sends nothing, and detect emptied rx0.toad."""
    (tmp_path / 'rx0.toad').write_text('previous\n')
    _run_detect(monkeypatch, tmp_path, '-o', 'rx0.toad', header=False)
    assert (tmp_path / 'rx0.card').stat().st_size == 0
    assert (tmp_path / 'rx0.toad').read_text() == 'previous\n'
    assert 'input is empty' in caplog.text


def test_output_streams_once_the_first_block_passed(monkeypatch, tmp_path):
    (tmp_path / 'rx0.toad').write_text('previous\n')
    with pytest.raises(FileFormatError, match='block 1 holds'):
        _run_detect(monkeypatch, tmp_path, '-o', 'rx0.toad',
                    blocks=[_card_noise(), _card_noise(1000)], header=False)
    assert (tmp_path / 'rx0.toad').read_text() == ''


@pytest.mark.parametrize('option', ['-o', '-a'])
def test_window_outside_the_fft_is_a_config_error(monkeypatch, tmp_path,
                                                  option):
    """A carrier bin the detector cannot index raised ValueError, with a
    traceback, on the first block -- after -o emptied the .toad."""
    (tmp_path / 'rx0.toad').write_text('previous\n')
    with pytest.raises(ConfigValidationError, match='carrier_window'):
        _run_detect(monkeypatch, tmp_path, option, 'rx0.toad',
                    '--carrier-window', '1000-40000', blocks=[_card_noise()])
    assert (tmp_path / 'rx0.toad').read_text() == 'previous\n'
