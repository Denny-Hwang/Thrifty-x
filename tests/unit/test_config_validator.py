# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for config_validator module."""

import pytest

from thriftyx.config_validator import validate_config
from thriftyx.exceptions import ConfigValidationError


def _valid_mini_config():
    return {
        'device_type': 'airspy_mini',
        'sample_rate': 6_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 16384,
        'block_history': 4920,
        'bit_depth': 12,
        'lna_gain': 5,
        'mixer_gain': 5,
        'vga_gain': 5,
        'bias_tee': False,
    }


def test_valid_airspy_mini_config():
    warnings = validate_config(_valid_mini_config())
    assert isinstance(warnings, list)


def test_valid_airspy_r2_config():
    config = _valid_mini_config()
    config['device_type'] = 'airspy_r2'
    config['sample_rate'] = 10_000_000
    warnings = validate_config(config)
    assert isinstance(warnings, list)


def test_invalid_device_type():
    config = _valid_mini_config()
    config['device_type'] = 'rtlsdr'
    with pytest.raises(ConfigValidationError, match="device_type"):
        validate_config(config)


def test_invalid_sample_rate_mini():
    config = _valid_mini_config()
    config['sample_rate'] = 2_500_000  # R2 rate, not Mini
    with pytest.raises(ConfigValidationError, match="sample_rate"):
        validate_config(config)


def test_invalid_sample_rate_r2():
    config = _valid_mini_config()
    config['device_type'] = 'airspy_r2'
    config['sample_rate'] = 6_000_000  # Mini rate, not R2
    with pytest.raises(ConfigValidationError, match="sample_rate"):
        validate_config(config)


def test_invalid_frequency():
    config = _valid_mini_config()
    config['tuner_freq'] = 5_000_000  # Below 24 MHz
    with pytest.raises(ConfigValidationError, match="tuner_freq"):
        validate_config(config)


def test_frequency_too_high():
    config = _valid_mini_config()
    config['tuner_freq'] = 2_000_000_000  # Above 1.8 GHz
    with pytest.raises(ConfigValidationError, match="tuner_freq"):
        validate_config(config)


def test_block_size_not_power_of_2():
    config = _valid_mini_config()
    config['block_size'] = 16385
    with pytest.raises(ConfigValidationError, match="block_size"):
        validate_config(config)


def test_lna_gain_out_of_range():
    config = _valid_mini_config()
    config['lna_gain'] = 15  # Max is 14
    with pytest.raises(ConfigValidationError, match="lna_gain"):
        validate_config(config)


def test_mixer_gain_out_of_range():
    config = _valid_mini_config()
    config['mixer_gain'] = 16  # Max is 15
    with pytest.raises(ConfigValidationError, match="mixer_gain"):
        validate_config(config)


def test_vga_gain_out_of_range():
    config = _valid_mini_config()
    config['vga_gain'] = -1  # Below 0
    with pytest.raises(ConfigValidationError, match="vga_gain"):
        validate_config(config)


def test_invalid_bit_depth():
    config = _valid_mini_config()
    config['bit_depth'] = 16
    with pytest.raises(ConfigValidationError, match="bit_depth"):
        validate_config(config)


def test_legacy_rtlsdr_rate_rejected():
    """Legacy RTL-SDR sample rate should be rejected with an error."""
    config = _valid_mini_config()
    config['sample_rate'] = 2_400_000  # RTL-SDR default
    with pytest.raises(ConfigValidationError, match="RTL-SDR"):
        validate_config(config)


def test_minimal_config():
    """Config with only device_type should pass."""
    warnings = validate_config({'device_type': 'airspy_mini'})
    assert isinstance(warnings, list)


def test_r2_lna_gain_15_accepted():
    """Airspy R2 supports LNA gain 0-15 (wider than Mini's 0-14)."""
    config = _valid_mini_config()
    config['device_type'] = 'airspy_r2'
    config['sample_rate'] = 10_000_000
    config['lna_gain'] = 15
    warnings = validate_config(config)
    assert isinstance(warnings, list)


def test_mini_lna_gain_15_rejected():
    """Airspy Mini only supports LNA gain 0-14; 15 should be rejected."""
    config = _valid_mini_config()
    config['lna_gain'] = 15
    with pytest.raises(ConfigValidationError, match="lna_gain"):
        validate_config(config)


def test_r2_lna_gain_16_rejected():
    """Airspy R2 LNA gain 16 exceeds max (15) and should be rejected."""
    config = _valid_mini_config()
    config['device_type'] = 'airspy_r2'
    config['sample_rate'] = 10_000_000
    config['lna_gain'] = 16
    with pytest.raises(ConfigValidationError, match="lna_gain"):
        validate_config(config)
