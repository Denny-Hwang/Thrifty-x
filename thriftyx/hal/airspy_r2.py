# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Airspy R2 SDR driver — extends AirspyMiniDevice."""

import ctypes

from thriftyx.hal.airspy_mini import AirspyMiniDevice, _lib, _lib_error
from thriftyx.hal.base import DeviceInfo, SampleFormat
from thriftyx.exceptions import DeviceNotFoundError, DeviceConfigError

_DEVICE_NAME = "Airspy R2"
_SUPPORTED_SAMPLE_RATES = (2_500_000, 10_000_000)
_FREQUENCY_RANGE = (24_000_000, 1_800_000_000)
_GAIN_STAGES = {'lna': (0, 15), 'mixer': (0, 15), 'vga': (0, 15)}

VALID_SAMPLE_RATES = _SUPPORTED_SAMPLE_RATES


class AirspyR2Device(AirspyMiniDevice):
    """Airspy R2 SDR driver.

    Inherits from AirspyMiniDevice; overrides device info, sample rates,
    gain stages, and frequency range with R2-specific values.
    """

    VALID_SAMPLE_RATES = _SUPPORTED_SAMPLE_RATES
    _GAIN_STAGES = _GAIN_STAGES
    _FREQUENCY_RANGE = _FREQUENCY_RANGE

    @property
    def device_info(self) -> DeviceInfo:
        """Return R2-specific device information."""
        return self.get_info()

    def get_info(self) -> DeviceInfo:
        serial = getattr(self, '_serial', 'unknown')
        return DeviceInfo(
            name=_DEVICE_NAME,
            serial=serial,
            supported_sample_rates=_SUPPORTED_SAMPLE_RATES,
            frequency_range=_FREQUENCY_RANGE,
            bit_depth=12,
            sample_format=SampleFormat.INT16,
            max_gain_stages={k: v[1] for k, v in _GAIN_STAGES.items()},
        )

    def set_sample_rate(self, rate: int) -> None:
        if rate not in _SUPPORTED_SAMPLE_RATES:
            raise DeviceConfigError(
                f"Sample rate {rate} not supported by Airspy R2. "
                f"Valid rates: {_SUPPORTED_SAMPLE_RATES}")
        self._check_open()
        ret = _lib.airspy_set_samplerate(self._handle, ctypes.c_uint32(rate))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_samplerate() failed: {ret}")

    def set_center_freq(self, freq: int) -> None:
        self._check_open()
        min_f, max_f = _FREQUENCY_RANGE
        if not (min_f <= freq <= max_f):
            raise DeviceConfigError(
                f"Frequency {freq} Hz out of range "
                f"[{min_f}, {max_f}]")
        ret = _lib.airspy_set_freq(self._handle, ctypes.c_uint32(freq))
        if ret != 0:
            raise DeviceConfigError(f"airspy_set_freq() failed: {ret}")

    def set_gain(self, gain_type: str, value: int) -> None:
        self._check_open()
        if gain_type not in _GAIN_STAGES:
            raise DeviceConfigError(
                f"Unknown gain type '{gain_type}'. "
                f"Valid types: {list(_GAIN_STAGES.keys())}")
        min_v, max_v = _GAIN_STAGES[gain_type]
        if not (min_v <= value <= max_v):
            raise DeviceConfigError(
                f"Gain {value} for '{gain_type}' out of range "
                f"[{min_v}, {max_v}]")
        if gain_type == 'lna':
            ret = _lib.airspy_set_lna_gain(self._handle,
                                            ctypes.c_uint8(value))
        elif gain_type == 'mixer':
            ret = _lib.airspy_set_mixer_gain(self._handle,
                                              ctypes.c_uint8(value))
        else:
            ret = _lib.airspy_set_vga_gain(self._handle,
                                            ctypes.c_uint8(value))
        if ret != 0:
            raise DeviceConfigError(
                f"airspy_set_{gain_type}_gain() failed: {ret}")
