# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Factory for creating SDR device instances."""

from thriftyx.hal.base import SDRDevice
from thriftyx.hal.airspy_mini import AirspyMiniDevice
from thriftyx.hal.airspy_r2 import AirspyR2Device
from thriftyx.exceptions import DeviceNotFoundError

_REGISTRY: dict[str, type[SDRDevice]] = {
    'airspy_mini': AirspyMiniDevice,
    'airspy_r2': AirspyR2Device,
}


def create_device(device_type: str = 'airspy_mini', **kwargs) -> SDRDevice:
    """Create an SDR device instance.

    Parameters
    ----------
    device_type : str
        Device type key. One of: 'airspy_mini', 'airspy_r2'.
    **kwargs
        Additional keyword arguments passed to the device constructor.

    Returns
    -------
    SDRDevice

    Raises
    ------
    DeviceNotFoundError
        If device_type is not registered.
    """
    if device_type not in _REGISTRY:
        raise DeviceNotFoundError(
            f"Unknown device type '{device_type}'. "
            f"Available devices: {list(_REGISTRY.keys())}")
    cls = _REGISTRY[device_type]
    return cls(**kwargs)


def register_device(name: str, cls: type[SDRDevice]) -> None:
    """Register a custom device type.

    Parameters
    ----------
    name : str
        Device type key.
    cls : type
        Class that implements SDRDevice.
    """
    _REGISTRY[name] = cls


def available_devices() -> list[str]:
    """Return list of registered device type names."""
    return list(_REGISTRY.keys())
