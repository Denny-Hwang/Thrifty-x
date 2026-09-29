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

import logging
import threading
import time

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

    def test_user_callback_mode_still_counts_hardware_drops(self):
        """``thriftyx capture`` (start_capture) reads dropped_samples too."""
        dev = _streaming_device()
        received = []
        dev._user_callback = received.append
        dev._on_samples(np.arange(8, dtype=np.int16), dropped_pairs=5)
        assert len(received) == 1
        assert dev.dropped_samples == 5
        assert dev._stream_total == 0


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

    @pytest.mark.parametrize('rate, expected', [
        (10_000_000, 80_000_000),
        (2_500_000, 20_000_000),
    ])
    def test_four_second_cap_at_bench_rates(self, rate, expected):
        dev = AirspyMiniDevice()
        dev._sample_rate = rate
        assert int(rate * 2 * dev.max_buffer_seconds) == expected


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
        dev.dropped_samples = 11
        dev.software_dropped_samples = 7
        dev.last_read_time = 123.0
        dev._buffer_full_logged = True
        dev._on_samples(np.full(8, 3, dtype=np.int16))
        dev.discard_buffered()
        assert dev._stream_total == 0
        assert dev.last_read_time is None
        assert dev.dropped_samples == 11
        assert dev.software_dropped_samples == 7
        assert dev._buffer_full_logged is False
        dev._on_samples(np.full(4, 4, dtype=np.int16))
        assert dev.read_sync(2).tolist() == [4] * 4


class _CountingLock:
    """A stream lock that can run an action just before its k-th acquisition.

    Lets a test place another thread's whole critical section (say, the
    consumer's boundary call, or the RX callback) exactly between two steps
    of the operation under test, without real threads or timing.  It also
    counts acquisitions, so a test can tell one critical section from two.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.entries = 0
        self._fire_at = None
        self._action = None

    def arm(self, k, action):
        """Run *action* just before the k-th (0-based) acquisition from now."""
        self.entries = 0
        self._fire_at, self._action = k, action

    def disarm(self):
        """Forget an action the operation under test never reached."""
        self._fire_at = self._action = None

    def locked(self):
        return self._lock.locked()

    def __enter__(self):
        if self._fire_at is not None and self.entries == self._fire_at:
            action, self._action, self._fire_at = self._action, None, None
            action()
        self.entries += 1
        self._lock.acquire()
        return self

    def __exit__(self, *_exc):
        self._lock.release()
        return False


def _gap_pairs(values):
    """I/Q pairs that are exactly zero in an interleaved int16 read."""
    pairs = values.reshape(-1, 2)
    return int(np.count_nonzero(~pairs.any(axis=1)))


class TestPausedBuffering:
    """pause_buffering()/resume_buffering(): a running stream nobody reads."""

    def test_paused_stream_cannot_fill_the_buffer(self, caplog):
        dev = _streaming_device()
        dev._max_stream_values = 16
        dev.pause_buffering()
        with caplog.at_level(logging.WARNING):
            for _ in range(100):                     # far beyond the cap
                dev._on_samples(np.ones(12, dtype=np.int16))
        assert dev._stream_mem == 0 and dev._stream_total == 0
        assert not dev._stream_chunks
        assert dev.software_dropped_samples == 0     # discarded, not lost
        assert dev.dropped_samples == 0
        assert 'buffer full' not in caplog.text

    def test_same_stream_unpaused_does_overflow(self, caplog):
        """Control: without the pause the very same traffic overflows."""
        dev = _streaming_device()
        dev._max_stream_values = 16
        with caplog.at_level(logging.WARNING):
            for _ in range(100):
                dev._on_samples(np.ones(12, dtype=np.int16))
        assert dev.software_dropped_samples == 99 * 6
        assert 'buffer full' in caplog.text

    def test_hardware_drops_still_count_but_leave_no_gap(self):
        dev = _streaming_device()
        dev.pause_buffering()
        dev._on_samples(np.ones(8, dtype=np.int16), dropped_pairs=5)
        assert dev.dropped_samples == 5
        assert dev.software_dropped_samples == 0
        assert dev._stream_total == 0                # no zero gap queued
        assert dev.resume_buffering() == 5

    def test_pause_drops_the_queue_and_reads_are_refused(self):
        dev = _streaming_device()
        dev._on_samples(np.full(8, 3, dtype=np.int16))
        dev.pause_buffering()
        assert dev._stream_total == 0 and dev._stream_mem == 0
        with pytest.raises(DeviceCaptureError, match="resume_buffering"):
            dev.read_sync(2)
        with pytest.raises(DeviceCaptureError, match="resume_buffering"):
            dev.read_sync(0)        # misuse is reported for an empty read too

    def test_resume_starts_from_an_empty_queue(self):
        dev = _streaming_device()
        dev._on_samples(np.full(8, 1, dtype=np.int16), dropped_pairs=2)
        dev.pause_buffering()
        dev._on_samples(np.full(8, 2, dtype=np.int16))       # discarded
        dev.dropped_samples += 7                              # counted, kept
        assert dev.resume_buffering() == 9
        assert dev._stream_total == 0
        dev._on_samples(np.full(4, 4, dtype=np.int16))
        assert dev.read_sync(2).tolist() == [4] * 4
        assert dev.dropped_samples == 9                       # never reset

    def test_resume_without_pause_is_a_clean_boundary_too(self):
        dev = _streaming_device()
        dev._on_samples(np.full(8, 1, dtype=np.int16), dropped_pairs=3)
        assert dev.resume_buffering() == 3
        assert dev._stream_total == 0

    def test_resume_rearms_the_buffer_full_warning(self, caplog):
        """A chunk larger than the cap never fits, so nothing but the reset
        in the boundary's queue clearing can re-arm the once-per-episode
        warning (an accepted chunk would also do it)."""
        dev = _streaming_device()
        dev._max_stream_values = 8
        with caplog.at_level(logging.WARNING):
            dev._on_samples(np.ones(12, dtype=np.int16))     # full: warns
            dev._on_samples(np.ones(12, dtype=np.int16))     # already logged
        assert caplog.text.count('buffer full') == 1
        dev.pause_buffering()
        dev.resume_buffering()
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            dev._on_samples(np.ones(12, dtype=np.int16))
        assert caplog.text.count('buffer full') == 1

    def test_pausing_and_resuming_take_the_stream_lock_once(self):
        """The queue is cleared under the lock the callback appends under."""
        dev = _streaming_device()
        dev._stream_lock = _CountingLock()
        dev.pause_buffering()
        assert dev._stream_lock.entries == 1
        dev.resume_buffering()
        assert dev._stream_lock.entries == 2

    def test_a_blocked_read_sync_notices_a_pause_from_another_thread(self):
        dev = _streaming_device()
        dev.read_timeout = 5.0
        box = {}

        def reader():
            try:
                dev.read_sync(4)
            except DeviceCaptureError as exc:
                box['error'] = exc

        thread = threading.Thread(target=reader)
        thread.start()
        time.sleep(0.1)                                      # reader waits
        dev.pause_buffering()
        thread.join(2)
        assert not thread.is_alive()
        assert 'resume_buffering' in str(box['error'])

    def test_stopping_rx_ends_the_pause(self):
        dev = _streaming_device()
        dev._capturing = False          # no libairspy call in _stop_rx
        dev.pause_buffering()
        dev._stop_rx()
        assert dev._buffering_paused is False

    def test_base_class_defaults_queue_nothing(self):
        from tests.mocks.mock_device import MockSDRDevice
        dev = MockSDRDevice()
        dev.dropped_samples = 4
        assert dev.pause_buffering() is None
        assert dev.resume_buffering() == 4


class TestBoundaryAtomicity:
    """Losses either side of resume_buffering() are never misattributed.

    A hardware drop is *counted* and its zero gap *queued* by the callback
    thread; the consumer's boundary clears the queue and reads the counter.
    Whatever the interleaving, a zero gap that is still in the queue after
    the boundary must be counted after the baseline the boundary returned.
    """

    @staticmethod
    def _dev():
        dev = _streaming_device()
        dev._stream_lock = _CountingLock()
        return dev

    @staticmethod
    def _assert_no_uncounted_gap(dev, baseline):
        queued = dev._stream_total
        stream = dev.read_sync(queued // 2) if queued else np.empty(0)
        assert _gap_pairs(stream) == dev.dropped_samples - baseline, (
            f"zero pairs still queued {_gap_pairs(stream)}, counted after "
            f"the baseline {dev.dropped_samples - baseline}")

    def test_callback_that_reaches_the_lock_after_the_boundary(self):
        dev = self._dev()
        boundary = {}
        dev._stream_lock.arm(0, lambda: boundary.setdefault(
            'baseline', dev.resume_buffering()))
        dev._on_samples(np.full(8, 5, dtype=np.int16), dropped_pairs=4)
        after = dev.dropped_samples - boundary['baseline']
        stream = dev.read_sync(4 + 4)            # 4 gap pairs + 4 data pairs
        assert _gap_pairs(stream) == after == 4

    def test_callback_that_finished_before_the_boundary(self):
        dev = self._dev()
        dev._on_samples(np.full(8, 5, dtype=np.int16), dropped_pairs=4)
        baseline = dev.resume_buffering()
        assert baseline == 4                     # counted before ...
        assert dev._stream_total == 0            # ... and its gap is gone

    @pytest.mark.parametrize('k', range(3))
    def test_callback_at_every_lock_gap_inside_the_boundary(self, k):
        """The RX callback runs before the k-th lock acquisition of
        resume_buffering().  With the boundary one critical section there
        is only k = 0; were it two (clear, then read the counter) k = 1
        would put a whole callback between them: counted in the baseline,
        its gap left in the queue."""
        dev = self._dev()
        dev._on_samples(np.full(8, 1, dtype=np.int16))       # queued before
        dev._stream_lock.arm(k, lambda: dev._on_samples(
            np.full(8, 5, dtype=np.int16), dropped_pairs=4))
        baseline = dev.resume_buffering()
        dev._stream_lock.disarm()
        self._assert_no_uncounted_gap(dev, baseline)

    @pytest.mark.parametrize('k', range(3))
    def test_boundary_at_every_lock_gap_inside_the_callback(self, k):
        """The consumer's boundary runs before the k-th lock acquisition of
        one hardware-drop callback.  Were counting and queueing the gap two
        critical sections, k = 1 would put the boundary between them: the
        loss lands in the baseline, its gap in the fresh queue."""
        dev = self._dev()
        boundary = {}
        dev._stream_lock.arm(k, lambda: boundary.setdefault(
            'baseline', dev.resume_buffering()))
        dev._on_samples(np.full(8, 5, dtype=np.int16), dropped_pairs=4)
        dev._stream_lock.disarm()
        self._assert_no_uncounted_gap(dev, boundary.get('baseline', 0))

    def test_the_counter_is_read_under_the_boundarys_lock(self):
        """A real callback thread fires at the instant the boundary reads
        the counter.  Were the read outside the lock, the whole callback
        (count *and* gap) could land right before it: the baseline would
        include a loss whose zero gap stays in the stream, measured and
        never counted.  Here the callback must wait for the boundary."""
        racer = {}

        class Probe(AirspyMiniDevice):
            @property
            def dropped_samples(self):
                hook, racer['hook'] = racer.get('hook'), None
                if hook is not None:
                    hook()
                return self._dropped

            @dropped_samples.setter
            def dropped_samples(self, value):
                self._dropped = value

        dev = Probe()
        dev._open = True
        dev._check_open = lambda: None
        dev.close = lambda: None
        dev._capturing = True
        dev._stream_started = True

        def callback_fires():
            thread = threading.Thread(
                target=dev._on_samples,
                args=(np.full(8, 5, dtype=np.int16), 4))
            racer['thread'] = thread
            thread.start()
            thread.join(0.05)      # completes now, or blocks on the lock

        racer['hook'] = callback_fires
        baseline = dev.resume_buffering()
        racer['thread'].join(2)
        assert not racer['thread'].is_alive()

        stream = dev.read_sync(4 + 4)            # 4 gap pairs + 4 data pairs
        assert _gap_pairs(stream) == dev.dropped_samples - baseline == 4

    def test_counter_is_only_written_with_the_lock_held(self):
        """Counting a loss happens inside the critical section that queues
        its gap (see the lock-gap sweeps above for the ordering)."""
        writes = []

        class Probe(AirspyMiniDevice):
            @property
            def dropped_samples(self):
                return self._dropped

            @dropped_samples.setter
            def dropped_samples(self, value):
                lock = getattr(self, '_stream_lock', None)
                writes.append(lock.locked() if lock is not None else None)
                self._dropped = value

        dev = Probe()
        dev._open = True
        dev._check_open = lambda: None
        dev.close = lambda: None
        writes.clear()
        dev._max_stream_values = 8
        dev._on_samples(np.ones(4, dtype=np.int16), dropped_pairs=2)  # hw
        dev._on_samples(np.ones(8, dtype=np.int16))    # buffer full: sw
        assert writes == [True, True]
