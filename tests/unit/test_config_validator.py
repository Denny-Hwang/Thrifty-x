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
from thriftyx.setting_parsers import freq_range


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
    config['device_type'] = 'hackrf'
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


def test_rtlsdr_rate_accepted():
    """RTL-SDR sample rate should be accepted for rtlsdr device."""
    config = {
        'device_type': 'rtlsdr',
        'sample_rate': 2_400_000,
        'tuner_freq': 162_000_000,
        'block_size': 16384,
        'block_history': 4920,
        'bit_depth': 8,
    }
    warnings = validate_config(config)
    assert isinstance(warnings, list)


def test_rtlsdr_rate_rejected_on_airspy():
    """RTL-SDR sample rate should be rejected for Airspy Mini."""
    config = _valid_mini_config()
    config['sample_rate'] = 2_400_000  # Not valid for Airspy Mini
    with pytest.raises(ConfigValidationError, match="sample_rate"):
        validate_config(config)


def test_minimal_config():
    """Config with only device_type should pass."""
    warnings = validate_config({'device_type': 'airspy_mini'})
    assert isinstance(warnings, list)


def test_minimal_rtlsdr_config():
    """RTL-SDR config with only device_type should pass."""
    warnings = validate_config({'device_type': 'rtlsdr'})
    assert isinstance(warnings, list)


def test_rtlsdr_bit_depth_mismatch_warns():
    """RTL-SDR with bit_depth=12 should warn."""
    config = {
        'device_type': 'rtlsdr',
        'bit_depth': 12,
    }
    warnings = validate_config(config)
    assert any('RTL-SDR' in w for w in warnings)


def test_rtlsdr_gains_not_validated():
    """RTL-SDR should not validate Airspy-specific gain ranges."""
    config = {
        'device_type': 'rtlsdr',
        'lna_gain': 50,  # Would be invalid for Airspy
    }
    warnings = validate_config(config)
    assert isinstance(warnings, list)


def test_r2_lna_gain_15_rejected():
    """Airspy R2 uses the same R820T2 tuner as the Mini: LNA is 0-14."""
    config = _valid_mini_config()
    config['device_type'] = 'airspy_r2'
    config['sample_rate'] = 10_000_000
    config['lna_gain'] = 15
    with pytest.raises(ConfigValidationError, match='lna_gain'):
        validate_config(config)


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


def test_block_size_must_be_at_least_twice_block_history():
    """Capture loop relies on ``block_size >= 2 * block_history`` so each
    read fills the next history window.  Validator must reject otherwise."""
    config = _valid_mini_config()
    # 16384 < 2 * 9000 — invalid layout
    config['block_size'] = 16384
    config['block_history'] = 9000
    with pytest.raises(ConfigValidationError, match="block_size"):
        validate_config(config)


def test_block_size_exactly_twice_block_history_ok():
    """Boundary: block_size == 2 * block_history is acceptable."""
    config = _valid_mini_config()
    config['block_size'] = 16384
    config['block_history'] = 8192
    # Sample-rate-related warnings are fine; this must not raise.
    validate_config(config)


def test_gain_mode_invalid_value_rejected():
    config = _valid_mini_config()
    config['gain_mode'] = 'auto'
    with pytest.raises(ConfigValidationError, match="gain_mode"):
        validate_config(config)


def test_gain_mode_linearity_requires_combined():
    config = _valid_mini_config()
    config['gain_mode'] = 'linearity'
    # No combined_gain set
    with pytest.raises(ConfigValidationError, match="combined_gain"):
        validate_config(config)


def test_gain_mode_linearity_with_combined_ok():
    config = _valid_mini_config()
    config['gain_mode'] = 'linearity'
    config['combined_gain'] = 12
    validate_config(config)


def test_combined_gain_out_of_range():
    config = _valid_mini_config()
    config['gain_mode'] = 'sensitivity'
    config['combined_gain'] = 22
    with pytest.raises(ConfigValidationError, match="combined_gain"):
        validate_config(config)


def test_gain_mode_linearity_warns_when_agc_set():
    config = _valid_mini_config()
    config['gain_mode'] = 'linearity'
    config['combined_gain'] = 5
    config['lna_agc'] = True
    warnings = validate_config(config)
    assert any('ignores' in w for w in warnings)


@pytest.mark.parametrize('rate, history, bits', [
    (3e6, 4920, 10), (6e6, 12278, 10), (10e6, 20464, 10), (6e6, 4920, 9)])
def test_history_too_short_for_eleven_bits_warns(rate, history, bits):
    """The Pi capture example used to pin 3M / 4920: its captures could
    never be correlated with an 11-bit template."""
    config = {'device_type': 'airspy_mini' if rate in (3e6, 6e6)
              else 'airspy_r2', 'sample_rate': rate,
              'block_size': 65536, 'block_history': history,
              'chip_rate': 0.999707e6}
    warnings = validate_config(config)
    assert any(f'holds codes up to {bits} bits' in w for w in warnings)


@pytest.mark.parametrize('rate', [2.4e6, 2.5e6, 3e6, 6e6, 10e6])
def test_default_geometry_raises_no_history_warning(rate):
    from thriftyx.settings import compute_block_params
    size, history, _ = compute_block_params(rate, 0.999707e6)
    config = {'device_type': 'rtlsdr' if rate == 2.4e6 else
              'airspy_r2' if rate in (2.5e6, 10e6) else 'airspy_mini',
              'sample_rate': rate, 'block_size': size,
              'block_history': history, 'chip_rate': 0.999707e6}
    assert not [w for w in validate_config(config) if 'holds' in w]


@pytest.mark.parametrize('window', ['50-60k', '40000-100', '-40000-0',
                                    '0-7MHz'])
def test_carrier_window_outside_the_fft_is_rejected(window):
    """Capture used to accept these (at most with a warning) and crash
    on its first block, exit 1, after creating the output file."""
    config = _valid_mini_config()
    config['carrier_window'] = freq_range(window)
    with pytest.raises(ConfigValidationError, match='carrier_window'):
        validate_config(config)


@pytest.mark.parametrize('window, warns', [
    ('7-130', False), ('0--1', False), ('-100-100', False),
    ('50-60kHz', False),
    ('9000-10000', True), ('-10000--9000', True)])
def test_carrier_window_beyond_nyquist_warns(window, warns):
    config = _valid_mini_config()
    config['carrier_window'] = freq_range(window)
    warnings = validate_config(config)
    assert any('Nyquist' in w for w in warnings) == warns


def test_carrier_window_in_hz_needs_the_sample_rate():
    config = _valid_mini_config()
    del config['sample_rate']
    config['carrier_window'] = freq_range('0-7MHz')
    validate_config(config)


def test_huge_block_size_is_rejected():
    config = _valid_mini_config()
    config['block_size'] = 2 ** 45
    with pytest.raises(ConfigValidationError, match='block_size'):
        validate_config(config)
