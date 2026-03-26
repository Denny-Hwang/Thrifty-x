# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Airspy R2 SDR driver — extends AirspyMiniDevice."""

from thriftyx.hal.airspy_mini import AirspyMiniDevice, _lib, _lib_error
from thriftyx.hal.base import DeviceInfo, SampleFormat
from thriftyx.exceptions import DeviceNotFoundError

_DEVICE_NAME = "Airspy R2"
_SUPPORTED_SAMPLE_RATES = (2_500_000, 10_000_000)
_FREQUENCY_RANGE = (24_000_000, 1_800_000_000)
_GAIN_STAGES = {'lna': (0, 14), 'mixer': (0, 15), 'vga': (0, 15)}


class AirspyR2Device(AirspyMiniDevice):
    """Airspy R2 SDR driver.

    Inherits from AirspyMiniDevice; overrides device info and sample rates.
    """

    def get_info(self) -> DeviceInfo:
        return DeviceInfo(
            name=_DEVICE_NAME,
            serial="unknown",
            supported_sample_rates=_SUPPORTED_SAMPLE_RATES,
            frequency_range=_FREQUENCY_RANGE,
            bit_depth=12,
            sample_format=SampleFormat.INT16,
            max_gain_stages={k: v[1] for k, v in _GAIN_STAGES.items()},
        )

    def set_sample_rate(self, rate: int) -> None:
        from thriftyx.exceptions import DeviceConfigError
        if rate not in _SUPPORTED_SAMPLE_RATES:
            raise DeviceConfigError(
                f"Sample rate {rate} not supported by Airspy R2. "
                f"Valid rates: {_SUPPORTED_SAMPLE_RATES}")
        self._check_open()
        import ctypes
        ret = _lib.airspy_set_samplerate(self._handle, ctypes.c_uint32(rate))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_samplerate() failed: {ret}")
