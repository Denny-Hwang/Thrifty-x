# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Abstract SDR device interface for Thrifty-X."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable, ClassVar, Literal

import numpy as np

from thriftyx.exceptions import DeviceConfigError

if TYPE_CHECKING:
    from thriftyx.hal.profiles import DeviceProfile

logger = logging.getLogger(__name__)


class SampleFormat(Enum):
    """ADC sample format."""
    INT16 = auto()    # Airspy 12-bit signed (stored in int16)
    UINT8 = auto()    # RTL-SDR legacy 8-bit unsigned
    FLOAT32 = auto()  # Normalized float


@dataclass(frozen=True)
class DeviceInfo:
    """Information about an SDR device."""
    name: str
    serial: str
    supported_sample_rates: tuple
    frequency_range: tuple  # (min_hz, max_hz)
    bit_depth: int
    sample_format: SampleFormat
    max_gain_stages: dict  # {'lna': 14, 'mixer': 15, 'vga': 15}


class SDRDevice(ABC):
    """Abstract base class for SDR devices.

    Class attributes
    ----------------
    PROFILE : DeviceProfile
        Hardware facts for the model (see :mod:`thriftyx.hal.profiles`).
        Drivers read ranges from it rather than keeping their own copies.

    Attributes
    ----------
    dropped_samples : int
        I/Q sample pairs lost since streaming started (USB overflow or a
        full internal buffer).  ``read_sync`` returns lost samples as
        zeros, so the stream stays time-contiguous (sample *k* is *k*
        sample periods after the first) and block indices stay aligned;
        the counter is for reporting.
    last_read_time : float or None
        Wall-clock time at which the last sample returned by
        ``read_sync`` arrived from the device, or ``None`` when the
        driver does not track it (capture then stamps blocks when it
        processes them).
    """

    PROFILE: ClassVar['DeviceProfile']
    dropped_samples: int = 0
    last_read_time: 'float | None' = None

    @abstractmethod
    def open(self) -> None:
        """Open and initialize the device."""

    @abstractmethod
    def close(self) -> None:
        """Close the device and release resources."""

    @abstractmethod
    def get_info(self) -> DeviceInfo:
        """Return device information."""

    @abstractmethod
    def set_sample_rate(self, rate: int) -> 'int | None':
        """Set sample rate in samples per second.

        Returns
        -------
        int or None
            The rate actually configured, when the driver snaps *rate*
            to one the hardware supports; ``None`` means *rate* itself.
        """

    @abstractmethod
    def set_center_freq(self, freq: int) -> None:
        """Set center frequency in Hz."""

    @abstractmethod
    def set_gain(self, gain_type: str, value: int) -> None:
        """Set gain for a specific stage.

        Parameters
        ----------
        gain_type : str
            Gain stage name ('lna', 'mixer', 'vga').
        value : int
            Gain index value.
        """

    @abstractmethod
    def set_bias_tee(self, enabled: bool) -> None:
        """Enable or disable bias tee voltage on antenna port."""

    def apply_gain_mode(self, mode: str, *,
                        lna: 'int | None' = None,
                        mixer: 'int | None' = None,
                        vga: 'int | None' = None,
                        lna_agc: bool = False,
                        mixer_agc: bool = False,
                        combined: 'int | None' = None) -> None:
        """Apply a gain configuration.

        The default supports ``'manual'`` mode without AGC by calling
        :meth:`set_gain` for each stage that is given.  Devices with
        preset gain ladders or AGC loops override this.

        Raises
        ------
        DeviceConfigError
            For a preset mode or an AGC request the device cannot honour.
        """
        if mode != 'manual' or lna_agc or mixer_agc:
            raise DeviceConfigError(
                f"{type(self).__name__} supports only gain_mode='manual' "
                "without AGC")
        for stage, value in (('lna', lna), ('mixer', mixer), ('vga', vga)):
            if value is not None:
                self.set_gain(stage, int(value))

    def set_packing(self, enabled: bool) -> None:
        """Enable or disable USB sample packing, where supported.

        The default has no packing: disabling is a no-op and enabling
        logs a warning, since capture still works unpacked.
        """
        if enabled:
            logger.warning("%s does not support sample packing; ignored",
                           type(self).__name__)

    def read_tuner_registers(self, first: int = 0x00,
                             last: int = 0x1F) -> dict[int, int]:
        """Read raw tuner registers *first*..*last* (default: none).

        Raises
        ------
        DeviceConfigError
            The device gives no register access.
        """
        raise DeviceConfigError(
            f"{type(self).__name__} gives no tuner register access")

    def write_tuner_register(self, reg: int, value: int) -> None:
        """Write one raw tuner register (default: unsupported).

        Raises
        ------
        DeviceConfigError
            The device gives no register access.
        """
        raise DeviceConfigError(
            f"{type(self).__name__} gives no tuner register access")

    def discard_buffered(self) -> None:
        """Drop samples queued for ``read_sync`` (default: none queued).

        Live displays call this before reading so they show the newest
        samples rather than a backlog.
        """
        return None

    def pause_buffering(self) -> None:
        """Stop queueing samples for ``read_sync`` while nobody reads.

        The hardware keeps streaming; the driver just drops what arrives
        (default: nothing is queued).  Use it whenever the consumer will
        be away for longer than the driver's buffer holds -- waiting for
        an operator, slow control transfers -- so the queue cannot
        overflow and pollute the drop counters.  Losses the hardware
        itself reports keep counting in ``dropped_samples``.
        :meth:`resume_buffering` ends the pause.

        Calling ``read_sync`` while paused is unsupported: the Airspy
        driver raises :class:`~thriftyx.exceptions.DeviceCaptureError`.
        Like :meth:`discard_buffered`, pausing breaks the time-contiguity
        of the stream.  A driver that queues samples must override both
        this and :meth:`resume_buffering`; the defaults here are for
        drivers that queue nothing.
        """
        return None

    def resume_buffering(self) -> int:
        """Resume queueing from an empty queue; return the drop counter.

        Whatever the driver had queued is gone, and the returned
        ``dropped_samples`` is the counter read together with that
        clearing, so ``dropped_samples - returned`` counts the losses
        the driver saw after the boundary.  The boundary is exact between
        the driver's counter and its queue; samples and loss reports
        already in the hardware or libusb/libairspy pipeline may still
        arrive after it (a late loss report is counted in the new window,
        which errs on the safe side).

        The default only calls :meth:`discard_buffered` and reads the
        counter, which is all a driver without a queue can do.
        """
        self.discard_buffered()
        return self.dropped_samples

    @abstractmethod
    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        """Start asynchronous sample capture.

        Parameters
        ----------
        callback : callable
            Function called with each buffer of samples (int16 ndarray).
        """

    @abstractmethod
    def stop_capture(self) -> None:
        """Stop asynchronous sample capture."""

    @abstractmethod
    def read_sync(self, num_samples: int) -> np.ndarray:
        """Read samples synchronously.

        Parameters
        ----------
        num_samples : int
            Number of I/Q sample pairs to read.

        Returns
        -------
        np.ndarray
            Interleaved I/Q samples as int16 array; samples lost in
            transit are zeros (see ``dropped_samples``).
        """

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the device is open."""

    @property
    @abstractmethod
    def is_capturing(self) -> bool:
        """Whether async capture is active."""

    def __enter__(self) -> 'SDRDevice':
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object,
                 exc_tb: object) -> Literal[False]:
        try:
            if self.is_open:
                if self.is_capturing:
                    self.stop_capture()
                self.close()
        except Exception:
            pass
        return False
