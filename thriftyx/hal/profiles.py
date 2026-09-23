# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Hardware facts for each supported SDR model.

This is the single source of truth for sample rates, tuning ranges,
gain ranges and sample formats.  The HAL drivers, the config validator,
the settings defaults and ``tdoa`` all read it; nothing else may hold a
copy.  It is pure data (no ctypes), so importing it never loads
libairspy.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from thriftyx.exceptions import ConfigValidationError
from thriftyx.hal.base import SampleFormat

# libairspy occasionally reports a rate a few Hz off the nominal value
# (e.g. 5_999_998); accept rates within this tolerance.
SAMPLE_RATE_TOLERANCE_HZ = 100

# Airspy gain configurations (see AirspyMiniDevice.apply_gain_mode).
GAIN_MODES = ('manual', 'linearity', 'sensitivity')


@dataclass(frozen=True)
class DeviceProfile:
    """Hardware facts for one SDR model.

    Attributes
    ----------
    name : str
        Human-readable model name.
    sample_rates : tuple of int
        Supported I/Q sample rates (Hz).  For RTL-SDR these are the
        common rates; others only produce a warning.
    default_sample_rate : int
        Rate used when ``sample_rate`` is not configured, by capture and
        by every later stage (detect for headerless input, tdoa), so a
        default capture is processed at the rate it was recorded.
    frequency_range : (int, int)
        Tunable centre-frequency range (Hz).
    bit_depth : int
        ADC bit depth: 8 (uint8 samples) or 12 (int16 samples).
    sample_format : SampleFormat
        Container type of the raw I/Q samples.
    gain_stages : mapping of str to (int, int)
        Index range per gain stage; empty for devices with a single dB
        gain (RTL-SDR).
    combined_gain_range : (int, int) or None
        Index range of the preset gain ladders, when supported.
    gain_modes : tuple of str
        Supported ``gain_mode`` values.
    strict_sample_rates : bool
        Whether a rate outside ``sample_rates`` is an error (Airspy) or
        only unusual (RTL-SDR).
    """
    name: str
    sample_rates: tuple[int, ...]
    default_sample_rate: int
    frequency_range: tuple[int, int]
    bit_depth: int
    sample_format: SampleFormat
    gain_stages: Mapping[str, tuple[int, int]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False)
    combined_gain_range: 'tuple[int, int] | None' = None
    gain_modes: tuple[str, ...] = ('manual',)
    strict_sample_rates: bool = True

    def supports_sample_rate(self, rate: float,
                             rates: 'tuple[int, ...] | None' = None) -> bool:
        """Whether *rate* matches a supported rate (within tolerance).

        *rates* overrides :attr:`sample_rates`, e.g. with the list a
        connected device reports.
        """
        candidates = self.sample_rates if rates is None else rates
        return any(abs(int(rate) - int(r)) <= SAMPLE_RATE_TOLERANCE_HZ
                   for r in candidates)


# Airspy Mini and R2 share the R820T2 tuner and libairspy driver: the
# same tuning and gain ranges, different ADC clock and therefore rates.
_AIRSPY_GAIN_STAGES = MappingProxyType(
    {'lna': (0, 14), 'mixer': (0, 15), 'vga': (0, 15)})

# Airspy defaults are each board's highest rate, the canonical rate of
# the shipped example configs (example/detector_mini.cfg, _r2.cfg).
# Block parameters left at their defaults are enlarged for it by
# settings (32768/12349 at 6 MSPS, 65536/20539 at 10 MSPS).
AIRSPY_MINI = DeviceProfile(
    name="Airspy Mini",
    sample_rates=(3_000_000, 6_000_000),
    default_sample_rate=6_000_000,
    frequency_range=(24_000_000, 1_800_000_000),
    bit_depth=12,
    sample_format=SampleFormat.INT16,
    gain_stages=_AIRSPY_GAIN_STAGES,
    combined_gain_range=(0, 21),
    gain_modes=GAIN_MODES,
)

AIRSPY_R2 = DeviceProfile(
    name="Airspy R2",
    sample_rates=(2_500_000, 10_000_000),
    default_sample_rate=10_000_000,
    frequency_range=(24_000_000, 1_800_000_000),
    bit_depth=12,
    sample_format=SampleFormat.INT16,
    gain_stages=_AIRSPY_GAIN_STAGES,
    combined_gain_range=(0, 21),
    gain_modes=GAIN_MODES,
)

# RTL-SDR is captured through rtl_sdr / fastcard rather than a HAL
# driver, but its facts live here too so defaults and validation agree.
RTLSDR = DeviceProfile(
    name="RTL-SDR",
    sample_rates=(225_001, 300_000, 900_001, 1_200_000, 1_400_000,
                  1_600_000, 1_800_000, 1_920_000, 2_000_000, 2_048_000,
                  2_400_000, 2_560_000, 2_800_000, 3_200_000),
    default_sample_rate=2_400_000,
    # R820T/2 dongles tune about 24 MHz - 1.766 GHz.
    frequency_range=(24_000_000, 1_766_000_000),
    bit_depth=8,
    sample_format=SampleFormat.UINT8,
    strict_sample_rates=False,
)

PROFILES: Mapping[str, DeviceProfile] = MappingProxyType({
    'rtlsdr': RTLSDR,
    'airspy_mini': AIRSPY_MINI,
    'airspy_r2': AIRSPY_R2,
})

DEFAULT_DEVICE_TYPE = 'airspy_mini'


def get_profile(device_type: str) -> DeviceProfile:
    """Return the profile for *device_type*.

    Raises
    ------
    ConfigValidationError
        If the device type is unknown.
    """
    try:
        return PROFILES[device_type]
    except KeyError:
        raise ConfigValidationError(
            f"device_type '{device_type}' is not valid. "
            f"Must be one of: {', '.join(PROFILES)}") from None
