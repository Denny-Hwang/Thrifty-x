# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Signal generation utilities for testing."""

import numpy as np


def generate_carrier(freq_hz: float, sample_rate: float,
                     num_samples: int,
                     amplitude: float = 1.0) -> np.ndarray:
    """Generate a complex carrier signal.

    Returns complex64 array.
    """
    t = np.arange(num_samples) / sample_rate
    return (amplitude * np.exp(2j * np.pi * freq_hz * t)).astype(np.complex64)


def add_noise(signal: np.ndarray, snr_db: float, seed: int = 42) -> np.ndarray:
    """Add Gaussian noise to achieve the given SNR in dB."""
    signal_power = np.mean(np.abs(signal) ** 2)
    snr_linear = 10 ** (snr_db / 10)
    noise_power = signal_power / snr_linear
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, np.sqrt(noise_power / 2), signal.shape) + \
            1j * rng.normal(0, np.sqrt(noise_power / 2), signal.shape)
    return (signal + noise).astype(np.complex64)


def complex_to_int16(signal: np.ndarray) -> np.ndarray:
    """Convert complex64 signal to interleaved int16 (12-bit range).

    Scales signal so that amplitude 1.0 maps to ±2047.
    """
    max_val = np.max(np.abs(signal))
    if max_val > 0:
        scale = 2047.0 / max_val
    else:
        scale = 1.0
    i_samples = np.clip(np.real(signal) * scale, -2048, 2047).astype(np.int16)
    q_samples = np.clip(np.imag(signal) * scale, -2048, 2047).astype(np.int16)
    result = np.empty(len(signal) * 2, dtype=np.int16)
    result[0::2] = i_samples
    result[1::2] = q_samples
    return result
