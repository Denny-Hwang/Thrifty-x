# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Configuration validation for Airspy hardware."""

from thriftyx.exceptions import ConfigValidationError

AIRSPY_MINI_RATES = frozenset({3_000_000, 6_000_000})
AIRSPY_R2_RATES = frozenset({2_500_000, 10_000_000})
DEVICE_FREQ_RANGE = (24_000_000, 1_800_000_000)
GAIN_LIMITS = {'lna': (0, 14), 'mixer': (0, 15), 'vga': (0, 15)}

# Legacy RTL-SDR default sample rate
_RTLSDR_LEGACY_RATE = 2_400_000


def validate_config(config: dict) -> list[str]:
    """Validate Airspy hardware configuration.

    Parameters
    ----------
    config : dict
        Configuration dictionary (as loaded by settings.load()).

    Returns
    -------
    list of str
        Warnings (non-fatal issues). Empty list if no warnings.

    Raises
    ------
    ConfigValidationError
        If configuration is invalid.
    """
    warnings = []

    # 1. device_type
    device_type = config.get('device_type', 'airspy_mini')
    valid_devices = ('airspy_mini', 'airspy_r2')
    if device_type not in valid_devices:
        raise ConfigValidationError(
            f"device_type '{device_type}' is not valid. "
            f"Must be one of: {valid_devices}")

    # 2. sample_rate must be in supported set
    sample_rate = config.get('sample_rate')
    if sample_rate is not None:
        sample_rate = int(sample_rate)
        if device_type == 'airspy_mini':
            valid_rates = AIRSPY_MINI_RATES
        else:
            valid_rates = AIRSPY_R2_RATES
        if sample_rate not in valid_rates and sample_rate != _RTLSDR_LEGACY_RATE:
            raise ConfigValidationError(
                f"sample_rate {sample_rate} not supported by {device_type}. "
                f"Valid rates: {sorted(valid_rates)}")
        if sample_rate == _RTLSDR_LEGACY_RATE:
            warnings.append(
                f"sample_rate {sample_rate} looks like a legacy RTL-SDR "
                "default. Consider using 3000000 or 6000000 for Airspy Mini.")

    # 3. center_freq within 24 MHz – 1.8 GHz
    freq = config.get('tuner_freq')
    if freq is not None:
        freq = int(freq)
        min_f, max_f = DEVICE_FREQ_RANGE
        if not (min_f <= freq <= max_f):
            raise ConfigValidationError(
                f"tuner_freq {freq} Hz out of range [{min_f}, {max_f}] Hz")

    # 4. block_size must be power of 2
    block_size = config.get('block_size')
    if block_size is not None:
        block_size = int(block_size)
        if block_size <= 0 or (block_size & (block_size - 1)) != 0:
            raise ConfigValidationError(
                f"block_size {block_size} must be a positive power of 2")

    # 5. block_history >= template_length - 1 (if both present)
    history = config.get('block_history')
    if history is not None and block_size is not None:
        history = int(history)
        if history >= block_size:
            raise ConfigValidationError(
                f"block_history ({history}) must be less than "
                f"block_size ({block_size})")

    # 6. carrier_window must fit within block_size/2 (Nyquist)
    carrier_window = config.get('carrier_window')
    if carrier_window is not None and block_size is not None:
        # carrier_window is (start, stop, unit_hz) tuple
        if isinstance(carrier_window, (tuple, list)) and len(carrier_window) >= 2:
            start_bin = carrier_window[0]
            stop_bin = carrier_window[1]
            if stop_bin > block_size // 2:
                warnings.append(
                    f"carrier_window stop bin {stop_bin} exceeds Nyquist "
                    f"({block_size // 2}). Check carrier_window setting.")

    # 7. Gain values within device-specific ranges
    for gain_type, (min_v, max_v) in GAIN_LIMITS.items():
        val = config.get(f'{gain_type}_gain')
        if val is not None:
            val = int(val)
            if not (min_v <= val <= max_v):
                raise ConfigValidationError(
                    f"{gain_type}_gain {val} out of range [{min_v}, {max_v}]")

    # 8. bit_depth must be 8 or 12
    bit_depth = config.get('bit_depth')
    if bit_depth is not None:
        bit_depth = int(bit_depth)
        if bit_depth not in (8, 12):
            raise ConfigValidationError(
                f"bit_depth {bit_depth} not supported. Use 8 or 12.")

    return warnings
