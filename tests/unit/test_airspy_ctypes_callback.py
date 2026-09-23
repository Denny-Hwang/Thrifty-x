# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""The libairspy RX callback, exercised through the real ctypes boundary.

Other HAL tests replace ``_lib`` and never run the CFUNCTYPE trampoline
that ``_start_rx`` hands to ``airspy_start_rx``.  Here a fake library
keeps that trampoline, and the tests call it the way libairspy's
consumer thread does: with a pointer to an ``airspy_transfer`` struct
whose ``samples`` field points at a C int16 buffer.
"""

import ctypes
import threading
import time

import numpy as np
import pytest

from thriftyx.exceptions import DeviceCaptureError
from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import AirspyMiniDevice


class _StartRxLib:
    """Fake libairspy that records the callback passed to airspy_start_rx."""

    def __init__(self):
        self.callback = None

    def airspy_open(self, handle_ptr):
        return 0

    def airspy_set_sample_type(self, handle, sample_type):
        return 0

    def airspy_board_partid_serialno_read(self, handle, info):
        return -1

    def airspy_set_samplerate(self, handle, rate):
        return 0

    def airspy_start_rx(self, handle, callback, ctx):
        self.callback = callback
        return 0

    def airspy_stop_rx(self, handle):
        return 0

    def airspy_close(self, handle):
        return 0


@pytest.fixture
def device(monkeypatch):
    lib = _StartRxLib()
    monkeypatch.setattr(am, '_lib', lib)
    dev = AirspyMiniDevice()
    dev.open()
    dev.set_sample_rate(6_000_000)
    dev.read_timeout = 5.0
    yield dev, lib
    dev.close()


def _deliver(lib, samples, dropped=0,
             sample_type=am.AIRSPY_SAMPLE_INT16_IQ):
    """Invoke the registered C callback with one transfer; return its buffer."""
    buf = (ctypes.c_int16 * len(samples))(*samples)
    transfer = am._AirspyTransfer(
        device=None, ctx=None,
        samples=ctypes.cast(buf, ctypes.c_void_p),
        sample_count=len(samples) // 2,
        dropped_samples=dropped,
        sample_type=sample_type)
    assert lib.callback(ctypes.byref(transfer)) == 0
    return buf


def _read_in_background(dev, num_samples):
    """Start read_sync (which starts RX) on a thread; return (thread, box)."""
    box = {}

    def _read():
        try:
            box['iq'] = dev.read_sync(num_samples)
        except Exception as exc:  # surfaced to the test thread
            box['error'] = exc

    thread = threading.Thread(target=_read)
    thread.start()
    return thread, box


def _wait_for_callback(lib):
    deadline = time.monotonic() + 5
    while lib.callback is None:
        assert time.monotonic() < deadline, "airspy_start_rx never called"
        time.sleep(0.001)


def test_transfer_struct_matches_airspy_h():
    """airspy_transfer_t on LP64: 3 pointers, int, padding, uint64, int."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("layout check written for 64-bit hosts")
    fields = am._AirspyTransfer
    assert ctypes.sizeof(fields) == 48
    assert (fields.samples.offset, fields.sample_count.offset,
            fields.dropped_samples.offset, fields.sample_type.offset) == \
        (16, 24, 32, 40)


def test_samples_cross_the_callback_unchanged_and_copied(device):
    dev, lib = device
    thread, box = _read_in_background(dev, 4)
    _wait_for_callback(lib)
    iq = [1, -1, 2, -2, 30000, -30000, 4, -4]
    buf = _deliver(lib, iq)
    buf[0] = 999  # libairspy reuses its buffer after the callback returns
    thread.join(5)
    np.testing.assert_array_equal(box['iq'], iq)


def test_callback_reference_is_kept_alive(device):
    """ctypes frees a trampoline whose Python object is collected; libairspy
    would then call freed memory.  The device must hold the reference."""
    dev, lib = device
    thread, _ = _read_in_background(dev, 1)
    _wait_for_callback(lib)
    assert dev._callback_ref is lib.callback
    _deliver(lib, [0, 0])
    thread.join(5)


def test_dropped_samples_are_accounted(device):
    dev, lib = device
    thread, box = _read_in_background(dev, 2)
    _wait_for_callback(lib)
    _deliver(lib, [1, 1, 2, 2], dropped=4096)
    thread.join(5)
    assert dev.dropped_samples == 4096


def test_wrong_sample_type_surfaces_in_read_sync(device):
    """ctypes would swallow an exception raised in the callback; it must be
    reported by read_sync instead of silently corrupting data."""
    dev, lib = device
    thread, box = _read_in_background(dev, 2)
    _wait_for_callback(lib)
    _deliver(lib, [1, 1, 2, 2], sample_type=am.AIRSPY_SAMPLE_FLOAT32_IQ)
    thread.join(5)
    assert isinstance(box.get('error'), DeviceCaptureError)
    assert 'sample_type' in str(box['error'])
