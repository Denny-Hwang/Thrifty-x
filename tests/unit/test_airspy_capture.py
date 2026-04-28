"""Tests for Airspy capture block indexing behaviour."""

import io

import numpy as np

from thriftyx.airspy_capture import _capture_airspy
from thriftyx.settings import Namespace


class _FakeAirspyDevice:
    """Minimal fake Airspy device for _capture_airspy tests."""

    def __init__(self, buffers, dropped_samples=0):
        self._buffers = list(buffers)
        self._opened = False
        self.dropped_samples = dropped_samples

    def open(self):
        self._opened = True

    def close(self):
        self._opened = False

    def set_sample_rate(self, _rate):
        return None

    def set_center_freq(self, _freq):
        return None

    def set_gain(self, _gain_type, _value):
        return None

    def set_bias_tee(self, _enabled):
        return None

    def read_sync(self, _num_samples):
        if not self._buffers:
            return np.array([], dtype=np.int16)
        return self._buffers.pop(0)


def _build_config(capture_skip=0):
    return Namespace({
        'device_type': 'airspy_mini',
        'sample_rate': 3_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 8,
        'block_history': 2,
        'capture_skip': capture_skip,
        'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 0.0, 0.0),
        'lna_gain': 0,
        'mixer_gain': 0,
        'vga_gain': 0,
        'bias_tee': False,
    })


def _capture_indices(monkeypatch, capture_skip):
    new_samples = 6  # block_size(8) - history(2)
    buf_len = new_samples * 2

    skip_buffers = [np.arange(buf_len, dtype=np.int16) for _ in range(capture_skip)]
    process_buffers = [
        np.arange(buf_len, dtype=np.int16),
        np.arange(buf_len, dtype=np.int16) + 100,
        np.arange(buf_len, dtype=np.int16) + 200,
    ]
    fake = _FakeAirspyDevice(skip_buffers + process_buffers)

    monkeypatch.setattr(
        'thriftyx.hal.device_factory.create_device',
        lambda _device_type: fake,
    )
    monkeypatch.setattr(
        'thriftyx.airspy_capture.carrier_detect_block',
        lambda *_args, **_kwargs: (True, 1, 10.0, 1.0),
    )

    output = io.StringIO()
    _capture_airspy(_build_config(capture_skip), Namespace({'duration': None}), output)

    lines = [line for line in output.getvalue().strip().splitlines()
             if line and not line.startswith('#')]
    return [int(line.split()[1]) for line in lines]


def test_airspy_block_index_starts_at_zero(monkeypatch):
    indices = _capture_indices(monkeypatch, capture_skip=0)
    assert indices == [0, 1, 2]


def test_airspy_block_index_starts_at_zero_after_skip(monkeypatch):
    indices = _capture_indices(monkeypatch, capture_skip=2)
    assert indices == [0, 1, 2]
