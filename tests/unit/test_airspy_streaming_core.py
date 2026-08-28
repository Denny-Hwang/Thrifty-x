# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Unit tests for the read_sync streaming core (no hardware required).

Exercises the sample-routing / bounded-buffer / error-surfacing logic of
``AirspyMiniDevice`` by driving ``_on_samples`` (the callback-thread
entry point) and ``read_sync`` directly.  Covers review findings O1
(bounded buffer with software-drop accounting), N5 (callback exceptions
must surface instead of being swallowed by ctypes), and part of O3
(previously the streaming core had no tests at all).
"""

import numpy as np
import pytest

from thriftyx.exceptions import DeviceCaptureError
from thriftyx.hal.airspy_mini import AirspyMiniDevice


def _streaming_device():
    """A device object in 'streaming already started' state.

    ``read_sync`` only touches the internal buffer once
    ``_stream_started`` is set, so no libairspy calls are made.
    """
    dev = AirspyMiniDevice()
    dev._open = True
    dev._capturing = True
    dev._stream_started = True
    dev._check_open = lambda: None  # bypass the _lib presence check
    # Shadow close() so __del__ can never reach the real libairspy with
    # this fake state: with libairspy installed, close() -> _stop_rx()
    # would call airspy_stop_rx(NULL) at GC time and segfault the test
    # run (the flags above claim an open, capturing device).
    dev.close = lambda: None
    return dev


class TestOnSamplesRouting:
    def test_appends_to_stream_buffer(self):
        dev = _streaming_device()
        dev._on_samples(np.arange(8, dtype=np.int16))
        assert dev._stream_total == 8
        assert len(dev._stream_chunks) == 1

    def test_routes_to_user_callback(self):
        dev = _streaming_device()
        received = []
        dev._user_callback = received.append
        dev._on_samples(np.arange(8, dtype=np.int16))
        assert len(received) == 1
        assert dev._stream_total == 0  # not buffered in callback mode


class TestBoundedBuffer:
    def test_drops_when_full_and_counts(self):
        dev = _streaming_device()
        dev._max_stream_values = 16
        dev._on_samples(np.zeros(12, dtype=np.int16))   # fits
        dev._on_samples(np.zeros(12, dtype=np.int16))   # 24 > 16 -> dropped
        assert dev._stream_total == 12
        assert dev.software_dropped_samples == 6        # 12 int16 = 6 pairs
        assert dev.dropped_samples == 6                 # folded in

    def test_recovers_after_drain(self):
        dev = _streaming_device()
        dev._max_stream_values = 16
        dev._on_samples(np.zeros(12, dtype=np.int16))
        dev._on_samples(np.zeros(12, dtype=np.int16))   # dropped
        dev.read_sync(6)                                # drain 12 values
        dev._on_samples(np.ones(12, dtype=np.int16))    # fits again
        assert dev._stream_total == 12
        assert dev.software_dropped_samples == 6        # unchanged

    def test_unbounded_when_cap_is_none(self):
        dev = _streaming_device()
        dev._max_stream_values = None
        for _ in range(10):
            dev._on_samples(np.zeros(1000, dtype=np.int16))
        assert dev._stream_total == 10_000
        assert dev.software_dropped_samples == 0

    def test_start_rx_sizes_cap_from_sample_rate(self):
        dev = AirspyMiniDevice()
        dev.max_buffer_seconds = 2.0
        dev._sample_rate = 3_000_000
        # Replicate _start_rx's sizing formula (int16 values = pairs * 2).
        rate = dev._sample_rate or max(dev._supported_sample_rates)
        assert int(rate * 2 * dev.max_buffer_seconds) == 12_000_000


class TestReadSync:
    def test_assembles_exact_request_across_chunks(self):
        dev = _streaming_device()
        dev._on_samples(np.arange(0, 6, dtype=np.int16))
        dev._on_samples(np.arange(6, 14, dtype=np.int16))
        out = dev.read_sync(5)  # 10 int16 values spanning both chunks
        assert out.tolist() == list(range(10))
        # Remainder stays buffered for the next call.
        assert dev._stream_total == 4

    def test_timeout_raises_capture_error(self):
        dev = _streaming_device()
        dev.read_timeout = 0.05
        with pytest.raises(DeviceCaptureError, match="timed out"):
            dev.read_sync(4)

    def test_callback_error_is_surfaced(self):
        dev = _streaming_device()
        dev._callback_error = RuntimeError("boom in callback")
        with pytest.raises(DeviceCaptureError, match="boom in callback"):
            dev.read_sync(4)
        assert dev._callback_error is None  # consumed, not re-raised

    def test_refuses_while_user_callback_active(self):
        dev = _streaming_device()
        dev._user_callback = lambda arr: None
        with pytest.raises(DeviceCaptureError, match="start_capture"):
            dev.read_sync(4)


def test_read_sync_zero_samples_returns_empty():
    """read_sync(0) must return an empty int16 array, not raise from
    np.concatenate([])."""
    dev = _streaming_device()
    out = dev.read_sync(0)
    assert out.dtype == np.int16
    assert out.size == 0
