# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Configuration validation for SDR hardware.

Supports RTL-SDR (legacy 8-bit), Airspy Mini, and Airspy R2 devices.
"""

from thriftyx.exceptions import ConfigValidationError

AIRSPY_MINI_RATES = frozenset({3_000_000, 6_000_000})
AIRSPY_R2_RATES = frozenset({2_500_000, 10_000_000})
# RTL-SDR supports a wide range; these are the most common rates
RTLSDR_RATES = frozenset({
    225_001, 300_000, 900_001, 1_200_000, 1_400_000, 1_600_000,
    1_800_000, 1_920_000, 2_000_000, 2_048_000, 2_400_000,
    2_560_000, 2_800_000, 3_200_000,
})
DEVICE_FREQ_RANGE = (24_000_000, 1_800_000_000)
GAIN_LIMITS_MINI = {'lna': (0, 14), 'mixer': (0, 15), 'vga': (0, 15)}
GAIN_LIMITS_R2 = {'lna': (0, 15), 'mixer': (0, 15), 'vga': (0, 15)}

ALL_VALID_DEVICES = ('rtlsdr', 'airspy_mini', 'airspy_r2')


def validate_config(config: dict) -> list[str]:
    """Validate SDR hardware configuration.

    Supports RTL-SDR, Airspy Mini, and Airspy R2 devices.

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
    device_type = config.get('device_type', 'rtlsdr')
    if device_type not in ALL_VALID_DEVICES:
        raise ConfigValidationError(
            f"device_type '{device_type}' is not valid. "
            f"Must be one of: {ALL_VALID_DEVICES}")

    # 2. sample_rate must be in supported set for the device
    sample_rate = config.get('sample_rate')
    if sample_rate is not None:
        sample_rate = int(sample_rate)
        if device_type == 'airspy_mini':
            valid_rates = AIRSPY_MINI_RATES
        elif device_type == 'airspy_r2':
            valid_rates = AIRSPY_R2_RATES
        else:
            # RTL-SDR: accept any rate in the known set, or warn if unusual
            valid_rates = RTLSDR_RATES
        if sample_rate not in valid_rates:
            if device_type == 'rtlsdr':
                # RTL-SDR supports a wide range; just warn for unusual rates
                warnings.append(
                    f"sample_rate {sample_rate} is not a common RTL-SDR rate. "
                    f"Common rates: {sorted(valid_rates)}")
            else:
                raise ConfigValidationError(
                    f"sample_rate {sample_rate} not supported by {device_type}. "
                    f"Valid rates: {sorted(valid_rates)}")

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
        if history < 1:
            raise ConfigValidationError(
                f"block_history ({history}) must be >= 1 to avoid "
                f"division by zero")
        if history >= block_size:
            raise ConfigValidationError(
                f"block_history ({history}) must be less than "
                f"block_size ({block_size})")
        # The capture loop slices ``raw[-(block_history * 2):]`` from each
        # ``new_samples = block_size - block_history`` read, so the read
        # buffer must be at least as large as the history portion.
        # Equivalently: ``block_size >= 2 * block_history``.  Without this
        # invariant, history slicing silently returns short buffers and
        # carrier detection drifts.
        if block_size < 2 * history:
            raise ConfigValidationError(
                f"block_size ({block_size}) must be >= 2 * block_history "
                f"({2 * history}) so that each read fills the next "
                f"history window. Increase block_size or decrease "
                f"block_history.")

    # 5b. block_history must accommodate the template for the given sample rate
    chip_rate = config.get('chip_rate')
    if sample_rate is not None and chip_rate is not None and history is not None:
        sps = sample_rate / chip_rate
        est_template_len = int(sps * 1023)  # 10-bit Gold code
        if history < est_template_len - 1:
            warnings.append(
                f"block_history ({history}) is smaller than estimated "
                f"template length ({est_template_len}) for sample_rate="
                f"{sample_rate/1e6:.1f}M. Detection will fail. "
                f"Recommended block_history >= {est_template_len * 2}.")
        if block_size is not None and block_size <= est_template_len:
            warnings.append(
                f"block_size ({block_size}) is not larger than estimated "
                f"template length ({est_template_len}) for sample_rate="
                f"{sample_rate/1e6:.1f}M. Detection will fail.")

    # 6. carrier_window must fit within block_size/2 (Nyquist)
    carrier_window = config.get('carrier_window')
    if carrier_window is not None and block_size is not None:
        # carrier_window is (start, stop) or (start, stop, unit_hz) tuple
        if isinstance(carrier_window, (tuple, list)) and len(carrier_window) >= 2:
            stop_val = carrier_window[1]
            unit_hz = (len(carrier_window) >= 3 and carrier_window[2])
            if unit_hz:
                # Values are in Hz; convert to bins before Nyquist check
                if sample_rate is not None:
                    stop_bin = int(stop_val * block_size / sample_rate)
                else:
                    stop_bin = None  # cannot check without sample_rate
            else:
                stop_bin = stop_val
            if stop_bin is not None and stop_bin > block_size // 2:
                warnings.append(
                    f"carrier_window stop bin {stop_bin} exceeds Nyquist "
                    f"({block_size // 2}). Check carrier_window setting.")

    # 7. Gain values within device-specific ranges (Airspy only)
    if device_type in ('airspy_mini', 'airspy_r2'):
        gain_limits = GAIN_LIMITS_R2 if device_type == 'airspy_r2' else GAIN_LIMITS_MINI
        for gain_type, (min_v, max_v) in gain_limits.items():
            val = config.get(f'{gain_type}_gain')
            if val is not None:
                val = int(val)
                if not (min_v <= val <= max_v):
                    raise ConfigValidationError(
                        f"{gain_type}_gain {val} out of range [{min_v}, {max_v}]")

        # gain_mode + combined_gain consistency
        gain_mode = config.get('gain_mode', 'manual')
        if gain_mode not in ('manual', 'linearity', 'sensitivity'):
            raise ConfigValidationError(
                f"gain_mode '{gain_mode}' invalid. "
                f"Use 'manual', 'linearity', or 'sensitivity'.")
        if gain_mode in ('linearity', 'sensitivity'):
            combined = config.get('combined_gain')
            if combined is None:
                raise ConfigValidationError(
                    f"gain_mode='{gain_mode}' requires combined_gain (0-21)")
            combined = int(combined)
            if not (0 <= combined <= 21):
                raise ConfigValidationError(
                    f"combined_gain {combined} out of range [0, 21]")
            # AGC flags are ignored in non-manual modes; warn if set.
            if config.get('lna_agc') or config.get('mixer_agc'):
                warnings.append(
                    f"gain_mode='{gain_mode}' ignores lna_agc / mixer_agc; "
                    "use gain_mode='manual' to combine AGC with manual gains.")

    # 8. bit_depth must match device type
    bit_depth = config.get('bit_depth')
    if bit_depth is not None:
        bit_depth = int(bit_depth)
        if bit_depth not in (8, 12):
            raise ConfigValidationError(
                f"bit_depth {bit_depth} not supported. Use 8 or 12.")
        if device_type == 'rtlsdr' and bit_depth != 8:
            warnings.append(
                f"RTL-SDR uses 8-bit samples, but bit_depth={bit_depth}. "
                "Setting will be ignored for RTL-SDR hardware.")
        if device_type in ('airspy_mini', 'airspy_r2') and bit_depth != 12:
            warnings.append(
                f"Airspy uses 12-bit samples, but bit_depth={bit_depth}.")

    return warnings
