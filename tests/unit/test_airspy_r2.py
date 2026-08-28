# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Airspy R2-specific HAL regression tests.

These cover the R2 overrides of AirspyMiniDevice, which previously
carried copy-paste defects that no test exercised:

- ``parse_airspy_serial`` treating ``0x``-prefixed digits-only serials
  as decimal;
- the R2 LNA gain range declared 0-15 while the R820T2 range is 0-14;
- ``AirspyR2Device.set_sample_rate`` not recording ``_sample_rate``,
  which made ``_start_rx`` size the bounded read_sync buffer from the
  maximum supported rate (10 MSPS) instead of the configured one.

No libairspy or hardware required: the ctypes binding
``thriftyx.hal.airspy_mini._lib`` is replaced with a recording fake.
"""

import pytest

from thriftyx.config_validator import (GAIN_LIMITS_R2, validate_config)
from thriftyx.exceptions import ConfigValidationError, DeviceConfigError
from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import parse_airspy_serial
from thriftyx.hal.airspy_r2 import AirspyR2Device


class _FakeLib:
    """Minimal fake _lib that records tuning calls."""

    def __init__(self):
        self.calls = []

    def airspy_open(self, h):
        return 0

    def airspy_set_sample_type(self, h, t):
        return 0

    def airspy_board_partid_serialno_read(self, h, info):
        return -1

    def airspy_close(self, h):
        return 0

    def _record(self, name, value):
        v = value.value if hasattr(value, 'value') else value
        self.calls.append((name, int(v)))
        return 0

    def airspy_set_samplerate(self, h, v):
        return self._record('rate', v)

    def airspy_set_freq(self, h, v):
        return self._record('freq', v)

    def airspy_set_lna_gain(self, h, v):
        return self._record('lna', v)

    def airspy_set_mixer_gain(self, h, v):
        return self._record('mixer', v)

    def airspy_set_vga_gain(self, h, v):
        return self._record('vga', v)


@pytest.fixture
def r2_dev(monkeypatch):
    lib = _FakeLib()
    monkeypatch.setattr(am, '_lib', lib, raising=False)
    dev = AirspyR2Device()
    dev.open()
    yield dev, lib
    dev._open = False


# --------------------- serial parsing ---------------------------------

def test_parse_serial_0x_prefix_with_decimal_digits_is_hex():
    # Regression: the 0x prefix was stripped before the hex/decimal
    # decision, so digits-only hex parsed as decimal.
    assert parse_airspy_serial('0x12345678') == 0x12345678
    assert parse_airspy_serial('0X12345678') == 0x12345678


def test_parse_serial_0x_prefix_with_hex_letters():
    assert parse_airspy_serial('0xABCD') == 0xABCD


def test_parse_serial_plain_decimal_unchanged():
    assert parse_airspy_serial('12345678') == 12345678


def test_parse_serial_16_digit_board_id_is_hex():
    # 16-char board IDs (as printed by airspy_info) are hex even
    # without a prefix.
    assert parse_airspy_serial('1234567890123456') == 0x1234567890123456


# --------------------- R2 LNA gain range ------------------------------

def test_r2_lna_gain_range_matches_r820t2():
    assert GAIN_LIMITS_R2['lna'] == (0, 14)
    assert AirspyR2Device._GAIN_STAGES['lna'] == (0, 14)


def test_r2_device_accepts_lna_14_rejects_15(r2_dev):
    dev, lib = r2_dev
    dev.set_gain('lna', 14)
    assert ('lna', 14) in lib.calls
    with pytest.raises(DeviceConfigError):
        dev.set_gain('lna', 15)


def test_r2_device_mixer_vga_range_unchanged(r2_dev):
    dev, lib = r2_dev
    dev.set_gain('mixer', 15)
    dev.set_gain('vga', 15)
    assert ('mixer', 15) in lib.calls
    assert ('vga', 15) in lib.calls


def _r2_config(**overrides):
    cfg = {
        'device_type': 'airspy_r2',
        'sample_rate': 10_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 65536,
        'block_history': 20464,
        'bit_depth': 12,
    }
    cfg.update(overrides)
    return cfg


def test_validator_rejects_r2_lna_15():
    with pytest.raises(ConfigValidationError, match='lna_gain'):
        validate_config(_r2_config(lna_gain=15))


def test_validator_accepts_r2_lna_14():
    validate_config(_r2_config(lna_gain=14))  # must not raise


# --------------------- sample-rate bookkeeping ------------------------

def test_r2_set_sample_rate_records_rate(r2_dev):
    # Regression: the override omitted the _sample_rate assignment, so
    # the bounded read_sync buffer was sized for 10 MSPS at any rate.
    dev, lib = r2_dev
    dev.set_sample_rate(2_500_000)
    assert ('rate', 2_500_000) in lib.calls
    assert dev._sample_rate == 2_500_000


def test_r2_set_sample_rate_rejects_mini_rates(r2_dev):
    dev, _ = r2_dev
    with pytest.raises(DeviceConfigError):
        dev.set_sample_rate(3_000_000)
    dev.set_sample_rate(10_000_000)
    assert dev._sample_rate == 10_000_000


def test_parse_serial_rejects_out_of_range_values():
    """A typo'd negative or >64-bit serial must raise, not silently
    wrap/mask to a different device's serial."""
    with pytest.raises(ValueError):
        parse_airspy_serial('-5')
    with pytest.raises(ValueError):
        parse_airspy_serial('0x1FFFFFFFFFFFFFFFF')  # 65 bits
    # Boundary value is still accepted.
    assert parse_airspy_serial('0xFFFFFFFFFFFFFFFF') == 0xFFFFFFFFFFFFFFFF
