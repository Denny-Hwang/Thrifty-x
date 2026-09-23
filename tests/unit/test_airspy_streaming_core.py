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
        assert dev._stream_mem == 12                    # memory stays capped
        assert dev.software_dropped_samples == 6        # 12 int16 = 6 pairs
        assert dev.dropped_samples == 6                 # folded in

    def test_recovers_after_drain(self):
        dev = _streaming_device()
        dev._max_stream_values = 16
        dev._on_samples(np.full(12, 5, dtype=np.int16))
        dev._on_samples(np.full(12, 7, dtype=np.int16))  # dropped
        dev.read_sync(6)                                # drain 12 values
        dev._on_samples(np.ones(12, dtype=np.int16))    # fits again
        assert dev._stream_mem == 12
        assert dev.software_dropped_samples == 6        # unchanged

    def test_dropped_chunk_is_read_back_as_zeros(self):
        """The stream must stay time-contiguous: a chunk dropped for a
        full buffer comes back as zeros of the same length, so later
        samples keep their position (and block indices their meaning)."""
        dev = _streaming_device()
        dev._max_stream_values = 16
        dev._on_samples(np.full(12, 5, dtype=np.int16))
        dev._on_samples(np.full(12, 7, dtype=np.int16))  # dropped
        out = dev.read_sync(6)
        dev._on_samples(np.full(4, 9, dtype=np.int16))
        out = np.concatenate([out, dev.read_sync(8)])
        assert out.tolist() == [5] * 12 + [0] * 12 + [9] * 4

    def test_unbounded_when_cap_is_none(self):
        dev = _streaming_device()
        dev._max_stream_values = None
        for _ in range(10):
            dev._on_samples(np.zeros(1000, dtype=np.int16))
        assert dev._stream_mem == 10_000
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


class TestGapsAndTimes:
    def test_hardware_drop_is_zero_filled_before_the_transfer(self):
        dev = _streaming_device()
        dev._on_samples(np.full(4, 1, dtype=np.int16))
        dev._on_samples(np.full(4, 2, dtype=np.int16), dropped_pairs=3)
        out = dev.read_sync(7)
        assert out.tolist() == [1] * 4 + [0] * 6 + [2] * 4
        assert dev.dropped_samples == 3

    def test_consecutive_losses_merge_into_one_gap(self):
        dev = _streaming_device()
        dev._max_stream_values = 8
        dev._on_samples(np.full(8, 1, dtype=np.int16))
        dev._on_samples(np.full(4, 2, dtype=np.int16))   # dropped
        dev._on_samples(np.full(6, 3, dtype=np.int16))   # dropped
        assert dev._stream_total == 18
        out = dev.read_sync(9)
        assert out.tolist() == [1] * 8 + [0] * 10
        assert dev._stream_total == 0

    def test_gap_spanning_reads(self):
        dev = _streaming_device()
        dev._on_samples(np.full(2, 1, dtype=np.int16), dropped_pairs=5)
        assert dev.read_sync(2).tolist() == [0] * 4
        assert dev.read_sync(4).tolist() == [0] * 6 + [1, 1]

    def test_last_read_time_is_arrival_of_last_sample(self):
        """Blocks are stamped when their last sample arrived, not when a
        backlogged consumer got round to them."""
        dev = _streaming_device()
        dev._sample_rate = 1000  # 1 ms per pair
        dev._on_samples(np.zeros(20, dtype=np.int16), arrived=100.0)
        dev._on_samples(np.zeros(20, dtype=np.int16), arrived=100.010)
        dev.read_sync(10)     # exactly the first chunk
        assert dev.last_read_time == pytest.approx(100.0)
        dev.read_sync(6)      # 6 of the second chunk's 10 pairs
        assert dev.last_read_time == pytest.approx(100.010 - 0.004)

    def test_discard_buffered_drops_backlog(self):
        dev = _streaming_device()
        dev._on_samples(np.full(8, 3, dtype=np.int16))
        dev.discard_buffered()
        assert dev._stream_total == 0
        dev._on_samples(np.full(4, 4, dtype=np.int16))
        assert dev.read_sync(2).tolist() == [4] * 4
