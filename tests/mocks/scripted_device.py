# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""A complete SDRDevice that replays scripted samples.

Capture tests drive ``_capture_airspy`` through the HAL contract with
this device instead of ad-hoc duck-typed fakes, so a capture change that
steps outside :class:`~thriftyx.hal.base.SDRDevice` fails here too.
"""

from typing import Callable, Iterable

import numpy as np

from thriftyx.exceptions import DeviceCaptureError, DeviceConfigError
from thriftyx.hal.base import DeviceInfo, SDRDevice
from thriftyx.hal.profiles import AIRSPY_MINI, DeviceProfile


class ScriptedSDRDevice(SDRDevice):
    """SDRDevice whose ``read_sync`` returns scripted int16 buffers.

    Parameters
    ----------
    buffers : iterable of numpy.ndarray
        Interleaved int16 I/Q returned by successive ``read_sync`` calls,
        regardless of the requested length.  An empty array (end of the
        script) makes capture stop.
    stream : numpy.ndarray or None
        Alternative to *buffers*: one continuous interleaved int16 I/Q
        recording, served in exactly the requested number of samples.
        ``samples_read`` counts the I/Q pairs delivered so far.
    profile : DeviceProfile
        Hardware facts reported by ``get_info``.
    fail_in : str or None
        Name of a method that raises *exc* when called ("open",
        "set_sample_rate", "set_gain", "read_sync", ...).
    exc : type
        Exception raised by *fail_in*.
    dropped_samples : int
        Initial value of the drop counter.

    Configuration calls are recorded in ``sample_rate``, ``center_freq``,
    ``gains``, ``bias_tee``, ``packing``, ``applied_gain_mode`` and
    ``applied_kwargs``; ``closed`` records teardown.
    """

    def __init__(self, buffers: Iterable[np.ndarray] = (), *,
                 stream: 'np.ndarray | None' = None,
                 profile: DeviceProfile = AIRSPY_MINI,
                 fail_in: 'str | None' = None,
                 exc: type = DeviceConfigError,
                 dropped_samples: int = 0) -> None:
        self.PROFILE = profile  # type: ignore[misc]
        self._buffers = [np.asarray(b, dtype=np.int16) for b in buffers]
        self._stream = (None if stream is None
                        else np.asarray(stream, dtype=np.int16))
        self.samples_read = 0
        self._fail_in = fail_in
        self._exc = exc
        self.dropped_samples = dropped_samples
        self._open = False
        self._capturing = False
        self.closed = False
        self.sample_rate: 'int | None' = None
        self.center_freq: 'int | None' = None
        self.gains: dict[str, int] = {}
        self.bias_tee: 'bool | None' = None
        self.packing: 'bool | None' = None
        self.applied_gain_mode: 'str | None' = None
        self.applied_kwargs: 'dict | None' = None

    def _maybe_fail(self, name: str) -> None:
        if self._fail_in == name:
            raise self._exc(f'{name} failure')

    def open(self) -> None:
        self._open = True
        self._maybe_fail('open')

    def close(self) -> None:
        self._open = False
        self.closed = True

    def get_info(self) -> DeviceInfo:
        profile = self.PROFILE
        return DeviceInfo(
            name=profile.name, serial='SCRIPTED',
            supported_sample_rates=profile.sample_rates,
            frequency_range=profile.frequency_range,
            bit_depth=profile.bit_depth,
            sample_format=profile.sample_format,
            max_gain_stages={k: v[1] for k, v in profile.gain_stages.items()})

    def set_sample_rate(self, rate: int) -> None:
        self._maybe_fail('set_sample_rate')
        self.sample_rate = rate

    def set_center_freq(self, freq: int) -> None:
        self._maybe_fail('set_center_freq')
        self.center_freq = freq

    def set_gain(self, gain_type: str, value: int) -> None:
        self._maybe_fail('set_gain')
        self.gains[gain_type] = value

    def set_bias_tee(self, enabled: bool) -> None:
        self._maybe_fail('set_bias_tee')
        self.bias_tee = bool(enabled)

    def set_packing(self, enabled: bool) -> None:
        self._maybe_fail('set_packing')
        self.packing = bool(enabled)

    def apply_gain_mode(self, mode: str, **kwargs: object) -> None:  # type: ignore[override]
        self._maybe_fail('apply_gain_mode')
        self._maybe_fail('set_gain')
        self.applied_gain_mode = mode
        self.applied_kwargs = kwargs

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        raise DeviceCaptureError("ScriptedSDRDevice supports read_sync only")

    def stop_capture(self) -> None:
        self._capturing = False

    def read_sync(self, num_samples: int) -> np.ndarray:
        self._maybe_fail('read_sync')
        if self._stream is not None:
            start = self.samples_read * 2
            chunk = self._stream[start:start + num_samples * 2]
            if len(chunk) < num_samples * 2:
                return np.array([], dtype=np.int16)
            self.samples_read += num_samples
            return chunk
        if not self._buffers:
            return np.array([], dtype=np.int16)
        return self._buffers.pop(0)

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def is_capturing(self) -> bool:
        return self._capturing
