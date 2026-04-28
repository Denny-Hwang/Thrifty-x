# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Regression tests for ``_capture_airspy`` failure-path safety.

Verify that exceptions raised before the capture loop starts (e.g. in
``set_sample_rate``) are reported via ``sys.exit`` rather than producing
a confusing traceback from an unbound local variable in the ``finally``
block.
"""

import io

import pytest

from thriftyx.airspy_capture import _capture_airspy
from thriftyx.exceptions import DeviceConfigError, DeviceCaptureError
from thriftyx.settings import Namespace


class _FailingDevice:
    """Stub device that fails in a configurable phase."""

    def __init__(self, fail_in='set_sample_rate', exc=DeviceConfigError):
        self._fail_in = fail_in
        self._exc = exc
        self._open = False
        self.closed = False

    def open(self):
        self._open = True
        if self._fail_in == 'open':
            raise self._exc('open failure')

    def close(self):
        self.closed = True

    def _maybe_fail(self, name):
        if self._fail_in == name:
            raise self._exc(f'{name} failure')

    def set_sample_rate(self, _r):
        self._maybe_fail('set_sample_rate')

    def set_center_freq(self, _f):
        self._maybe_fail('set_center_freq')

    def set_gain(self, _t, _v):
        self._maybe_fail('set_gain')

    def set_bias_tee(self, _b):
        self._maybe_fail('set_bias_tee')

    def read_sync(self, _n):
        self._maybe_fail('read_sync')
        # Returning empty buffer triggers the loop's "short read" exit.
        import numpy as np
        return np.array([], dtype=np.int16)


def _config(**overrides):
    base = {
        'device_type': 'airspy_mini',
        'sample_rate': 6_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 16,
        'block_history': 4,
        'capture_skip': 0,
        'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 0.0, 0.0),
        'lna_gain': 0,
        'mixer_gain': 0,
        'vga_gain': 0,
        'bias_tee': False,
    }
    base.update(overrides)
    return Namespace(base)


def _run_with_failing_device(monkeypatch, fail_in, exc):
    fake = _FailingDevice(fail_in=fail_in, exc=exc)
    monkeypatch.setattr(
        'thriftyx.hal.device_factory.create_device',
        lambda _device_type, **_kwargs: fake,
    )
    output = io.StringIO()
    with pytest.raises(SystemExit) as exc_info:
        _capture_airspy(_config(), Namespace({'duration': None}), output)
    return exc_info.value.code, fake


def test_capture_airspy_set_sample_rate_failure_does_not_unbound_local(
        monkeypatch):
    """``finally`` block must not raise ``UnboundLocalError`` when
    ``set_sample_rate`` fails before the capture loop runs."""
    code, fake = _run_with_failing_device(
        monkeypatch, 'set_sample_rate', DeviceConfigError)
    assert code == 1
    assert fake.closed is True


def test_capture_airspy_capture_error_caught(monkeypatch):
    """``DeviceCaptureError`` raised by the device must be caught and
    converted into a clean ``sys.exit(1)``."""
    code, fake = _run_with_failing_device(
        monkeypatch, 'read_sync', DeviceCaptureError)
    assert code == 1
    assert fake.closed is True
