# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""R820T2 register access: HAL binding, setting, validation, capture."""

import ctypes
import io

import numpy as np
import pytest

from thriftyx import setting_parsers
from thriftyx.airspy_capture import _capture_airspy
from thriftyx.config_validator import validate_config
from thriftyx.exceptions import ConfigValidationError, DeviceConfigError
from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import AirspyMiniDevice
from thriftyx.settings import Namespace
from tests.mocks.scripted_device import ScriptedSDRDevice


class _RegisterLib:
    """libairspy stand-in holding a tuner register file."""

    def __init__(self, fail_reg=None):
        self.regs = {r: (0x96 if r == 0 else r) for r in range(0x20)}
        self.fail_reg = fail_reg
        self.writes = []

    def airspy_r820t_read(self, handle, reg, value_ptr):
        reg = reg.value
        if reg == self.fail_reg:
            return -1000
        ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_uint8))[0] = \
            self.regs[reg]
        return 0

    def airspy_r820t_write(self, handle, reg, value):
        self.writes.append((reg.value, value.value))
        self.regs[reg.value] = value.value
        return 0


@pytest.fixture
def reg_dev(monkeypatch):
    lib = _RegisterLib()
    monkeypatch.setattr(am, '_lib', lib, raising=False)
    dev = AirspyMiniDevice()
    dev._open = True
    dev._handle = object()
    return dev, lib


def test_hal_reads_the_register_file(reg_dev):
    dev, _ = reg_dev
    regs = dev.read_tuner_registers()
    assert sorted(regs) == list(range(0x20))
    assert regs[0x00] == 0x96
    assert dev.read_tuner_registers(0x0C, 0x0C) == {0x0C: 0x0C}


def test_hal_writes_a_register(reg_dev):
    dev, lib = reg_dev
    dev.write_tuner_register(0x0C, 0x68)
    assert lib.writes == [(0x0C, 0x68)]
    assert dev.read_tuner_registers(0x0C, 0x0C) == {0x0C: 0x68}


@pytest.mark.parametrize('reg, value', [(0x04, 0), (0x20, 0), (0x0C, 256)])
def test_hal_rejects_bad_writes(reg_dev, reg, value):
    dev, lib = reg_dev
    with pytest.raises(DeviceConfigError):
        dev.write_tuner_register(reg, value)
    assert lib.writes == []


def test_hal_read_failure_raises(monkeypatch):
    monkeypatch.setattr(am, '_lib', _RegisterLib(fail_reg=0x07),
                        raising=False)
    dev = AirspyMiniDevice()
    dev._open = True
    dev._handle = object()
    with pytest.raises(DeviceConfigError, match='0x07'):
        dev.read_tuner_registers()


def test_hal_without_register_api(monkeypatch):
    monkeypatch.setattr(am, '_lib', object(), raising=False)
    dev = AirspyMiniDevice()
    dev._open = True
    with pytest.raises(DeviceConfigError, match='airspy_r820t_read'):
        dev.read_tuner_registers()
    with pytest.raises(DeviceConfigError, match='airspy_r820t_write'):
        dev.write_tuner_register(0x0C, 0x68)


# -- setting ---------------------------------------------------------------

@pytest.mark.parametrize('text', ['0x0C', '0x0C=zz', '0x04=0x00',
                                  '0x20=0x00', '0x0C=0x100'])
def test_tuner_registers_setting_rejects(text):
    with pytest.raises(ValueError):
        setting_parsers.tuner_registers(text)


def test_tuner_registers_setting_accepts_separators():
    assert setting_parsers.tuner_registers('0C=68; 0x0a=0xB2,') == \
        ((0x0C, 0x68), (0x0A, 0xB2))


# -- validation -------------------------------------------------------------

def _airspy_config(**extra):
    config = {'device_type': 'airspy_r2', 'sample_rate': 10_000_000,
              'chip_rate': 999_707, 'block_size': 65536,
              'block_history': 20539, 'bit_depth': 12}
    config.update(extra)
    return config


def test_rtl_gain_on_airspy_warns_with_equivalent_indices():
    warns = validate_config(_airspy_config(tuner_gain=30.0))
    assert any('lna_gain 8, mixer_gain 8, vga_gain 8' in w for w in warns)
    assert not any('tuner_gain' in w
                   for w in validate_config(_airspy_config(tuner_gain=0.0)))


def test_stage_gains_on_rtlsdr_warn():
    config = {'device_type': 'rtlsdr', 'sample_rate': 2_400_000,
              'lna_gain': 7, 'vga_gain': 8}
    warns = validate_config(config)
    assert any('lna_gain, vga_gain are ignored' in w for w in warns)


def test_tuner_registers_need_a_skipped_block():
    with pytest.raises(ConfigValidationError, match='capture_skip'):
        validate_config(_airspy_config(tuner_registers=((0x0C, 0x68),),
                                       capture_skip=0))
    validate_config(_airspy_config(tuner_registers=((0x0C, 0x68),),
                                   capture_skip=1))


def test_tuner_registers_need_an_airspy():
    with pytest.raises(ConfigValidationError, match='needs an Airspy'):
        validate_config({'device_type': 'rtlsdr', 'sample_rate': 2_400_000,
                         'tuner_registers': ((0x0C, 0x68),)})


def test_preset_gain_mode_without_combined_gain_is_an_error():
    # combined_gain no longer defaults to 0 (the minimum-gain row).
    with pytest.raises(ConfigValidationError, match='requires combined_gain'):
        validate_config(_airspy_config(gain_mode='linearity'))


# -- capture ----------------------------------------------------------------

def _capture_config(**overrides):
    base = {
        'device_type': 'airspy_mini', 'sample_rate': 6_000_000,
        'tuner_freq': 433_920_000, 'block_size': 16, 'block_history': 4,
        'capture_skip': 2, 'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 0.0, 0.0), 'lna_gain': 0,
        'mixer_gain': 0, 'vga_gain': 8, 'bias_tee': False,
        'gain_mode': 'manual', 'lna_agc': False, 'mixer_agc': False,
        'ppm': 0.0, 'packing': False,
    }
    base.update(overrides)
    return Namespace(base)


def _run(monkeypatch, device, **overrides):
    monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                        lambda _type, **_kw: device)
    _capture_airspy(_capture_config(**overrides),
                    Namespace({'duration': None}), io.StringIO())


def _stream(pairs=200):
    return np.ones(pairs * 2, dtype=np.int16)


def test_capture_writes_after_the_stream_starts_and_logs_registers(
        monkeypatch, capsys):
    device = ScriptedSDRDevice(stream=_stream(),
                               registers={0x00: 0x96, 0x0C: 0x48})
    _run(monkeypatch, device, tuner_registers=((0x0C, 0x68),))
    # Written after the first read: the firmware has programmed the
    # tuner by then.
    assert device.register_writes == [(0x0C, 0x68, 1)]
    err = capsys.readouterr().err
    assert 'tuner register writes: 0x0C=0x68' in err
    assert 'tuner registers: 0x00=0x96 0x0C=0x68' in err


def test_capture_without_register_access_still_runs(monkeypatch, capsys):
    device = ScriptedSDRDevice(stream=_stream())
    _run(monkeypatch, device)
    assert 'tuner registers:' not in capsys.readouterr().err


def test_capture_failed_register_write_stops(monkeypatch):
    device = ScriptedSDRDevice(stream=_stream(), registers={},
                               fail_in='write_tuner_register')
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, device, tuner_registers=((0x0C, 0x68),))
    assert exc.value.code == 1


def test_capture_without_skipped_blocks_still_logs_registers(
        monkeypatch, capsys):
    device = ScriptedSDRDevice(stream=_stream(),
                               registers={0x00: 0x96, 0x0C: 0x48})
    _run(monkeypatch, device, capture_skip=0)
    err = capsys.readouterr().err
    assert err.count('tuner registers: 0x00=0x96 0x0C=0x48') == 1
    assert device.register_writes == []
