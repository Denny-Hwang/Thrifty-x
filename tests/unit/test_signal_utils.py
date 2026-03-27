# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for signal_utils module."""

import numpy as np
import pytest

from thriftyx.signal_utils import Signal, time_shift


def test_signal_fft_ifft_roundtrip():
    """Test that FFT -> IFFT recovers the original signal."""
    data = np.array([1, 2, 3, 4], dtype=np.complex64)
    sig = Signal(data)
    recovered = sig.fft.ifft
    np.testing.assert_allclose(np.array(recovered), data, atol=1e-6)


def test_time_shift_zero():
    """A zero shift should return the original signal."""
    data = np.array([1, 0, 0, 0], dtype=np.complex64)
    shifted = time_shift(data, 0.0)
    np.testing.assert_allclose(shifted, data, atol=1e-6)


def test_time_shift_integer():
    """An integer shift should circularly rotate the signal."""
    data = np.array([1, 2, 3, 4], dtype=np.complex64)
    shifted = time_shift(data, 1.0)
    expected = np.array([4, 1, 2, 3], dtype=np.complex64)
    np.testing.assert_allclose(shifted, expected, atol=1e-5)
