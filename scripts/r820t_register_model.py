#!/usr/bin/env python
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""
Predict the R820T2 register file (0x05-0x1F) an RTL-SDR and an Airspy
leave behind for the same capture settings, and compare them.

Both receivers use the same Rafael R820T2 tuner, but different code
programs it: librtlsdr (osmocom rtl-sdr, as driven by upstream
Thrifty's fastcard) on the RTL-SDR, the airspyone firmware on the
Airspy.  This replays each driver's register writes -- init table,
filter calibration, bandwidth, tuning, gain -- on a shadow register
file, so the two end states can be put side by side, or checked
against a register dump of a real device (``--dump``: the ``tuner
registers: 0xNN=0xVV ...`` line an Airspy capture prints to stderr).

Sources replayed (repositories in _SOURCES):
  RTL-SDR   src/tuner_r82xx.c + src/librtlsdr.c (r82xx_init,
            r82xx_set_bandwidth, r82xx_set_freq, r82xx_set_gain),
            called in fastcard's order: sample rate, frequency,
            manual gain mode, gain.  --rtl-driver picks osmocom
            rtl-sdr or the RTL-SDR Blog fork, which differ.
  Airspy    common/r820t.c + common/airspy_{nos,mini}_conf.c
            (init table, r820t_set_freq, r820t_calibrate,
            r820t_set_if_bandwidth), gains and AGC as libairspy sets
            them, then the receiver start (r820t_init).

Values the tuner itself reports are inputs: the filter calibration
code both drivers read back (RTL keeps it in 0x0A, the Airspy discards
it) and the PLL lock (assumed).  The model is register-exact only as far
as the replayed code is; differences in the *meaning* of a register
(e.g. the 3.5 dB VGA step) are for the datasheet.

Examples:
    # RTL fastcard -g 0 at 2.4 Msps vs Airspy R2 0/0/8 at 10 Msps
    r820t_register_model.py -f 161.3M --rtl-rate 2.4M --rtl-gain 0 \\
        --airspy r2 --airspy-rate 10M --lna 0 --mixer 0 --vga 8

    # Check a capture's logged registers against the prediction
    r820t_register_model.py -f 161.3M --airspy r2 --airspy-rate 10M \\
        --lna 0 --mixer 0 --vga 8 --dump capture.log
"""

import argparse
import sys

from thriftyx.hal import r820t

_SOURCES = {
    'librtlsdr': 'https://github.com/osmocom/rtl-sdr (src/tuner_r82xx.c)',
    'librtlsdr-blog': 'https://github.com/rtlsdrblog/rtl-sdr-blog '
                      '(src/tuner_r82xx.c)',
    'airspy': 'https://github.com/airspy/airspyone_firmware '
              '(common/r820t.c, common/airspy_nos_conf.c)',
}

FIRST_REG = 0x05
LAST_REG = 0x1F

# Tracking-filter table (identical in both drivers, from the Linux
# r820t driver): (start MHz, open_d, rf_mux_ploy, tf_c).  librtlsdr looks
# it up by the LO frequency, the Airspy firmware by the RF frequency.
_FREQ_RANGES = (
    (0, 0x08, 0x02, 0xdf), (50, 0x08, 0x02, 0xbe), (55, 0x08, 0x02, 0x8b),
    (60, 0x08, 0x02, 0x7b), (65, 0x08, 0x02, 0x69), (70, 0x08, 0x02, 0x58),
    (75, 0x00, 0x02, 0x44), (80, 0x00, 0x02, 0x44), (90, 0x00, 0x02, 0x34),
    (100, 0x00, 0x02, 0x34), (110, 0x00, 0x02, 0x24),
    (120, 0x00, 0x02, 0x24), (140, 0x00, 0x02, 0x14),
    (180, 0x00, 0x02, 0x13), (220, 0x00, 0x02, 0x13),
    (250, 0x00, 0x02, 0x11), (280, 0x00, 0x02, 0x00),
    (310, 0x00, 0x41, 0x00), (450, 0x00, 0x41, 0x00),
    (588, 0x00, 0x40, 0x00), (650, 0x00, 0x40, 0x00),
)

# Field names per register, from the R820T2 Register Description
# (Rafael Micro, 2012), for the comparison report.
REGISTER_FIELDS = {
    0x05: 'PWD_LT | PWD_LNA1 | LNA_GAIN_MODE | LNA_GAIN[3:0]',
    0x06: 'PWD_PDET1 | PWD_PDET3 | FILT_3DB | PW_LNA[2:0]',
    0x07: 'PWD_MIX | PW0_MIX | MIXGAIN_MODE | MIX_GAIN[3:0]',
    0x08: 'PWD_AMP | PW0_AMP | IMR_G[5:0]',
    0x09: 'PWD_IFFILT | PW1_IFFILT | IMR_P[5:0]',
    0x0A: 'PWD_FILT | PW_FILT[1:0] | FILT_CODE[3:0]',
    0x0B: 'FILT_BW[1:0] | HPF[3:0]',
    0x0C: 'PWD_VGA | VGA_MODE | VGA_CODE[3:0]',
    0x0D: 'LNA_VTH_H[3:0] | LNA_VTH_L[3:0]',
    0x0E: 'MIX_VTH_H[3:0] | MIX_VTH_L[3:0]',
    0x0F: 'CLK_OUT_ENB | CLK_AGC_ENB',
    0x10: 'SEL_DIV[2:0] | REF_DIV2 | CAPX[1:0]',
    0x11: 'PW_LDO_A[1:0]',
    0x12: 'PW_SDM',
    0x13: '(version / reserved)',
    0x14: 'SI2C[1:0] | NI2C[5:0]',
    0x15: 'SDM_IN[8:1]',
    0x16: 'SDM_IN[16:9]',
    0x17: 'PW_LDO_D[1:0] | OPEN_D',
    0x18: '(reserved)',
    0x19: 'PWD_RFFILT | SW_AGC',
    0x1A: 'RFMUX[1:0] | PLL_AUTO_CLK[1:0] | RFFILT[1:0]',
    0x1B: 'TF_NCH[3:0] | TF_LP[3:0]',
    0x1C: 'PDET3_GAIN[3:0]',
    0x1D: 'PDET1_GAIN[2:0] | PDET2_GAIN[2:0]',
    0x1E: 'FILTER_EXT | PDET_CLK[4:0]',
    0x1F: '(reserved)',
}

# Bits the datasheet's register matrix (Table 1-2) prints as a fixed
# 0 or 1 instead of naming a field: {reg: (mask, value)}.  They are not
# unused -- the Linux/librtlsdr driver (written from Rafael's reference
# code) names several of them (0x12[7:5] VCO current, 0x1A[5:4] AGC
# clock, 0x0B[7] narrow IF filter, 0x0A[4] filter Q) -- but the vendor
# publishes neither their function nor whether the printed value is
# required.  R30 is left out: the matrix prints bit 6 as 1 where the
# field table names it FILTER_EXT.
DATASHEET_FIXED_BITS = {
    0x05: (0x40, 0x00), 0x06: (0x18, 0x10), 0x07: (0x80, 0x00),
    0x0A: (0x10, 0x10), 0x0B: (0x90, 0x00), 0x0C: (0xA0, 0xA0),
    0x0F: (0xED, 0x28), 0x10: (0x0C, 0x04), 0x11: (0x3F, 0x03),
    0x12: (0xF7, 0x80), 0x13: (0xFF, 0x00), 0x17: (0x37, 0x34),
    0x18: (0xC0, 0x40), 0x19: (0x6C, 0x0C), 0x1A: (0x30, 0x20),
    0x1C: (0x0D, 0x04), 0x1D: (0xC0, 0xC0), 0x1F: (0xFC, 0xC0),
}


def datasheet_deviation(reg, value):
    """Bits of *value* that differ from the datasheet's fixed bits."""
    mask, fixed = DATASHEET_FIXED_BITS.get(reg, (0, 0))
    return (value ^ fixed) & mask


# Registers that set the analog signal path (gain, filters, mixer,
# image rejection, tracking filter).  The PLL / reference registers
# must differ between a 28.8 MHz and a 25 MHz crystal and are reported
# separately.
SIGNAL_PATH_REGS = (0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C,
                    0x19, 0x1A, 0x1B, 0x1E)
PLL_REGS = (0x10, 0x12, 0x14, 0x15, 0x16)


class _Shadow:
    """Shadow copy of registers 0x05-0x1F, written like the drivers do."""

    def __init__(self, init):
        if len(init) != LAST_REG - FIRST_REG + 1:
            raise ValueError("init table must cover 0x05-0x1F")
        self.regs = {FIRST_REG + i: v for i, v in enumerate(init)}

    def write(self, reg, val):
        self.regs[reg] = val & 0xff

    def write_mask(self, reg, val, mask):
        self.write(reg, (self.regs[reg] & ~mask) | (val & mask))


def _range_for(freq_hz):
    """Tracking-filter row for *freq_hz* (librtlsdr's lookup)."""
    mhz = int(freq_hz) // 1_000_000
    row = _FREQ_RANGES[0]
    for candidate in _FREQ_RANGES:
        if mhz >= candidate[0]:
            row = candidate
    return row


# --------------------------------------------------------------------
# RTL-SDR: osmocom librtlsdr
# --------------------------------------------------------------------

RTL_XTAL_HZ = 28_800_000
_RTL_INIT = (
    0x83, 0x32, 0x75, 0xc0, 0x40, 0xd6, 0x6c, 0xf5, 0x63, 0x75, 0x68,
    0x6c, 0x83, 0x80, 0x00, 0x0f, 0x00, 0xc0, 0x30, 0x48, 0xcc, 0x60,
    0x00, 0x54, 0xae, 0x4a, 0xc0)
_RTL_VER_NUM = 49
_RTL_LPF_BW = (1700000, 1600000, 1550000, 1450000, 1200000, 900000,
               700000, 550000, 450000, 350000)


def _rtl_set_pll(shadow, freq_hz, xtal=RTL_XTAL_HZ, driver='osmocom'):
    """r82xx_set_pll, assuming the PLL locks at once and the VCO fine
    tune read back equals vco_power_ref (no divider correction)."""
    shadow.write_mask(0x1a, 0x00, 0x0c)          # autotune 128 kHz
    regs = [shadow.regs[0x10 + i] for i in range(7)]
    regs[0] &= ~0x10                              # refdiv2 = 0
    if driver == 'blog':
        regs[2] = 0x06                            # VCO current max
    else:
        regs[2] = (regs[2] & ~0xe0) | 0x80        # VCO current 100
    freq_khz = (int(freq_hz) + 500) // 1000
    mix_div, div_num = 2, 0
    while mix_div <= 64:
        if 1_770_000 <= freq_khz * mix_div < 3_540_000:
            div_buf = mix_div
            while div_buf > 2:
                div_buf >>= 1
                div_num += 1
            break
        mix_div <<= 1
    else:
        raise ValueError(f"no PLL divider for {freq_hz} Hz")
    regs[0] = (regs[0] & ~0xe0) | ((div_num << 5) & 0xe0)
    if driver == 'blog':
        nint, sdm = _blog_sdm(int(freq_hz) * mix_div, xtal)
    else:
        vco_div = (xtal + 65536 * int(freq_hz) * mix_div) // (2 * xtal)
        nint, sdm = vco_div // 65536, vco_div % 65536
    ni = (nint - 13) // 4
    si = nint - 4 * ni - 13
    regs[4] = (ni + (si << 6)) & 0xff
    regs[2] = (regs[2] & ~0x08) | (0x08 if sdm == 0 else 0x00)
    regs[5], regs[6] = sdm & 0xff, sdm >> 8
    for i, val in enumerate(regs):
        shadow.write(0x10 + i, val)
    shadow.write_mask(0x1a, 0x08, 0x08)          # autotune 8 kHz (locked)


def _blog_sdm(vco_hz, xtal):
    """The RTL-SDR Blog driver's (older) integer/SDM split."""
    nint = vco_hz // (2 * xtal)
    vco_fra = (vco_hz - 2 * xtal * nint) // 1000
    ref_khz = (xtal + 500) // 1000
    n_sdm, sdm = 2, 0
    while vco_fra > 1 and n_sdm:
        if vco_fra > (2 * ref_khz // n_sdm):
            sdm = (sdm + 32768 // (n_sdm // 2)) & 0xffff
            vco_fra -= 2 * ref_khz // n_sdm
            if n_sdm >= 0x8000:
                break
        n_sdm = (n_sdm << 1) & 0xffff
    return nint, sdm


def _rtl_bandwidth(shadow, bw):
    """r82xx_set_bandwidth; returns the IF (Hz) librtlsdr then uses."""
    if bw > 7_000_000:
        reg_0a, reg_0b, int_freq = 0x10, 0x0b, 4_570_000
    elif bw > 6_000_000:
        reg_0a, reg_0b, int_freq = 0x10, 0x2a, 4_570_000
    elif bw > _RTL_LPF_BW[0] + 350_000 + 380_000:
        reg_0a, reg_0b, int_freq = 0x10, 0x6b, 3_570_000
    else:
        reg_0a, reg_0b, int_freq, real_bw = 0x00, 0x80, 2_300_000, 0
        if bw > _RTL_LPF_BW[0] + 350_000:
            bw -= 380_000
            int_freq += 380_000
            real_bw += 380_000
        else:
            reg_0b |= 0x20
        if bw > _RTL_LPF_BW[0]:
            bw -= 350_000
            int_freq += 350_000
            real_bw += 350_000
        else:
            reg_0b |= 0x40
        i = len(_RTL_LPF_BW)
        for j, lpf in enumerate(_RTL_LPF_BW):
            if bw > lpf:
                i = j
                break
        i -= 1
        i = max(i, 0)
        reg_0b |= 15 - i
        real_bw += _RTL_LPF_BW[i]
        int_freq -= real_bw // 2
    shadow.write_mask(0x0a, reg_0a, 0x10)
    shadow.write_mask(0x0b, reg_0b, 0xef)
    return int_freq


def _rtl_set_gain(shadow, manual, gain_tenths):
    """r82xx_set_gain."""
    if not manual:
        shadow.write_mask(0x05, 0x00, 0x10)
        shadow.write_mask(0x07, 0x10, 0x10)
        shadow.write_mask(0x0c, r820t.RTL_AGC_VGA, 0x9f)
        return None
    shadow.write_mask(0x05, 0x10, 0x10)
    shadow.write_mask(0x07, 0x00, 0x10)
    shadow.write_mask(0x0c, r820t.RTL_MANUAL_VGA, 0x9f)
    lna, mix = r820t.rtl_gain_indices(gain_tenths)
    shadow.write_mask(0x05, lna, 0x0f)
    shadow.write_mask(0x07, mix, 0x0f)
    return lna, mix


RTL_DRIVERS = ('osmocom', 'blog')


def rtl_registers(freq_hz, rate_hz, gain_db, cal_code=None,
                  auto_gain=False, driver='osmocom'):
    """R820T2 registers after upstream fastcard opens an RTL-SDR.

    Parameters
    ----------
    freq_hz, rate_hz : float
        Tuned (RF) frequency and I/Q sample rate.
    gain_db : float
        fastcard ``-g``: snapped to the nearest supported gain and set
        in manual mode (fastcard always enables manual gain, so 0 dB is
        LNA 0 / Mixer 0, not AGC).
    cal_code : int or None
        Filter calibration code the tuner reports (low nibble of 0x0A).
        ``None`` leaves it 0 and marks 0x0A as device-dependent.
    auto_gain : bool
        Model ``rtl_sdr -g 0`` (tuner AGC) instead of fastcard.
    driver : str
        ``'osmocom'`` (osmocom/rtl-sdr) or ``'blog'`` (rtlsdrblog/
        rtl-sdr-blog, as used with RTL-SDR Blog V3 dongles): it changes
        the init value of 0x06, the IF filter gain, loop-through, the
        PLL VCO current and SDM, and the divider-buffer current.

    Returns
    -------
    (dict, dict)
        Register values, and facts: IF frequency, LNA/Mixer indices,
        gain in tenths of a dB.
    """
    if driver not in RTL_DRIVERS:
        raise ValueError(f"unknown RTL driver {driver!r}")
    blog = driver == 'blog'
    init = list(_RTL_INIT)
    if blog:
        init[0x06 - FIRST_REG] = 0x30
    shadow = _Shadow(init)
    # r82xx_set_tv_standard(bw=3, digital, 0) inside r82xx_init
    shadow.write_mask(0x0c, 0x00, 0x0f)
    shadow.write_mask(0x13, _RTL_VER_NUM, 0x3f)
    shadow.write_mask(0x1d, 0x00, 0x38)
    shadow.write_mask(0x0b, 0x6b, 0x60)
    shadow.write_mask(0x0f, 0x04, 0x04)
    shadow.write_mask(0x10, 0x00, 0x03)
    _rtl_set_pll(shadow, 56_000_000, driver=driver)
    shadow.write_mask(0x0b, 0x10, 0x10)
    shadow.write_mask(0x0b, 0x00, 0x10)
    shadow.write_mask(0x0f, 0x00, 0x04)
    code = 0 if cal_code is None else int(cal_code) & 0x0f
    if code == 0x0f:
        code = 0
    shadow.write_mask(0x0a, 0x10 | code, 0x1f)
    shadow.write_mask(0x0b, 0x6b, 0xef)
    shadow.write_mask(0x07, 0x00, 0x80)
    shadow.write_mask(0x06, 0x30 if blog else 0x10, 0x30)
    shadow.write_mask(0x1e, 0x60, 0x60)
    shadow.write_mask(0x05, 0x80 if blog else 0x01, 0x80)
    shadow.write_mask(0x1f, 0x00, 0x80)
    shadow.write_mask(0x0f, 0x00, 0x80)
    shadow.write_mask(0x19, 0x60, 0x60)
    # r82xx_sysfreq_sel(0, digital, DVB-T)
    shadow.write_mask(0x1d, 0xe5, 0xc7)
    shadow.write_mask(0x1c, 0x24, 0xf8)
    shadow.write(0x0d, 0x53)
    shadow.write(0x0e, 0x75)
    shadow.write_mask(0x05, 0x00, 0x60)
    shadow.write_mask(0x06, 0x00, 0x08)
    shadow.write_mask(0x11, 0x38, 0x38)
    shadow.write_mask(0x17, 0xa0 if blog else 0x30, 0x30)
    shadow.write_mask(0x0a, 0x40, 0x60)
    shadow.write_mask(0x1d, 0x00, 0x38)
    shadow.write_mask(0x1c, 0x00, 0x04)
    shadow.write_mask(0x06, 0x00, 0x40)
    shadow.write_mask(0x1a, 0x30, 0x30)
    shadow.write_mask(0x1d, 0x18, 0x38)
    shadow.write_mask(0x1c, 0x24, 0x04)
    shadow.write_mask(0x1e, 14, 0x1f)
    shadow.write_mask(0x1a, 0x20, 0x30)
    # rtlsdr_set_sample_rate -> r820t_set_bw(rate).  Its retune to the
    # not-yet-set frequency 0 fails without touching the final state.
    if_hz = _rtl_bandwidth(shadow, int(rate_hz))
    # rtlsdr_set_center_freq -> r82xx_set_freq: mux + PLL at the LO
    lo_hz = int(freq_hz) + if_hz
    _, open_d, rf_mux_ploy, tf_c = _range_for(lo_hz)
    shadow.write_mask(0x17, open_d, 0x08)
    shadow.write_mask(0x1a, rf_mux_ploy, 0xc3)
    shadow.write(0x1b, tf_c)
    shadow.write_mask(0x10, 0x00, 0x0b)          # XTAL_HIGH_CAP_0P
    shadow.write_mask(0x08, 0x00, 0x3f)
    shadow.write_mask(0x09, 0x00, 0x3f)
    if blog:
        shadow.write_mask(0x0c, 0x08, 0x9f)      # r82xx_set_vga_gain
    _rtl_set_pll(shadow, lo_hz, driver=driver)
    facts = {'if_hz': if_hz, 'lo_hz': lo_hz, 'xtal_hz': RTL_XTAL_HZ,
             'cal_code_known': cal_code is not None, 'driver': driver}
    if auto_gain:
        _rtl_set_gain(shadow, False, 0)
        facts.update(gain='AGC', lna=None, mixer=None)
    else:
        _rtl_set_gain(shadow, True, 0)           # set_tuner_gain_mode(1)
        tenths = r820t.nearest_rtl_gain(gain_db)
        lna, mix = _rtl_set_gain(shadow, True, tenths)
        facts.update(gain=tenths, lna=lna, mixer=mix)
    return shadow.regs, facts


# --------------------------------------------------------------------
# Airspy: airspyone firmware
# --------------------------------------------------------------------

_AIRSPY_INIT = (
    0x90, 0x80, 0x60, 0x80, 0x40, 0xA8, 0x0F, 0x40, 0x63, 0x75, 0xF8,
    0x7C, 0x42, 0x06, 0x00, 0x0F, 0x00, 0xC0, 0xA0, 0x48, 0xCC, 0x60,
    0x00, 0x54, 0xAE, 0x0A, 0xC0)

# Per model (airspy_nos_conf.c = R2, airspy_mini_conf.c): crystal,
# per I/Q rate (IF frequency, r820t_if_bw), and where the init table
# differs from the R2's.
AIRSPY_MODELS = {
    'r2': {'xtal_hz': 25_000_000,
           'rates': {10_000_000: (5_000_000, 59),
                     2_500_000: (1_250_000, 0)},
           'init': {}},
    'mini': {'xtal_hz': 24_000_000,
             'rates': {6_000_000: (3_000_000, 32),
                       3_000_000: (1_500_000, 16)},
             'init': {0x0F: 0xE8}},
}
_CALIBRATION_LO_HZ = 88_000_000


def _airspy_set_pll(shadow, freq_hz, xtal_hz):
    """r820t_set_pll (firmware integer arithmetic)."""
    ref = xtal_hz >> 1
    for div_num in range(6):
        vco = (int(freq_hz) << (div_num + 1)) & 0xffffffff
        if 1_770_000_000 <= vco <= 3_900_000_000:
            break
    else:
        raise ValueError(f"no PLL divider for {freq_hz} Hz")
    vco += ref >> 16
    ref <<= 8
    mask, rem = 1 << 23, 0
    while mask > 0 and vco > 0:
        if vco >= ref:
            rem |= mask
            vco -= ref
        ref >>= 1
        mask >>= 1
    nint = ((rem >> 16) - 13) & 0xff
    sdm = rem & 0xffff
    shadow.write_mask(0x10, (div_num << 5) & 0xff, 0xe0)
    shadow.write(0x14, (nint >> 2) + ((nint & 3) << 6))
    if sdm == 0:
        shadow.write_mask(0x12, 0x08, 0x08)
    else:
        shadow.write(0x15, sdm & 0xff)
        shadow.write(0x16, sdm >> 8)
        shadow.write_mask(0x12, 0x00, 0x08)


def _airspy_set_freq(shadow, freq_hz, if_hz, xtal_hz):
    """r820t_set_freq: tracking filter by RF, PLL at RF + IF."""
    _, open_d, rf_mux_ploy, tf_c = _range_for(freq_hz)
    shadow.write_mask(0x17, open_d, 0x08)
    shadow.write_mask(0x1a, rf_mux_ploy, 0xc3)
    shadow.write(0x1b, tf_c)
    shadow.write_mask(0x10, 0x08, 0x0b)
    shadow.write_mask(0x08, 0x00, 0x3f)
    shadow.write_mask(0x09, 0x00, 0x3f)
    _airspy_set_pll(shadow, int(freq_hz) + if_hz, xtal_hz)


def airspy_registers(freq_hz, rate_hz, lna, mixer, vga, model='r2',
                     lna_agc=False, mixer_agc=False):
    """R820T2 registers after Thrifty-X starts an Airspy capture.

    Replays ``capture``'s order (sample rate, frequency, gains, AGC)
    and then the firmware's receiver start, which re-initialises the
    tuner from its shadow registers, calibrates the filter (and
    discards the code), retunes and sets the fixed IF bandwidth.

    Returns
    -------
    (dict, dict)
        Register values, and facts: IF, LO, crystal.
    """
    spec = AIRSPY_MODELS[model]
    xtal = spec['xtal_hz']
    try:
        if_hz, if_bw = spec['rates'][int(rate_hz)]
    except KeyError:
        raise ValueError(f"Airspy {model} has no {rate_hz} I/Q rate; "
                         f"valid: {sorted(spec['rates'])}") from None
    init = list(_AIRSPY_INIT)
    for reg, value in spec['init'].items():
        init[reg - FIRST_REG] = value
    shadow = _Shadow(init)
    # Before the receiver starts, set_freq uses the boot IF (conf 0).
    boot_if = spec['rates'][max(spec['rates'])][0]
    _airspy_set_freq(shadow, freq_hz, boot_if, xtal)
    shadow.write_mask(0x05, int(lna), 0x0f)
    shadow.write_mask(0x07, int(mixer), 0x0f)
    shadow.write_mask(0x0c, int(vga), 0x0f)
    shadow.write_mask(0x05, 0x00 if lna_agc else 0x10, 0x10)
    shadow.write_mask(0x07, 0x10 if mixer_agc else 0x00, 0x10)
    # Receiver start: r820t_init(if) then r820t_set_if_bandwidth(bw)
    _airspy_set_freq(shadow, freq_hz, if_hz, xtal)
    shadow.write_mask(0x0b, 0x08, 0x60)
    shadow.write_mask(0x0f, 0x04, 0x04)
    shadow.write_mask(0x10, 0x00, 0x03)
    _airspy_set_pll(shadow, _CALIBRATION_LO_HZ, xtal)
    shadow.write_mask(0x0b, 0x10, 0x10)
    shadow.write_mask(0x0b, 0x00, 0x10)
    shadow.write_mask(0x0f, 0x00, 0x04)
    _airspy_set_freq(shadow, freq_hz, if_hz, xtal)
    modes = (0xE0, 0x80, 0x60, 0x00)
    shadow.write(0x0a, 0xB0 | (15 - (if_bw & 0x0f)))
    shadow.write(0x0b, 0x0F | modes[if_bw >> 4])
    facts = {'if_hz': if_hz, 'lo_hz': int(freq_hz) + if_hz,
             'xtal_hz': xtal, 'lna': int(lna), 'mixer': int(mixer),
             'vga': int(vga)}
    return shadow.regs, facts


# --------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------

def compare(rtl, airspy):
    """Rows ``(reg, rtl, airspy, differing bit mask, group)``."""
    rows = []
    for reg in range(FIRST_REG, LAST_REG + 1):
        a, b = rtl[reg], airspy[reg]
        if reg in SIGNAL_PATH_REGS:
            group = 'signal'
        elif reg in PLL_REGS:
            group = 'pll'
        else:
            group = 'other'
        rows.append((reg, a, b, a ^ b, group))
    return rows


def read_dump(stream):
    """Parse ``0xNN=0xVV`` tokens into {reg: value}.

    Accepts capture's ``tuner registers: 0x00=0x96 0x01=...`` stderr
    line as is, or one token per line; other words and ``#`` comments
    are ignored.
    """
    regs = {}
    for line in stream:
        for token in line.split('#', 1)[0].split():
            reg, sep, value = token.partition('=')
            if not sep:
                continue
            try:
                regs[int(reg, 16)] = int(value, 16)
            except ValueError:
                raise ValueError(
                    f"bad register token {token!r}") from None
    return regs


def _metric(text):
    text = text.strip()
    scale = {'k': 1e3, 'M': 1e6, 'G': 1e9}.get(text[-1:], 1)
    return float(text[:-1] if scale != 1 else text) * scale


def _main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-f', '--freq', type=_metric, required=True,
                        help="tuned RF frequency (Hz, k/M/G suffixes)")
    parser.add_argument('--rtl-rate', type=_metric, default=2.4e6)
    parser.add_argument('--rtl-gain', type=float, default=0.0,
                        help="fastcard -g (dB); fastcard sets it in "
                             "manual mode, 0 included")
    parser.add_argument('--rtl-auto-gain', action='store_true',
                        help="model rtl_sdr -g 0 (tuner AGC) instead")
    parser.add_argument('--rtl-driver', choices=RTL_DRIVERS,
                        default='osmocom',
                        help="librtlsdr the RTL-SDR runs with")
    parser.add_argument('--rtl-cal-code', type=lambda s: int(s, 0),
                        default=None,
                        help="RTL filter calibration code (0x0A low "
                             "nibble), when known")
    parser.add_argument('--airspy', choices=sorted(AIRSPY_MODELS),
                        default='r2')
    parser.add_argument('--airspy-rate', type=_metric, default=10e6)
    parser.add_argument('--lna', type=int, default=0)
    parser.add_argument('--mixer', type=int, default=0)
    parser.add_argument('--vga', type=int, default=0)
    parser.add_argument('--dump', type=argparse.FileType('r'),
                        help="Airspy register dump to check against the "
                             "prediction: a capture log holding its "
                             "'tuner registers:' line ('-' for stdin)")
    args = parser.parse_args(argv)

    rtl, rfacts = rtl_registers(args.freq, args.rtl_rate, args.rtl_gain,
                                args.rtl_cal_code, args.rtl_auto_gain,
                                args.rtl_driver)
    air, afacts = airspy_registers(args.freq, args.airspy_rate, args.lna,
                                   args.mixer, args.vga, args.airspy)
    print(f"RTL-SDR ({rfacts['driver']}): IF {rfacts['if_hz'] / 1e6:.3f} MHz, LO "
          f"{rfacts['lo_hz'] / 1e6:.3f} MHz, xtal "
          f"{rfacts['xtal_hz'] / 1e6:g} MHz, gain {rfacts['gain']} "
          f"(LNA {rfacts['lna']}, Mixer {rfacts['mixer']}, VGA "
          f"{'11 AGC' if args.rtl_auto_gain else r820t.RTL_MANUAL_VGA})")
    print(f"Airspy {args.airspy}: IF {afacts['if_hz'] / 1e6:.3f} MHz, LO "
          f"{afacts['lo_hz'] / 1e6:.3f} MHz, xtal "
          f"{afacts['xtal_hz'] / 1e6:g} MHz, LNA {afacts['lna']}, Mixer "
          f"{afacts['mixer']}, VGA {afacts['vga']}")
    print()
    print("reg   RTL   AIR   RTL^AIR   group   datasheet  fields")
    for reg, a, b, diff, group in compare(rtl, air):
        mark = f"{diff:08b}" if diff else '   =    '
        off = [name for name, v in (('RTL', a), ('AIR', b))
               if datasheet_deviation(reg, v)]
        fixed = ' '.join(off) if off else '-'
        note = ''
        if reg == 0x0A and not rfacts['cal_code_known']:
            note = '  (RTL low nibble = device cal code)'
        print(f"0x{reg:02X}  0x{a:02X}  0x{b:02X}  {mark}  {group:6}  "
              f"{fixed:9}  {REGISTER_FIELDS[reg]}{note}")
    print()
    print("RTL^AIR: bits that differ.  datasheet: who departs from a bit "
          "the datasheet prints as a fixed 0/1 (see DATASHEET_FIXED_BITS).")
    if args.dump is not None:
        dump = read_dump(args.dump)
        bad = [(r, v, air[r]) for r, v in sorted(dump.items())
               if FIRST_REG <= r <= LAST_REG and v != air[r]]
        print()
        if bad:
            print("Airspy dump differs from the prediction:")
            for reg, got, want in bad:
                print(f"  0x{reg:02X}: read 0x{got:02X}, predicted "
                      f"0x{want:02X}")
            return 1
        print("Airspy dump matches the prediction.")
    return 0


if __name__ == '__main__':
    sys.exit(_main())
