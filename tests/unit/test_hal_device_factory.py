# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for HAL device factory."""

import pytest

from thriftyx.hal.device_factory import (create_device, register_device,
                                          available_devices)
from thriftyx.hal.airspy_mini import AirspyMiniDevice
from thriftyx.hal.airspy_r2 import AirspyR2Device
from thriftyx.exceptions import DeviceNotFoundError
from tests.mocks.mock_device import MockSDRDevice


def test_create_device_unknown():
    with pytest.raises(DeviceNotFoundError):
        create_device('unknown_device')


def test_create_device_airspy_mini():
    dev = create_device('airspy_mini')
    assert isinstance(dev, AirspyMiniDevice)


def test_create_device_airspy_r2():
    dev = create_device('airspy_r2')
    assert isinstance(dev, AirspyR2Device)


def test_register_device():
    register_device('mock', MockSDRDevice)
    assert 'mock' in available_devices()
    dev = create_device('mock')
    assert isinstance(dev, MockSDRDevice)
    # clean up
    from thriftyx.hal.device_factory import _REGISTRY
    _REGISTRY.pop('mock', None)


def test_available_devices():
    devices = available_devices()
    assert 'airspy_mini' in devices
    assert 'airspy_r2' in devices
