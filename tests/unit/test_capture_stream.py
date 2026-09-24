# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""HAL capture: block indices, timestamps, header and output lifecycle.

TDOA turns ``block_idx`` into sample-of-arrival and matches receivers by
card timestamp, so both must describe when the samples were taken --
not how many blocks happened to be processed, or when.
"""

import base64
import io
import os
import signal
import sys
import time

import numpy as np
import pytest

import thriftyx.airspy_capture as ac
from thriftyx.airspy_capture import CardSink, _capture_airspy
from thriftyx.exceptions import (EXIT_CONFIG, ConfigValidationError,
                                 DeviceConfigError)
from thriftyx.settings import Namespace
from tests.mocks.scripted_device import ScriptedSDRDevice


def _config(**overrides):
    base = {
        'device_type': 'airspy_mini',
        'sample_rate': 6_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 8,
        'block_history': 2,
        'capture_skip': 0,
        'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 0.0, 0.0),
        'lna_gain': 0, 'mixer_gain': 0, 'vga_gain': 0,
        'bias_tee': False, 'gain_mode': 'manual', 'combined_gain': 0,
        'lna_agc': False, 'mixer_agc': False, 'ppm': 0.0,
        'packing': False,
    }
    base.update(overrides)
    return Namespace(base)


def _use(monkeypatch, device, detect=True):
    monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                        lambda _device_type, **_kwargs: device)
    calls = []

    def _detect(*_a, **_k):
        calls.append(1)
        return (detect, 1, 10.0, 1.0)

    monkeypatch.setattr(ac, 'carrier_detect_block', _detect)
    return calls


def _cards(text):
    return [line.split() for line in text.splitlines()
            if line and not line.startswith('#')]


def _blocks(n, start=1):
    # new_samples = 6 pairs = 12 int16 values per read
    return [np.full(12, start + i, dtype=np.int16) for i in range(n)]


def test_indices_stay_consecutive_after_a_drop(monkeypatch, capsys):
    """The HAL zero-fills lost samples, so block k is still k blocks into
    the run.  Adding the drop count to the index (the old behaviour)
    shifted every later SoA by a fraction of a block."""
    device = ScriptedSDRDevice(_blocks(3))
    real_read = device.read_sync

    def read_with_drop(n):
        if device.samples_read == 0 and len(device._buffers) == 2:
            device.dropped_samples += 4  # lost (and zero-filled) upstream
        return real_read(n)

    device.read_sync = read_with_drop
    _use(monkeypatch, device)
    out = io.StringIO()
    _capture_airspy(_config(), Namespace({'duration': None}), out)
    assert [int(c[1]) for c in _cards(out.getvalue())] == [0, 1, 2]
    err = capsys.readouterr().err
    assert 'lost and zero-filled' in err


def test_timestamps_are_reception_times(monkeypatch):
    device = ScriptedSDRDevice(_blocks(3), read_times=[100.25, 100.5, 101.0])
    _use(monkeypatch, device)
    out = io.StringIO()
    _capture_airspy(_config(), Namespace({'duration': None}), out)
    assert [float(c[0]) for c in _cards(out.getvalue())] == \
        [100.25, 100.5, 101.0]


def test_all_zero_block_skips_detection(monkeypatch):
    """Only a block that is zero throughout, history included, is
    skipped.  The first block of a drop still holds the previous block's
    tail: a burst there peaks in that block's window, and skipping it
    left only an off-peak detection in the block before."""
    zeros = np.zeros(12, dtype=np.int16)
    blocks = _blocks(1) + [zeros, zeros] + _blocks(1)
    device = ScriptedSDRDevice(blocks)
    calls = _use(monkeypatch, device)
    out = io.StringIO()
    _capture_airspy(_config(), Namespace({'duration': None}), out)
    assert len(calls) == 3
    cards = _cards(out.getvalue())
    assert [int(c[1]) for c in cards] == [0, 1, 3]
    history = np.frombuffer(base64.b64decode(cards[1][2]), np.int16)[:4]
    assert history.tolist() == [1, 1, 1, 1]  # block 0's tail


def test_window_outside_the_fft_fails_before_any_output(monkeypatch,
                                                       tmp_path):
    """A carrier bin the detector cannot index used to raise ValueError
    on the first block -- exit 1 after the output file was created."""
    created = []
    monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                        lambda *_a, **_k: created.append(1))
    card = tmp_path / 'rx0.card'
    with pytest.raises(ConfigValidationError, match='carrier_window'):
        # 8-sample blocks: bins 50-60 do not exist.
        _capture_airspy(_config(carrier_window=(50, 60, False)),
                        Namespace({'duration': None}),
                        ac._card_sink_for(str(card)))
    assert not card.exists()
    assert created == []


def test_header_records_rate_the_device_configured(monkeypatch):
    class Snapping(ScriptedSDRDevice):
        def set_sample_rate(self, rate):
            super().set_sample_rate(rate)
            return 6_000_000

    device = Snapping(_blocks(1))
    _use(monkeypatch, device)
    out = io.StringIO()
    _capture_airspy(_config(sample_rate=6_000_050),
                    Namespace({'duration': None}), out)
    assert 'sample_rate=6000000 ' in out.getvalue().splitlines()[0]


def test_get_info_failure_still_closes_device(monkeypatch):
    device = ScriptedSDRDevice(fail_in='get_info', exc=DeviceConfigError)
    _use(monkeypatch, device)
    with pytest.raises(SystemExit) as excinfo:
        _capture_airspy(_config(), Namespace({'duration': None}), None)
    assert excinfo.value.code == 1
    assert device.closed


def test_signal_handlers_are_restored(monkeypatch):
    def mine(_sig, _frame):
        pass

    previous = signal.signal(signal.SIGTERM, mine)
    try:
        _use(monkeypatch, ScriptedSDRDevice(_blocks(1)))
        _capture_airspy(_config(), Namespace({'duration': None}), None)
        assert signal.getsignal(signal.SIGTERM) is mine
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_failed_device_does_not_truncate_existing_output(monkeypatch,
                                                         tmp_path):
    card = tmp_path / 'rx0.card'
    card.write_text('#v2 bit_depth=12\nprevious run\n')
    device = ScriptedSDRDevice(fail_in='set_sample_rate')
    _use(monkeypatch, device)
    with pytest.raises(SystemExit):
        _capture_airspy(_config(), Namespace({'duration': None}),
                        ac._card_sink_for(str(card)))
    assert card.read_text() == '#v2 bit_depth=12\nprevious run\n'


# --- rotation -----------------------------------------------------------------

class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_rotation_starts_new_files_on_boundaries(tmp_path):
    clock = _Clock(7190.0)  # 10 s before an hour boundary
    pattern = str(tmp_path / 'rx0_%Y%m%dT%H%M%S.card')
    sink = CardSink(path=pattern, rotate=3600, clock=clock)
    sink.start(bit_depth=12, sample_rate=6_000_000, block_size=8,
               block_history=2)
    raw = np.zeros(4, dtype=np.int16)
    sink.write(7190.0, 0, raw)
    clock.now = 7199.9
    sink.tick()
    sink.write(7199.9, 1, raw)
    clock.now = 7200.0
    sink.tick()
    sink.write(7200.0, 2, raw)
    sink.close()

    first = time.strftime(pattern, time.localtime(7190.0))
    second = time.strftime(pattern, time.localtime(7200.0))
    assert sink.paths == [first, second]
    for path, indices in ((first, [0, 1]), (second, [2])):
        with open(path) as card:
            text = card.read()
        assert text.startswith('#v2 bit_depth=12 sample_rate=6000000')
        # Indices continue across files: SoA stays continuous.
        assert [int(c[1]) for c in _cards(text)] == indices


def test_rotation_never_overwrites_a_file(tmp_path):
    (tmp_path / 'rx0.card').write_text('keep\n')
    # Two rotations mapping to one name (e.g. local time falling back an
    # hour) must not clobber the earlier file.
    sink = CardSink(path=str(tmp_path / 'rx0.card'), rotate=60,
                    clock=_Clock(0.0))
    sink.start(bit_depth=12, sample_rate=6_000_000)
    sink.close()
    assert (tmp_path / 'rx0.card').read_text() == 'keep\n'
    assert (tmp_path / 'rx0.1.card').read_text().startswith('#v2 ')


def test_rotation_creates_pattern_directories(tmp_path):
    """A per-day directory ('%Y%m%d/rx0_...') used to stop capture with
    FileNotFoundError at the first rotation into a new day, and every
    restart failed the same way."""
    clock = _Clock(86400.0 - 0.5)
    pattern = str(tmp_path / 'card' / '%Y%m%d' / 'rx0_%H%M%S.card')
    sink = CardSink(path=pattern, rotate=3600, clock=clock)
    sink.start(bit_depth=12, sample_rate=6_000_000)
    clock.now = 86400.0
    sink.tick()
    clock.now = 2 * 86400.0
    sink.tick()
    sink.close()
    expected = [time.strftime(pattern, time.localtime(t))
                for t in (86400.0 - 0.5, 86400.0, 2 * 86400.0)]
    assert sink.paths == expected
    assert len({os.path.dirname(p) for p in expected}) >= 2
    for path in expected:
        with open(path) as card:
            assert card.read().startswith('#v2 ')


def test_rotation_pattern_without_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sink = CardSink(path='rx0_%H%M%S.card', rotate=60, clock=_Clock(0.0))
    sink.start(bit_depth=12, sample_rate=6_000_000)
    sink.close()
    assert [p.name for p in tmp_path.iterdir()] == sink.paths


@pytest.mark.parametrize('argv, message', [
    (['out.card', '--rotate', '3600'], 'strftime'),
    (['-', '--rotate', '3600'], 'output file'),
    (['x_%H.card', '--rotate', '0'], 'positive'),
    (['x_%Y%m%d.card', '--rotate', '60'], 'same name'),
])
def test_rotate_validation(monkeypatch, tmp_path, capsys, argv, message):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ac.config_validator, 'validate_config',
                        lambda _c: [])
    monkeypatch.setattr(sys, 'argv', ['capture'])
    with pytest.raises(SystemExit) as excinfo:
        ac.capture_cli(argv + ['--device-type', 'airspy_mini'])
    assert excinfo.value.code == EXIT_CONFIG
    assert message in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('pattern, rotate, repeats', [
    ('rx0_%Y%m%dT%H%M%S.card', 1, False),
    ('rx0_%Y%m%dT%H%M%S.card', 0.5, True),
    ('%Y%m%d/rx0_%H%M%S.card', 3600, False),
    # Hourly files named by the hour: local time repeats an hour when
    # daylight saving time ends, which the .1 suffix covers.
    ('rx0_%Y%m%d_%H.card', 3600, False),
    ('rx0_%Y%m%d_%H.card', 1800, True),
    ('rx0_%Y%m%d.card', 86400, False),
    ('rx0_%Y%m%d.card', 60, True),
    ('rx0_%H%M.card', 90, False),
    ('rx0_%H%M.card', 45, True),
    ('rx0_%Y%m.card', 30 * 86400, True),   # a 31-day month holds two
])
def test_rotation_pattern_must_name_every_file(pattern, rotate, repeats):
    """A pattern coarser than the interval tried .1 ... .998 suffixes and
    then stopped capture with a bare FileExistsError 999 files later
    (about 16 hours for daily names and --rotate 60) -- and again on
    every restart."""
    assert ac._pattern_repeats(pattern, rotate, now=1790000000) == repeats


def test_exhausted_suffixes_are_explained(tmp_path):
    (tmp_path / 'rx0.card').touch()
    for n in range(1, 1000):
        (tmp_path / 'rx0.{}.card'.format(n)).touch()
    with pytest.raises(FileExistsError) as excinfo:
        ac._open_new(str(tmp_path / 'rx0.card'))
    assert excinfo.value.filename == str(tmp_path / 'rx0.card')
    assert '.999' in excinfo.value.strerror


def _rtl_capture(monkeypatch, tmp_path, data, **overrides):
    raw = tmp_path / 'iq.u8'
    raw.write_bytes(data)
    monkeypatch.setattr(ac, 'carrier_detect_block',
                        lambda *_a, **_k: (True, 1, 10.0, 1.0))
    out = io.StringIO()
    ac._capture_rtlsdr(_config(device_type='rtlsdr', sample_rate=2_400_000,
                               tuner_gain=10.0, **overrides),
                       Namespace({'duration': None, 'input': str(raw)}), out)
    return out.getvalue()


def _card_bytes(card):
    return list(base64.b64decode(card[2]))


def test_rtl_python_fallback_writes_header_and_blocks(monkeypatch, tmp_path):
    # 2 history pairs, then 3 blocks of 6 new pairs
    text = _rtl_capture(monkeypatch, tmp_path, bytes(range(40)))
    assert text.startswith('#v2 bit_depth=8 sample_rate=2400000')
    cards = _cards(text)
    assert [int(c[1]) for c in cards] == [0, 1, 2]
    # Block 0's history is the first samples read.
    assert _card_bytes(cards[0]) == list(range(16))
    assert _card_bytes(cards[2]) == list(range(24, 40))


def test_rtl_history_after_skip_is_the_last_skipped_block(monkeypatch,
                                                          tmp_path):
    text = _rtl_capture(monkeypatch, tmp_path, bytes(range(12 + 24)),
                        capture_skip=1)
    cards = _cards(text)
    assert [int(c[1]) for c in cards] == [0, 1]
    assert _card_bytes(cards[0]) == list(range(8, 24))


@pytest.mark.parametrize('skip', [0, 1])
def test_rtl_noise_detects_no_carrier(tmp_path, capsys, skip):
    """Block 0's history used to be raw uint8 zeros, which decode to
    about -1-1j: a full-scale DC step that "detected" a carrier in block
    0 of every run, whatever the window or skip."""
    rng = np.random.default_rng(1)
    new = 16384 - 4920
    noise = np.clip(np.rint(127.4 + rng.normal(0, 6, 2 * new * 4)), 0, 255)
    raw = tmp_path / 'noise.u8'
    raw.write_bytes(noise.astype(np.uint8).tobytes())
    config = _config(device_type='rtlsdr', sample_rate=2_400_000,
                     tuner_gain=0.0, block_size=16384, block_history=4920,
                     capture_skip=skip, carrier_threshold=(0.0, 15.0, 0.0))
    out = io.StringIO()
    blocks = ac._capture_rtlsdr(
        config, Namespace({'duration': None, 'input': str(raw)}), out)
    assert blocks == 3
    assert _cards(out.getvalue()) == []
    assert 'block #' not in capsys.readouterr().err


# Inputs short of the first block: 2 history pairs (4 bytes), or one
# skipped block, then 6 new pairs (12 bytes).
@pytest.mark.parametrize('size, skip', [(0, 0), (4, 0), (15, 0), (0, 1),
                                        (12, 1), (23, 1)])
def test_rtl_input_without_a_block_keeps_the_old_card(monkeypatch, tmp_path,
                                                      capsys, size, skip):
    """`rtl_sdr ... - | thriftyx capture rx0.card --input -` with no
    dongle replaced rx0.card with a bare header and exited 0."""
    card = tmp_path / 'rx0.card'
    card.write_text('#v2 bit_depth=8\nprevious run\n')
    raw = tmp_path / 'iq.u8'
    raw.write_bytes(bytes(size))
    with pytest.raises(SystemExit) as excinfo:
        ac._capture_rtlsdr(_config(device_type='rtlsdr', tuner_gain=0.0,
                                   sample_rate=2_400_000,
                                   capture_skip=skip),
                           Namespace({'duration': None, 'input': str(raw)}),
                           ac._card_sink_for(str(card)))
    assert excinfo.value.code == 1
    assert 'input ended' in capsys.readouterr().err
    assert card.read_text() == '#v2 bit_depth=8\nprevious run\n'


def test_rtl_card_is_replaced_once_a_block_arrives(monkeypatch, tmp_path):
    card = tmp_path / 'rx0.card'
    card.write_text('previous run\n')
    raw = tmp_path / 'iq.u8'
    raw.write_bytes(bytes(range(16)))       # history + exactly one block
    monkeypatch.setattr(ac, 'carrier_detect_block',
                        lambda *_a, **_k: (False, 1, 10.0, 1.0))
    blocks = ac._capture_rtlsdr(
        _config(device_type='rtlsdr', tuner_gain=0.0, sample_rate=2_400_000),
        Namespace({'duration': None, 'input': str(raw)}),
        ac._card_sink_for(str(card)))
    assert blocks == 1
    assert card.read_text().startswith('#v2 bit_depth=8 sample_rate=2400000')
    assert _cards(card.read_text()) == []
