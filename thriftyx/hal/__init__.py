# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Hardware Abstraction Layer for SDR devices.

Driver modules are imported lazily: importing :mod:`thriftyx.hal` (or
the pure-data :mod:`thriftyx.hal.profiles`, which settings and the
config validator use) does not load libairspy.  The driver loads on
first use of a name such as :func:`create_device`.
"""

import importlib
from typing import TYPE_CHECKING, Any

from thriftyx.hal.base import SDRDevice, DeviceInfo, SampleFormat
from thriftyx.hal.profiles import (DeviceProfile, PROFILES, get_profile)

_LAZY = {
    'AirspyMiniDevice': 'thriftyx.hal.airspy_mini',
    'list_airspy_serials': 'thriftyx.hal.airspy_mini',
    'parse_airspy_serial': 'thriftyx.hal.airspy_mini',
    'AirspyR2Device': 'thriftyx.hal.airspy_r2',
    'create_device': 'thriftyx.hal.device_factory',
    'register_device': 'thriftyx.hal.device_factory',
    'available_devices': 'thriftyx.hal.device_factory',
}

if TYPE_CHECKING:
    from thriftyx.hal.airspy_mini import (AirspyMiniDevice,
                                           list_airspy_serials,
                                           parse_airspy_serial)
    from thriftyx.hal.airspy_r2 import AirspyR2Device
    from thriftyx.hal.device_factory import (create_device, register_device,
                                             available_devices)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


__all__ = [
    'SDRDevice',
    'DeviceInfo',
    'SampleFormat',
    'DeviceProfile',
    'PROFILES',
    'get_profile',
    'AirspyMiniDevice',
    'AirspyR2Device',
    'create_device',
    'register_device',
    'available_devices',
    'list_airspy_serials',
    'parse_airspy_serial',
]
