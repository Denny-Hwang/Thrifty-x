# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Abstract SDR device interface for Thrifty-X."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np


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
    """Abstract base class for SDR devices."""

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
    def set_sample_rate(self, rate: int) -> None:
        """Set sample rate in samples per second."""

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
            Interleaved I/Q samples as int16 array.
        """

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the device is open."""

    @property
    @abstractmethod
    def is_capturing(self) -> bool:
        """Whether async capture is active."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.is_capturing:
            self.stop_capture()
        self.close()
        return False
