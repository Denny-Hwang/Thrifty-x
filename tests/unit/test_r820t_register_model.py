# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/r820t_register_model.py replays both drivers' tuner writes."""

import importlib.util
import io
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'r820t_register_model',
    Path(__file__).parents[2] / 'scripts' / 'r820t_register_model.py')
model = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(model)

FREQ = 161_300_000


def _lo_from_registers(regs, xtal_hz):
    """LO the PLL registers select (datasheet R16, R20-R22)."""
    ref = xtal_hz / (2 if regs[0x10] & 0x10 else 1)
    reg14 = regs[0x14]
    nint = 4 * (reg14 & 0x3f) + (reg14 >> 6) + 13
    sdm = 0 if regs[0x12] & 0x08 else regs[0x15] | (regs[0x16] << 8)
    vco = 2 * (nint + sdm / 65536) * ref
    return vco / (2 << (regs[0x10] >> 5))


def test_vga_register_matches_measured_devices():
    # Read back from real devices at matched gain: RTL 0x68, R2 0x48
    # (same VGA code 8; only the undocumented bit 5 differs).
    rtl, _ = model.rtl_registers(FREQ, 2.4e6, 0)
    air, _ = model.airspy_registers(FREQ, 10_000_000, 0, 0, 8)
    assert rtl[0x0C] == 0x68
    assert air[0x0C] == 0x48


def test_rtl_gain_zero_is_manual_not_agc():
    # fastcard always enables manual gain: LNA_GAIN_MODE=1 (manual),
    # MIXGAIN_MODE=0 (manual), indices 0.
    rtl, facts = model.rtl_registers(FREQ, 2.4e6, 0)
    assert rtl[0x05] & 0x1f == 0x10
    assert rtl[0x07] & 0x1f == 0x00
    assert (facts['lna'], facts['mixer']) == (0, 0)
    # rtl_sdr -g 0 is AGC with VGA code 11.
    auto, _ = model.rtl_registers(FREQ, 2.4e6, 0, auto_gain=True)
    assert auto[0x05] & 0x10 == 0x00
    assert auto[0x07] & 0x10 == 0x10
    assert auto[0x0C] == 0x6B


@pytest.mark.parametrize('rtl_gain, lna, mixer', [(0, 0, 0), (30, 8, 8),
                                                   (40.2, 11, 11)])
def test_matched_gains_give_identical_gain_fields(rtl_gain, lna, mixer):
    rtl, _ = model.rtl_registers(FREQ, 2.4e6, rtl_gain)
    air, _ = model.airspy_registers(FREQ, 10_000_000, lna, mixer, 8)
    for reg, mask in ((0x05, 0x1f), (0x07, 0x1f), (0x0C, 0x1f)):
        assert rtl[reg] & mask == air[reg] & mask


def test_airspy_manual_gain_turns_agc_off():
    air, _ = model.airspy_registers(FREQ, 10_000_000, 3, 4, 5)
    assert air[0x05] & 0x1f == 0x13          # manual, LNA 3
    assert air[0x07] & 0x1f == 0x04          # manual, mixer 4
    agc, _ = model.airspy_registers(FREQ, 10_000_000, 3, 4, 5,
                                    lna_agc=True, mixer_agc=True)
    assert agc[0x05] & 0x10 == 0x00
    assert agc[0x07] & 0x10 == 0x10


def test_if_and_filter_follow_the_rate():
    _, rtl = model.rtl_registers(FREQ, 2.4e6, 0)
    assert rtl['if_hz'] == 1_815_000          # r82xx_set_bandwidth(2.4M)
    air10, f10 = model.airspy_registers(FREQ, 10_000_000, 0, 0, 8)
    air25, f25 = model.airspy_registers(FREQ, 2_500_000, 0, 0, 8)
    assert f10['if_hz'] == 5_000_000
    assert f25['if_hz'] == 1_250_000
    # Fixed firmware bandwidths: bw 59 at 10 Msps, 0 (narrowest) at 2.5.
    assert (air10[0x0A], air10[0x0B]) == (0xB4, 0x0F)
    assert (air25[0x0A], air25[0x0B]) == (0xBF, 0xEF)


@pytest.mark.parametrize('freq', [100e6, FREQ, 433_830_000, 915e6])
@pytest.mark.parametrize('driver', model.RTL_DRIVERS)
def test_rtl_pll_registers_tune_the_lo(freq, driver):
    regs, facts = model.rtl_registers(freq, 2.4e6, 0, driver=driver)
    lo = _lo_from_registers(regs, facts['xtal_hz'])
    assert lo == pytest.approx(facts['lo_hz'], abs=2e3)


@pytest.mark.parametrize('freq', [100e6, FREQ, 433_830_000, 915e6])
@pytest.mark.parametrize('model_name, rate', [('r2', 10_000_000),
                                              ('r2', 2_500_000),
                                              ('mini', 6_000_000)])
def test_airspy_pll_registers_tune_the_lo(freq, model_name, rate):
    regs, facts = model.airspy_registers(freq, rate, 0, 0, 8,
                                         model=model_name)
    lo = _lo_from_registers(regs, facts['xtal_hz'])
    assert lo == pytest.approx(facts['lo_hz'], abs=2e3)


def test_tracking_filter_is_shared():
    rtl, _ = model.rtl_registers(FREQ, 2.4e6, 0)
    air, _ = model.airspy_registers(FREQ, 10_000_000, 0, 0, 8)
    assert rtl[0x1B] == air[0x1B] == 0x14     # 140-180 MHz band


def test_blog_driver_differs_from_osmocom():
    osmo, _ = model.rtl_registers(FREQ, 2.4e6, 0)
    blog, _ = model.rtl_registers(FREQ, 2.4e6, 0, driver='blog')
    assert osmo[0x05] & 0x80 == 0x00          # loop-through left on
    assert blog[0x05] & 0x80 == 0x80
    assert blog[0x06] & 0x30 == 0x30          # FILT_3DB set
    assert blog[0x0C] == osmo[0x0C] == 0x68


def test_datasheet_fixed_bits():
    assert model.datasheet_deviation(0x0C, 0xA0) == 0
    assert model.datasheet_deviation(0x0C, 0x48) == 0xA0   # R2: 7 and 5
    assert model.datasheet_deviation(0x0C, 0x68) == 0x80   # RTL: 7


def test_read_dump_accepts_a_capture_log_line():
    log = io.StringIO("Skipping 100 block(s)...\n"
                      "tuner registers: 0x00=0x96 0x0C=0x48  # R2\n"
                      "0x05=0x90\n")
    assert model.read_dump(log) == {0x00: 0x96, 0x0C: 0x48, 0x05: 0x90}


def test_cli_dump_check(tmp_path, capsys):
    air, _ = model.airspy_registers(FREQ, 10_000_000, 0, 0, 8)
    dump = tmp_path / 'capture.log'
    dump.write_text('tuner registers: ' + ' '.join(
        f'0x{r:02X}=0x{v:02X}' for r, v in air.items()) + '\n')
    args = ['-f', '161.3M', '--vga', '8', '--dump', str(dump)]
    assert model._main(args) == 0
    assert 'matches the prediction' in capsys.readouterr().out
    dump.write_text('tuner registers: 0x0C=0x68\n')
    assert model._main(args) == 1
    assert '0x0C: read 0x68, predicted 0x48' in capsys.readouterr().out
