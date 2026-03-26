# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for HAL base classes."""

import pytest

from thriftyx.hal.base import SDRDevice, DeviceInfo, SampleFormat
from tests.mocks.mock_device import MockSDRDevice


def test_sampleformat_values():
    assert SampleFormat.INT16 != SampleFormat.UINT8
    assert SampleFormat.INT16 != SampleFormat.FLOAT32


def test_device_info_creation():
    info = DeviceInfo(
        name="Test Device",
        serial="TEST-001",
        supported_sample_rates=(3_000_000, 6_000_000),
        frequency_range=(24_000_000, 1_800_000_000),
        bit_depth=12,
        sample_format=SampleFormat.INT16,
        max_gain_stages={'lna': 14, 'mixer': 15, 'vga': 15},
    )
    assert info.name == "Test Device"
    assert info.bit_depth == 12
    assert info.sample_format == SampleFormat.INT16


def test_device_info_frozen():
    info = DeviceInfo(
        name="Test",
        serial="0",
        supported_sample_rates=(6_000_000,),
        frequency_range=(24_000_000, 1_800_000_000),
        bit_depth=12,
        sample_format=SampleFormat.INT16,
        max_gain_stages={},
    )
    with pytest.raises(Exception):
        info.name = "Changed"  # frozen dataclass


def test_abstract_base_cannot_instantiate():
    with pytest.raises(TypeError):
        SDRDevice()


def test_mock_device_open_close():
    dev = MockSDRDevice()
    assert not dev.is_open
    dev.open()
    assert dev.is_open
    dev.close()
    assert not dev.is_open


def test_mock_device_context_manager():
    with MockSDRDevice() as dev:
        assert dev.is_open
    assert not dev.is_open


def test_mock_device_read_sync():
    import numpy as np
    dev = MockSDRDevice()
    dev.open()
    data = dev.read_sync(1024)
    dev.close()
    assert isinstance(data, np.ndarray)
    assert data.dtype == np.int16
    assert len(data) == 1024 * 2  # interleaved I/Q


def test_mock_device_get_info():
    dev = MockSDRDevice()
    info = dev.get_info()
    assert isinstance(info, DeviceInfo)
    assert info.bit_depth == 12
    assert info.sample_format == SampleFormat.INT16
