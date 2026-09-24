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

from thriftyx.carrier_detect import fft_range_index
from thriftyx.exceptions import ConfigValidationError
from thriftyx.hal.profiles import DEFAULT_DEVICE_TYPE, get_profile
from thriftyx.setting_parsers import normalize_freq_range
from thriftyx.settings import compute_block_params, longest_code_bits


# Largest block_size capture accepts (2**24 samples, 1.7 s at 10 Msps).
MAX_BLOCK_SIZE = 2 ** 24


def validate_config(config: dict) -> list[str]:
    """Validate SDR hardware configuration.

    Device facts (rates, tuning and gain ranges) come from
    :mod:`thriftyx.hal.profiles`, the same source the HAL drivers use.

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
    device_type = config.get('device_type', DEFAULT_DEVICE_TYPE)
    profile = get_profile(device_type)

    # 2. sample_rate must be in supported set for the device
    sample_rate = config.get('sample_rate')
    if sample_rate is not None:
        sample_rate = int(sample_rate)
        if not profile.supports_sample_rate(sample_rate):
            rates = sorted(profile.sample_rates)
            if profile.strict_sample_rates:
                raise ConfigValidationError(
                    f"sample_rate {sample_rate} not supported by "
                    f"{device_type}. Valid rates: {rates}")
            warnings.append(
                f"sample_rate {sample_rate} is not a common {profile.name} "
                f"rate. Common rates: {rates}")

    # 3. center_freq within the tuner's range
    freq = config.get('tuner_freq')
    if freq is not None:
        freq = int(freq)
        min_f, max_f = profile.frequency_range
        if not (min_f <= freq <= max_f):
            raise ConfigValidationError(
                f"tuner_freq {freq} Hz out of range [{min_f}, {max_f}] Hz "
                f"for {device_type}")

    # 4. block_size must be power of 2
    block_size = config.get('block_size')
    if block_size is not None:
        block_size = int(block_size)
        if block_size <= 0 or (block_size & (block_size - 1)) != 0:
            raise ConfigValidationError(
                f"block_size {block_size} must be a positive power of 2")
        # Capture allocates and FFTs whole blocks; anything larger is a
        # typo, not a geometry (an 11-bit code at 10 Msps needs 65536).
        if block_size > MAX_BLOCK_SIZE:
            raise ConfigValidationError(
                f"block_size {block_size} is larger than {MAX_BLOCK_SIZE}")

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

    # 5b. block_history should hold the template of the longest code
    # (11 bits): a card captured with less can never be correlated with
    # it, whatever the detector is configured with later.
    chip_rate = config.get('chip_rate')
    if sample_rate is not None and chip_rate is not None and history is not None:
        _, rec_history, _ = compute_block_params(sample_rate, chip_rate)
        bits = longest_code_bits(history, sample_rate, chip_rate)
        if bits is None or bits < 11:
            fits = (f"codes up to {bits} bits" if bits
                    else "no supported code")
            warnings.append(
                f"block_history ({history}) at sample_rate="
                f"{sample_rate/1e6:.1f}M holds {fits}: cards captured with "
                f"it can never be correlated with a longer code's template "
                f"(upstream Thrifty transmitters send 11-bit codes). "
                f"Recommended block_history >= {rec_history}, the default "
                f"when block_history is not set.")
        template_10 = int(sample_rate / chip_rate * 1023)
        if block_size is not None and block_size <= template_10:
            warnings.append(
                f"block_size ({block_size}) is not larger than a 10-bit "
                f"code's template ({template_10} samples) at sample_rate="
                f"{sample_rate/1e6:.1f}M. Detection will fail.")

    # 6. carrier_window must lie within the FFT, and should within
    # block_size/2 (Nyquist).  A bin the carrier detector cannot index
    # would otherwise stop capture on its first block, after the output
    # file was created.
    carrier_window = config.get('carrier_window')
    if carrier_window is not None and block_size is not None:
        # carrier_window is (start, stop) or (start, stop, unit_hz) tuple
        if isinstance(carrier_window, (tuple, list)) and len(carrier_window) >= 2:
            unit_hz = bool(len(carrier_window) >= 3 and carrier_window[2])
            window = (carrier_window[0], carrier_window[1], unit_hz)
            # The conversion capture uses.  A window is reported in its
            # own unit (bin block_size/2 is sample_rate/2); only one in
            # bins can lack the 'Hz'.
            bins = None  # cannot convert Hz without sample_rate
            if not unit_hz:
                bins = normalize_freq_range(window, 1.0)
                what = f"carrier_window {bins[0]} to {bins[1]} (FFT bins)"
                nyquist = f"Nyquist ({block_size // 2})"
                hint = (" A window without 'Hz' is in bins even with a "
                        "k/M suffix: write e.g. 50-60kHz for one in Hz.")
            elif sample_rate is not None:
                bins = normalize_freq_range(window, sample_rate / block_size)
                what = (f"carrier_window {window[0]:.0f} to "
                        f"{window[1]:.0f} Hz")
                nyquist = (f"Nyquist (±{sample_rate / 2:.0f} Hz, "
                           f"sample_rate/2)")
                hint = " Check carrier_window setting."
            if bins is not None:
                try:
                    fft_range_index(bins[0], bins[1], block_size)
                except ValueError:
                    raise ConfigValidationError(
                        f"{what} lies outside the {block_size}-bin FFT: "
                        f"it exceeds {nyquist}.{hint}") from None
                if max(abs(bins[0]), abs(bins[1])) > block_size // 2:
                    warnings.append(f"{what} exceeds {nyquist}.{hint}")

    # 7. Gain indices and gain_mode, for devices with staged gain
    if profile.gain_stages:
        for gain_type, (min_v, max_v) in profile.gain_stages.items():
            val = config.get(f'{gain_type}_gain')
            if val is not None:
                val = int(val)
                if not (min_v <= val <= max_v):
                    raise ConfigValidationError(
                        f"{gain_type}_gain {val} out of range [{min_v}, {max_v}]")

        gain_mode = config.get('gain_mode', 'manual')
        if gain_mode not in profile.gain_modes:
            raise ConfigValidationError(
                f"gain_mode '{gain_mode}' invalid for {device_type}. "
                f"Use one of: {', '.join(profile.gain_modes)}.")
        if gain_mode != 'manual':
            lo, hi = profile.combined_gain_range or (0, 0)
            combined = config.get('combined_gain')
            if combined is None:
                raise ConfigValidationError(
                    f"gain_mode='{gain_mode}' requires combined_gain "
                    f"({lo}-{hi})")
            combined = int(combined)
            if not (lo <= combined <= hi):
                raise ConfigValidationError(
                    f"combined_gain {combined} out of range [{lo}, {hi}]")
            # AGC flags are ignored in non-manual modes; warn if set.
            if config.get('lna_agc') or config.get('mixer_agc'):
                warnings.append(
                    f"gain_mode='{gain_mode}' ignores lna_agc / mixer_agc; "
                    "use gain_mode='manual' to combine AGC with manual gains.")
            # Per-stage indices are resolved by libairspy's combined-gain
            # ladder in preset modes; warn if the user left non-default
            # per-stage values that will be silently ignored.
            stray_stages = [s for s in profile.gain_stages
                            if int(config.get(f'{s}_gain', 0) or 0) != 0]
            if stray_stages:
                warnings.append(
                    f"gain_mode='{gain_mode}' ignores per-stage gains "
                    f"{stray_stages}; libairspy resolves LNA/Mixer/VGA from "
                    "combined_gain. Use gain_mode='manual' to set stages "
                    "directly.")

    # 8. bit_depth must match the device's samples
    bit_depth = config.get('bit_depth')
    if bit_depth is not None:
        bit_depth = int(bit_depth)
        if bit_depth not in (8, 12):
            raise ConfigValidationError(
                f"bit_depth {bit_depth} not supported. Use 8 or 12.")
        if bit_depth != profile.bit_depth:
            warnings.append(
                f"{profile.name} delivers {profile.bit_depth}-bit samples; "
                f"bit_depth={bit_depth} is ignored for capture.")

    return warnings
