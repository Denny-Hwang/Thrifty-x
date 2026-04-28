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

    # airspy_open_sn: open by 64-bit serial number.  Available since
    # libairspy 1.0.9.
    _lib.airspy_open_sn.restype = ctypes.c_int
    _lib.airspy_open_sn.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.c_uint64]

    # airspy_list_devices: enumerate connected Airspy devices.
    # Returns the number of devices; fills `serials` (uint64_t array) up
    # to `count` entries.  Available since libairspy 1.0.9.
    _lib.airspy_list_devices.restype = ctypes.c_int
    _lib.airspy_list_devices.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                                          ctypes.c_int]

    # Library lifecycle (no-op on modern libairspy but required on older
    # builds).  Wrapped in try/except below since some distros omit them.
    try:
        _lib.airspy_init.restype = ctypes.c_int
        _lib.airspy_init.argtypes = []
        _lib.airspy_exit.restype = ctypes.c_int
        _lib.airspy_exit.argtypes = []
        _lib.airspy_init()
    except AttributeError:
        pass

    _lib.airspy_close.restype = ctypes.c_int
    _lib.airspy_close.argtypes = [ctypes.c_void_p]

    # Optional: dynamic sample-rate enumeration.  Newer libairspy exposes
    # ``airspy_get_samplerates(device, buffer, count)`` where buffer is
    # NULL on the first call to obtain the rate count.
    try:
        _lib.airspy_get_samplerates.restype = ctypes.c_int
        _lib.airspy_get_samplerates.argtypes = [ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_uint32),
                                                 ctypes.c_uint32]
    except AttributeError:
        pass

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

# Tolerance applied when validating a sample rate against the value(s)
# returned by ``airspy_get_samplerates``.  Some libairspy builds return
# slightly off values (e.g. 5_999_998 instead of 6_000_000), so a small
# tolerance avoids false rejections.
_SAMPLE_RATE_TOLERANCE_HZ = 100


def _rate_is_supported(rate: int, supported) -> bool:
    """Return True when ``rate`` matches one of the supported rates within
    the small tolerance that absorbs libairspy rounding noise."""
    rate = int(rate)
    return any(abs(rate - int(s)) <= _SAMPLE_RATE_TOLERANCE_HZ
               for s in supported)


def list_airspy_serials() -> list[int]:
    """Enumerate connected Airspy devices and return their 64-bit serials.

    Returns an empty list when libairspy is unavailable or the API is not
    present (older library builds).
    """
    if _lib is None or not hasattr(_lib, 'airspy_list_devices'):
        return []
    # First call: NULL buffer to obtain the device count.
    count = _lib.airspy_list_devices(None, 0)
    if count <= 0:
        return []
    buf = (ctypes.c_uint64 * count)()
    got = _lib.airspy_list_devices(buf, count)
    return [int(buf[i]) for i in range(min(got, count))]


def parse_airspy_serial(value) -> int:
    """Convert a CLI serial argument to ``uint64`` for ``airspy_open_sn``.

    Accepts:
      - int           (returned as-is)
      - hex string    e.g. ``"0x1234ABCD..."`` or ``"1234ABCDDEADBEEF"``
      - decimal str   e.g. ``"123456789"``
    """
    if isinstance(value, int):
        return int(value) & 0xFFFFFFFFFFFFFFFF
    if value is None:
        raise ValueError("Airspy serial value is None")
    text = str(value).strip().lower().replace('_', '')
    if text.startswith('0x'):
        text = text[2:]
    # Heuristic: if string contains any non-decimal digit, treat as hex.
    if any(c in 'abcdef' for c in text):
        return int(text, 16) & 0xFFFFFFFFFFFFFFFF
    # If purely numeric and exactly 16 chars, treat as hex (e.g. board ID
    # printed by `airspy_info`).
    if len(text) == 16 and all(c in '0123456789abcdef' for c in text):
        return int(text, 16) & 0xFFFFFFFFFFFFFFFF
    return int(text) & 0xFFFFFFFFFFFFFFFF


class AirspyMiniDevice(SDRDevice):
    """Airspy Mini SDR driver.

    Class attributes
    ----------------
    _SUPPORTED_SAMPLE_RATES : tuple of int
        Hardcoded fallback rates used when libairspy does not expose
        ``airspy_get_samplerates``.  The dynamic query result, when
        available, is stored on the *instance* during ``open()``.

    Parameters
    ----------
    serial : int | str | None
        Optional 64-bit Airspy serial number to open by.  Accepts a hex
        string, decimal string, or integer.  When ``None`` (default) and
        ``device_index`` is also ``None``, the first available device is
        opened.
    device_index : int | None
        When ``serial`` is not given, select a device by its index in
        ``airspy_list_devices``.  ``0`` is the first connected device.
    """

    _SUPPORTED_SAMPLE_RATES = _SUPPORTED_SAMPLE_RATES

    def __init__(self, serial=None, device_index=None):
        self._handle = ctypes.c_void_p(None)
        self._open = False
        self._capturing = False
        self._callback_ref = None  # keep reference to prevent GC
        self._serial = "unknown"
        self._requested_serial = serial
        self._requested_index = device_index
        # Populated by ``open()``; defaults to the class-level fallback so
        # that ``set_sample_rate`` works even when called before open().
        self._supported_sample_rates = type(self)._SUPPORTED_SAMPLE_RATES
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

    def _resolve_open_serial(self):
        """Return a uint64 serial to open with, or ``None`` for default open."""
        if self._requested_serial is not None:
            return parse_airspy_serial(self._requested_serial)
        if self._requested_index is not None:
            serials = list_airspy_serials()
            if not serials:
                raise DeviceNotFoundError(
                    "No Airspy devices found while resolving "
                    f"device_index={self._requested_index}.")
            if not (0 <= int(self._requested_index) < len(serials)):
                raise DeviceNotFoundError(
                    f"device_index {self._requested_index} out of range; "
                    f"{len(serials)} Airspy device(s) connected.")
            return serials[int(self._requested_index)]
        return None

    def open(self) -> None:
        if _lib is None:
            raise DeviceNotFoundError(
                f"libairspy not available: {_lib_error}")

        sn = self._resolve_open_serial()
        if sn is not None and hasattr(_lib, 'airspy_open_sn'):
            ret = _lib.airspy_open_sn(ctypes.byref(self._handle),
                                       ctypes.c_uint64(sn))
            if ret != 0:
                raise DeviceNotFoundError(
                    f"airspy_open_sn(0x{sn:016X}) failed with code {ret}. "
                    "Is the requested Airspy device connected?")
        else:
            if sn is not None:
                logger.warning(
                    "airspy_open_sn unavailable in this libairspy build; "
                    "falling back to airspy_open() — serial selector ignored.")
            ret = _lib.airspy_open(ctypes.byref(self._handle))
            if ret != 0:
                raise DeviceNotFoundError(
                    f"airspy_open() failed with code {ret}. "
                    "Is the Airspy device connected? "
                    "Check udev rules / user group membership.")
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

        # Refresh supported sample-rate set from the device when the
        # libairspy build exposes the enumeration API.  Hardcoded values
        # are kept as a fallback for older libraries.
        self._supported_sample_rates = self._query_supported_sample_rates()

        logger.debug("Airspy device opened (serial=%s, rates=%s)",
                      self._serial, self._supported_sample_rates)

    def _query_supported_sample_rates(self) -> tuple:
        """Query libairspy for the device's supported sample rates.

        Falls back to the class-level ``_SUPPORTED_SAMPLE_RATES`` constant
        when the API is unavailable or returns nothing.
        """
        default = type(self)._SUPPORTED_SAMPLE_RATES if hasattr(
            type(self), '_SUPPORTED_SAMPLE_RATES') else _SUPPORTED_SAMPLE_RATES
        if _lib is None or not hasattr(_lib, 'airspy_get_samplerates'):
            return default
        try:
            count = ctypes.c_uint32(0)
            ret = _lib.airspy_get_samplerates(
                self._handle, ctypes.byref(count), ctypes.c_uint32(0))
            if ret != 0 or count.value == 0:
                return default
            buf = (ctypes.c_uint32 * count.value)()
            ret = _lib.airspy_get_samplerates(self._handle, buf, count.value)
            if ret != 0:
                return default
            rates = tuple(int(buf[i]) for i in range(count.value))
            return rates if rates else default
        except Exception:
            logger.debug("airspy_get_samplerates() failed", exc_info=True)
            return default

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
            supported_sample_rates=getattr(self, '_supported_sample_rates',
                                            _SUPPORTED_SAMPLE_RATES),
            frequency_range=_FREQUENCY_RANGE,
            bit_depth=12,
            sample_format=SampleFormat.INT16,
            max_gain_stages={k: v[1] for k, v in _GAIN_STAGES.items()},
        )

    def set_sample_rate(self, rate: int) -> None:
        rates = self._supported_sample_rates
        if not _rate_is_supported(rate, rates):
            raise DeviceConfigError(
                f"Sample rate {rate} not supported. "
                f"Valid rates: {rates}")
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
        # If start_capture() is already running with a user callback, the
        # internal stream queue would never receive data — and silently
        # clobbering the user callback (the previous behaviour) hides the
        # mistake.  Refuse the call instead.
        if self._capturing and self._user_callback is not None:
            raise DeviceCaptureError(
                "read_sync() cannot be used while start_capture() is "
                "active with a user callback. Call stop_capture() first.")
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
