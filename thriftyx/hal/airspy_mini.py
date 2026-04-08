# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Airspy Mini SDR driver using ctypes binding to libairspy."""

import collections
import ctypes
import ctypes.util
import logging
import threading
import time
import warnings
from typing import Callable, Optional

import numpy as np

from thriftyx.hal.base import SDRDevice, DeviceInfo, SampleFormat
from thriftyx.exceptions import (DeviceNotFoundError, DeviceConfigError,
                                   DeviceCaptureError, DeviceError)

logger = logging.getLogger(__name__)

# Try to load libairspy — do NOT crash if not found
_lib = None
_lib_error = None

try:
    _lib_path = ctypes.util.find_library('airspy') or 'libairspy.so'
    _lib = ctypes.cdll.LoadLibrary(_lib_path)

    # Bind functions
    _lib.airspy_open.restype = ctypes.c_int
    _lib.airspy_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]

    _lib.airspy_close.restype = ctypes.c_int
    _lib.airspy_close.argtypes = [ctypes.c_void_p]

    _lib.airspy_set_samplerate.restype = ctypes.c_int
    _lib.airspy_set_samplerate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    _lib.airspy_set_freq.restype = ctypes.c_int
    _lib.airspy_set_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    _lib.airspy_set_lna_gain.restype = ctypes.c_int
    _lib.airspy_set_lna_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint8]

    _lib.airspy_set_mixer_gain.restype = ctypes.c_int
    _lib.airspy_set_mixer_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint8]

    _lib.airspy_set_vga_gain.restype = ctypes.c_int
    _lib.airspy_set_vga_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint8]

    _lib.airspy_set_rf_bias.restype = ctypes.c_int
    _lib.airspy_set_rf_bias.argtypes = [ctypes.c_void_p, ctypes.c_uint8]

    _lib.airspy_start_rx.restype = ctypes.c_int
    _lib.airspy_start_rx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p]

    _lib.airspy_stop_rx.restype = ctypes.c_int
    _lib.airspy_stop_rx.argtypes = [ctypes.c_void_p]

    _lib.airspy_set_sample_type.restype = ctypes.c_int
    _lib.airspy_set_sample_type.argtypes = [ctypes.c_void_p, ctypes.c_int]

    class _AirspyReadPartidSerialNo(ctypes.Structure):
        """airspy_read_partid_serialno_t C struct."""
        _fields_ = [
            ('part_id', ctypes.c_uint32 * 2),
            ('serial_no', ctypes.c_uint32 * 4),
        ]

    _lib.airspy_board_partid_serialno_read.restype = ctypes.c_int
    _lib.airspy_board_partid_serialno_read.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_AirspyReadPartidSerialNo),
    ]

except (OSError, AttributeError) as e:
    _lib_error = str(e)
    warnings.warn(
        f"libairspy not found ({e}). Airspy hardware will not be available. "
        "Unit tests can still run using MockSDRDevice.",
        ImportWarning,
        stacklevel=2
    )


class _AirspyTransfer(ctypes.Structure):
    """airspy_transfer_t C struct."""
    _fields_ = [
        ('device', ctypes.c_void_p),
        ('ctx', ctypes.c_void_p),
        ('samples', ctypes.c_void_p),
        ('sample_count', ctypes.c_int),
        ('dropped_samples', ctypes.c_uint64),
        ('sample_type', ctypes.c_int),
    ]


_CALLBACK_TYPE = ctypes.CFUNCTYPE(ctypes.c_int,
                                   ctypes.POINTER(_AirspyTransfer))

AIRSPY_SAMPLE_FLOAT32_IQ = 0
AIRSPY_SAMPLE_FLOAT32_REAL = 1
AIRSPY_SAMPLE_INT16_IQ = 2
AIRSPY_SAMPLE_INT16_REAL = 3

_DEVICE_NAME = "Airspy Mini"
_SUPPORTED_SAMPLE_RATES = (3_000_000, 6_000_000)
_FREQUENCY_RANGE = (24_000_000, 1_800_000_000)
_GAIN_STAGES = {'lna': (0, 14), 'mixer': (0, 15), 'vga': (0, 15)}


class AirspyMiniDevice(SDRDevice):
    """Airspy Mini SDR driver."""

    def __init__(self):
        self._handle = ctypes.c_void_p(None)
        self._open = False
        self._capturing = False
        self._callback_ref = None  # keep reference to prevent GC
        self._serial = "unknown"
        # Persistent streaming state for read_sync()
        self._stream_started = False
        self._stream_chunks = collections.deque()
        self._stream_total = 0  # total int16 values buffered
        self._stream_lock = threading.Lock()
        self._stream_event = threading.Event()
        self._user_callback = None  # for start_capture() async mode
        # Cumulative count of IQ sample pairs that libairspy reported as
        # dropped (e.g. due to USB overflow).  Exposed as a public
        # attribute so that higher-level code (_capture_airspy) can
        # adjust block indices to reflect real elapsed time.
        self.dropped_samples = 0

    def open(self) -> None:
        if _lib is None:
            raise DeviceNotFoundError(
                f"libairspy not available: {_lib_error}")
        ret = _lib.airspy_open(ctypes.byref(self._handle))
        if ret != 0:
            raise DeviceNotFoundError(
                f"airspy_open() failed with code {ret}. "
                "Is the Airspy Mini connected?")
        self._open = True

        # Set sample type to INT16 IQ (matches fastcapture/airspy_reader.c)
        ret = _lib.airspy_set_sample_type(
            self._handle, ctypes.c_int(AIRSPY_SAMPLE_INT16_IQ))
        if ret != 0:
            logger.warning("airspy_set_sample_type() failed: %d", ret)

        # Read serial number
        self._serial = "unknown"
        try:
            serial_info = _AirspyReadPartidSerialNo()
            ret = _lib.airspy_board_partid_serialno_read(
                self._handle, ctypes.byref(serial_info))
            if ret == 0:
                serial_parts = [serial_info.serial_no[i]
                                for i in range(4)]
                self._serial = ''.join(f'{p:08X}' for p in serial_parts)
        except Exception:
            logger.debug("Failed to read device serial number", exc_info=True)

        logger.debug("Airspy Mini opened successfully (serial=%s)",
                      self._serial)

    def close(self) -> None:
        if self._open and _lib is not None:
            self._stop_rx()
            _lib.airspy_close(self._handle)
            self._open = False
            logger.debug("Airspy Mini closed")

    def get_info(self) -> DeviceInfo:
        serial = getattr(self, '_serial', 'unknown')
        return DeviceInfo(
            name=_DEVICE_NAME,
            serial=serial,
            supported_sample_rates=_SUPPORTED_SAMPLE_RATES,
            frequency_range=_FREQUENCY_RANGE,
            bit_depth=12,
            sample_format=SampleFormat.INT16,
            max_gain_stages={k: v[1] for k, v in _GAIN_STAGES.items()},
        )

    def set_sample_rate(self, rate: int) -> None:
        if rate not in _SUPPORTED_SAMPLE_RATES:
            raise DeviceConfigError(
                f"Sample rate {rate} not supported. "
                f"Valid rates: {_SUPPORTED_SAMPLE_RATES}")
        self._check_open()
        ret = _lib.airspy_set_samplerate(self._handle, ctypes.c_uint32(rate))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_samplerate() failed: {ret}")

    def set_center_freq(self, freq: int) -> None:
        self._check_open()
        min_f, max_f = _FREQUENCY_RANGE
        if not (min_f <= freq <= max_f):
            raise DeviceConfigError(
                f"Frequency {freq} Hz out of range "
                f"[{min_f}, {max_f}]")
        ret = _lib.airspy_set_freq(self._handle, ctypes.c_uint32(freq))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_freq() failed: {ret}")

    def set_gain(self, gain_type: str, value: int) -> None:
        self._check_open()
        if gain_type not in _GAIN_STAGES:
            raise DeviceConfigError(
                f"Unknown gain type '{gain_type}'. "
                f"Valid types: {list(_GAIN_STAGES.keys())}")
        min_v, max_v = _GAIN_STAGES[gain_type]
        if not (min_v <= value <= max_v):
            raise DeviceConfigError(
                f"Gain {value} for '{gain_type}' out of range "
                f"[{min_v}, {max_v}]")
        if gain_type == 'lna':
            ret = _lib.airspy_set_lna_gain(self._handle,
                                            ctypes.c_uint8(value))
        elif gain_type == 'mixer':
            ret = _lib.airspy_set_mixer_gain(self._handle,
                                              ctypes.c_uint8(value))
        else:
            ret = _lib.airspy_set_vga_gain(self._handle,
                                            ctypes.c_uint8(value))
        if ret != 0:
            raise DeviceConfigError(
                f"airspy_set_{gain_type}_gain() failed: {ret}")

    def set_bias_tee(self, enabled: bool) -> None:
        self._check_open()
        ret = _lib.airspy_set_rf_bias(self._handle,
                                       ctypes.c_uint8(1 if enabled else 0))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_rf_bias() failed: {ret}")

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        self._check_open()
        if self._stream_started:
            raise DeviceCaptureError(
                "Cannot start_capture() while read_sync() streaming is active. "
                "Call stop_capture() first.")
        self._user_callback = callback
        self._start_rx()

    def stop_capture(self) -> None:
        self._stop_rx()
        self._user_callback = None

    def _start_rx(self) -> None:
        """Start the Airspy RX stream (called once, shared by both modes)."""
        if self._capturing:
            return
        self._check_open()

        def _c_callback(transfer_ptr):
            t = transfer_ptr.contents
            count = t.sample_count * 2  # I and Q interleaved
            buf = (ctypes.c_int16 * count).from_address(
                ctypes.cast(t.samples, ctypes.c_void_p).value)
            arr = np.frombuffer(buf, dtype=np.int16).copy()
            # Track samples dropped by hardware (USB overflow, etc.)
            if t.dropped_samples > 0:
                self.dropped_samples += t.dropped_samples
                logger.debug("Airspy dropped %d samples (total %d)",
                             t.dropped_samples, self.dropped_samples)
            # Route to user callback or internal stream buffer
            if self._user_callback is not None:
                self._user_callback(arr)
            else:
                with self._stream_lock:
                    self._stream_chunks.append(arr)
                    self._stream_total += len(arr)
                    self._stream_event.set()
            return 0

        self._callback_ref = _CALLBACK_TYPE(_c_callback)
        ret = _lib.airspy_start_rx(self._handle, self._callback_ref, None)
        if ret != 0:
            raise DeviceCaptureError(f"airspy_start_rx() failed: {ret}")
        self._capturing = True

    def _stop_rx(self) -> None:
        """Stop the Airspy RX stream."""
        if self._capturing and _lib is not None:
            _lib.airspy_stop_rx(self._handle)
            self._capturing = False
        self._stream_started = False
        self.dropped_samples = 0
        with self._stream_lock:
            self._stream_chunks.clear()
            self._stream_total = 0
            self._stream_event.clear()

    def read_sync(self, num_samples: int) -> np.ndarray:
        """Read samples synchronously via persistent streaming.

        On the first call, ``airspy_start_rx()`` is invoked once.  Subsequent
        calls drain samples from an internal ring buffer without restarting
        the hardware stream.  This avoids the ``-1000`` error that occurs when
        ``airspy_start_rx()`` is called repeatedly.

        Parameters
        ----------
        num_samples : int
            Number of I/Q sample pairs to read.

        Returns
        -------
        np.ndarray
            Interleaved int16 I/Q array of length ``num_samples * 2``.
        """
        self._check_open()
        if not self._stream_started:
            self._user_callback = None  # use internal buffer mode
            self._start_rx()
            self._stream_started = True

        needed = num_samples * 2  # int16 values (I + Q interleaved)
        deadline = time.monotonic() + 10.0

        while True:
            with self._stream_lock:
                if self._stream_total >= needed:
                    parts = []
                    remaining = needed
                    while remaining > 0:
                        chunk = self._stream_chunks[0]
                        if len(chunk) <= remaining:
                            parts.append(self._stream_chunks.popleft())
                            self._stream_total -= len(chunk)
                            remaining -= len(chunk)
                        else:
                            parts.append(chunk[:remaining])
                            self._stream_chunks[0] = chunk[remaining:]
                            self._stream_total -= remaining
                            remaining = 0
                    self._stream_event.clear()
                    return np.concatenate(parts)

            wait_time = deadline - time.monotonic()
            if wait_time <= 0:
                with self._stream_lock:
                    have = self._stream_total // 2
                raise DeviceCaptureError(
                    f"read_sync timed out after 10 s: "
                    f"collected {have}/{num_samples} samples")
            self._stream_event.wait(timeout=min(wait_time, 0.05))
            self._stream_event.clear()

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    def __del__(self):
        try:
            if self._open:
                self.close()
        except Exception:
            pass

    def _check_open(self):
        if not self._open:
            raise DeviceError("Device is not open. Call open() first.")
        if _lib is None:
            raise DeviceNotFoundError("libairspy not available")
