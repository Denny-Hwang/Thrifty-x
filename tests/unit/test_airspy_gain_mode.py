# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for Airspy gain-mode delegation (manual / linearity / sensitivity).

These verify that the HAL routes each gain mode to the correct libairspy
call (Option 1: delegate preset modes to airspy_set_linearity_gain /
airspy_set_sensitivity_gain rather than re-deriving the LNA/Mixer/VGA
ladder in Python), and that the config validator enforces the
combined-gain range and the preset/manual flag rules.

No hardware or real libairspy is needed: the ctypes binding
(``thriftyx.hal.airspy_mini._lib``) is replaced with a spy that records
which C function was called with which argument.
"""

import pytest

from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import AirspyMiniDevice, GAIN_MODES
from thriftyx.exceptions import DeviceConfigError
from thriftyx.config_validator import validate_config
from thriftyx.exceptions import ConfigValidationError


class _GainSpyLib:
    """libairspy stand-in that records every gain-related call."""

    def __init__(self):
        self.calls = []  # list of (fn_name, value)

    # Per-stage manual setters.
    def airspy_set_lna_gain(self, handle, value):
        self.calls.append(('lna', int(value.value if hasattr(value, 'value')
                                       else value)))
        return 0

    def airspy_set_mixer_gain(self, handle, value):
        self.calls.append(('mixer', int(value.value if hasattr(value, 'value')
                                         else value)))
        return 0

    def airspy_set_vga_gain(self, handle, value):
        self.calls.append(('vga', int(value.value if hasattr(value, 'value')
                                       else value)))
        return 0

    # AGC toggles (manual mode only).
    def airspy_set_lna_agc(self, handle, value):
        self.calls.append(('lna_agc', int(value.value if hasattr(value, 'value')
                                           else value)))
        return 0

    def airspy_set_mixer_agc(self, handle, value):
        self.calls.append(('mixer_agc', int(value.value
                                             if hasattr(value, 'value')
                                             else value)))
        return 0

    # Preset ladders (delegated to libairspy).
    def airspy_set_linearity_gain(self, handle, value):
        self.calls.append(('linearity', int(value.value
                                             if hasattr(value, 'value')
                                             else value)))
        return 0

    def airspy_set_sensitivity_gain(self, handle, value):
        self.calls.append(('sensitivity', int(value.value
                                               if hasattr(value, 'value')
                                               else value)))
        return 0

    def fn_names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def gain_dev(monkeypatch):
    """An 'open' AirspyMiniDevice whose _lib is the gain spy."""
    spy = _GainSpyLib()
    monkeypatch.setattr(am, '_lib', spy, raising=False)
    dev = AirspyMiniDevice()
    dev._open = True          # bypass open(); _check_open() returns the spy
    dev._handle = object()    # opaque handle; the spy ignores it
    return dev, spy


# ----------------------------------------------------------------------
# HAL routing: each mode hits the right libairspy call.
# ----------------------------------------------------------------------

def test_manual_mode_sets_three_stages(gain_dev):
    dev, spy = gain_dev
    dev.apply_gain_mode('manual', lna=3, mixer=5, vga=7)
    assert ('lna', 3) in spy.calls
    assert ('mixer', 5) in spy.calls
    assert ('vga', 7) in spy.calls
    # Manual mode must NOT touch the preset ladders.
    assert 'linearity' not in spy.fn_names()
    assert 'sensitivity' not in spy.fn_names()


def test_manual_mode_applies_agc_flags(gain_dev):
    dev, spy = gain_dev
    dev.apply_gain_mode('manual', lna=0, mixer=0, vga=0,
                        lna_agc=True, mixer_agc=False)
    assert ('lna_agc', 1) in spy.calls
    assert ('mixer_agc', 0) in spy.calls


def test_linearity_mode_delegates_once(gain_dev):
    dev, spy = gain_dev
    dev.apply_gain_mode('linearity', combined=14)
    assert spy.calls.count(('linearity', 14)) == 1
    # Must delegate, NOT drive the per-stage setters itself.
    assert 'lna' not in spy.fn_names()
    assert 'mixer' not in spy.fn_names()
    assert 'vga' not in spy.fn_names()


def test_sensitivity_mode_delegates_once(gain_dev):
    dev, spy = gain_dev
    dev.apply_gain_mode('sensitivity', combined=9)
    assert spy.calls.count(('sensitivity', 9)) == 1
    assert 'lna' not in spy.fn_names()


def test_preset_requires_combined(gain_dev):
    dev, _ = gain_dev
    with pytest.raises(DeviceConfigError, match="combined"):
        dev.apply_gain_mode('linearity')  # no combined value


@pytest.mark.parametrize("bad", [-1, 22, 100])
def test_combined_gain_out_of_range_rejected(gain_dev, bad):
    dev, _ = gain_dev
    with pytest.raises(DeviceConfigError, match="out of range"):
        dev.apply_gain_mode('sensitivity', combined=bad)


def test_unknown_mode_rejected(gain_dev):
    dev, _ = gain_dev
    with pytest.raises(DeviceConfigError, match="Unknown gain_mode"):
        dev.apply_gain_mode('auto', combined=0)


def test_gain_modes_constant():
    assert GAIN_MODES == ('manual', 'linearity', 'sensitivity')


# ----------------------------------------------------------------------
# Validator: combined-gain range and preset/manual flag rules.
# ----------------------------------------------------------------------

def _base_airspy_config():
    return {
        'device_type': 'airspy_r2',
        'sample_rate': 10_000_000,
        'chip_rate': 999_707,
        'block_size': 65536,
        'block_history': 20464,
        'bit_depth': 12,
    }


def test_validator_rejects_combined_gain_22():
    cfg = _base_airspy_config()
    cfg['gain_mode'] = 'linearity'
    cfg['combined_gain'] = 22  # GAIN_COUNT is 22 -> valid range 0..21
    with pytest.raises(ConfigValidationError, match="combined_gain"):
        validate_config(cfg)


def test_validator_accepts_combined_gain_21():
    cfg = _base_airspy_config()
    cfg['gain_mode'] = 'sensitivity'
    cfg['combined_gain'] = 21
    validate_config(cfg)  # must not raise


def test_validator_warns_on_preset_plus_per_stage_gain():
    cfg = _base_airspy_config()
    cfg['gain_mode'] = 'linearity'
    cfg['combined_gain'] = 10
    cfg['lna_gain'] = 8  # non-default per-stage value, ignored in preset mode
    warns = validate_config(cfg)
    assert any('ignores per-stage gains' in w for w in warns), warns


def test_validator_no_stray_warning_in_manual_mode():
    cfg = _base_airspy_config()
    cfg['gain_mode'] = 'manual'
    cfg['lna_gain'] = 8
    warns = validate_config(cfg)
    assert not any('ignores per-stage gains' in w for w in warns), warns
