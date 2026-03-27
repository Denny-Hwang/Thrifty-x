# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Mock SDR device for testing without hardware."""

import threading
import time
from typing import Callable

import numpy as np

from thriftyx.hal.base import SDRDevice, DeviceInfo, SampleFormat


class MockSDRDevice(SDRDevice):
    """Synthetic SDR device for unit testing.

    Generates int16 I/Q samples with configurable carrier and noise.
    Works without any hardware.
    """

    def __init__(self, sample_rate: int = 6_000_000,
                 center_freq: int = 433_000_000,
                 noise_amplitude: float = 100.0,
                 carrier_freq_offset: float = 0.0,
                 carrier_amplitude: float = 0.0,
                 buffer_size: int = 65536):
        self._sample_rate = sample_rate
        self._center_freq = center_freq
        self._noise_amplitude = noise_amplitude
        self._carrier_freq_offset = carrier_freq_offset
        self._carrier_amplitude = carrier_amplitude
        self._buffer_size = buffer_size
        self._open = False
        self._capturing = False
        self._capture_thread = None
        self._lna_gain = 0
        self._mixer_gain = 0
        self._vga_gain = 0
        self._bias_tee = False
        self._sample_count = 0
        self._rng = np.random.default_rng(seed=42)

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        if self._capturing:
            self.stop_capture()
        self._open = False

    def get_info(self) -> DeviceInfo:
        return DeviceInfo(
            name="Mock SDR",
            serial="MOCK-001",
            supported_sample_rates=(3_000_000, 6_000_000),
            frequency_range=(24_000_000, 1_800_000_000),
            bit_depth=12,
            sample_format=SampleFormat.INT16,
            max_gain_stages={'lna': 14, 'mixer': 15, 'vga': 15},
        )

    def set_sample_rate(self, rate: int) -> None:
        self._sample_rate = rate

    def set_center_freq(self, freq: int) -> None:
        self._center_freq = freq

    def set_gain(self, gain_type: str, value: int) -> None:
        setattr(self, f'_{gain_type}_gain', value)

    def set_bias_tee(self, enabled: bool) -> None:
        self._bias_tee = enabled

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        self._capturing = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(callback,),
            daemon=True)
        self._capture_thread.start()

    def stop_capture(self) -> None:
        self._capturing = False
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

    def read_sync(self, num_samples: int) -> np.ndarray:
        return self._generate_samples(num_samples)

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    def _generate_samples(self, num_samples: int) -> np.ndarray:
        """Generate synthetic int16 I/Q samples."""
        n = np.arange(num_samples) + self._sample_count
        self._sample_count += num_samples

        # Noise
        noise_i = self._rng.normal(0, self._noise_amplitude, num_samples)
        noise_q = self._rng.normal(0, self._noise_amplitude, num_samples)

        # Carrier
        if self._carrier_amplitude > 0:
            phase = 2 * np.pi * self._carrier_freq_offset * n / self._sample_rate
            carrier_i = self._carrier_amplitude * np.cos(phase)
            carrier_q = self._carrier_amplitude * np.sin(phase)
        else:
            carrier_i = 0.0
            carrier_q = 0.0

        i_samples = np.clip(noise_i + carrier_i, -2048, 2047).astype(np.int16)
        q_samples = np.clip(noise_q + carrier_q, -2048, 2047).astype(np.int16)

        # Interleave I/Q
        result = np.empty(num_samples * 2, dtype=np.int16)
        result[0::2] = i_samples
        result[1::2] = q_samples
        return result

    def _capture_loop(self, callback: Callable[[np.ndarray], None]) -> None:
        while self._capturing:
            buf = self._generate_samples(self._buffer_size)
            callback(buf)
            # Simulate real-time pacing
            time.sleep(self._buffer_size / self._sample_rate)
