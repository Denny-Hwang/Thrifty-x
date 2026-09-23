# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""HAL capture: block indices, timestamps, header and output lifecycle.

TDOA turns ``block_idx`` into sample-of-arrival and matches receivers by
card timestamp, so both must describe when the samples were taken --
not how many blocks happened to be processed, or when.
"""

import io
import signal
import sys
import time

import numpy as np
import pytest

import thriftyx.airspy_capture as ac
from thriftyx.airspy_capture import CardSink, _capture_airspy
from thriftyx.exceptions import EXIT_CONFIG, DeviceConfigError
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
    blocks = _blocks(1) + [np.zeros(12, dtype=np.int16)] + _blocks(1)
    device = ScriptedSDRDevice(blocks)
    calls = _use(monkeypatch, device)
    out = io.StringIO()
    _capture_airspy(_config(), Namespace({'duration': None}), out)
    assert len(calls) == 2
    assert [int(c[1]) for c in _cards(out.getvalue())] == [0, 2]


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


@pytest.mark.parametrize('argv, message', [
    (['out.card', '--rotate', '3600'], 'strftime'),
    (['-', '--rotate', '3600'], 'output file'),
    (['x_%H.card', '--rotate', '0'], 'positive'),
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


def test_rtl_python_fallback_writes_header_and_blocks(monkeypatch, tmp_path):
    raw = tmp_path / 'iq.u8'
    raw.write_bytes(bytes(range(36)))  # 3 blocks of 6 new pairs
    monkeypatch.setattr(ac, 'carrier_detect_block',
                        lambda *_a, **_k: (True, 1, 10.0, 1.0))
    out = io.StringIO()
    ac._capture_rtlsdr(_config(device_type='rtlsdr', sample_rate=2_400_000,
                               tuner_gain=10.0),
                       Namespace({'duration': None, 'input': str(raw)}), out)
    text = out.getvalue()
    assert text.startswith('#v2 bit_depth=8 sample_rate=2400000')
    assert [int(c[1]) for c in _cards(text)] == [0, 1, 2]
