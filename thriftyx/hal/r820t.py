# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""R820T2 gain facts shared by the RTL-SDR and the Airspy.

Both receivers carry the Rafael R820T2 tuner and write the same three
gain fields: LNA_GAIN (R5[3:0]), MIX_GAIN (R7[3:0]) and VGA_CODE
(R12[3:0]).  librtlsdr exposes one dB value and splits it over the LNA
and mixer with a fixed VGA code; libairspy exposes the three indices.
This module converts between the two so an RTL-SDR gain can be matched
register for register on an Airspy.  Pure data, no ctypes.

Sources: osmocom rtl-sdr ``src/tuner_r82xx.c`` (``r82xx_set_gain``,
the measured step tables) and ``src/librtlsdr.c``
(``rtlsdr_get_tuner_gains``); upstream Thrifty ``fastcard`` (always
manual gain, snapped with ``nearest_gain``); Rafael Micro, R820T2
Register Description (2012), R12 (VGA -12 dB to +40.5 dB, 3.5 dB/step).
"""

# librtlsdr's measured per-step gains in tenths of a dB (index 0 = 0).
LNA_GAIN_STEPS = (0, 9, 13, 40, 38, 13, 31, 22, 26, 31, 26, 14, 19, 5, 35,
                  13)
MIXER_GAIN_STEPS = (0, 5, 10, 10, 19, 9, 10, 25, 17, 10, 8, 16, 13, 6, 3,
                    -8)
VGA_GAIN_STEPS = (0, 26, 26, 30, 42, 35, 24, 13, 14, 32, 36, 34, 35, 37, 35,
                  36)
# librtlsdr's VGA_BASE_GAIN: VGA code 0 in tenths of a dB.
VGA_BASE_GAIN = -47

# rtlsdr_get_tuner_gains() for the R820T/R820T2, tenths of a dB.
RTL_GAINS = (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229,
             254, 280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439,
             445, 480, 496)

# VGA code librtlsdr fixes in manual gain mode (fastcard, rtl_sdr -g N
# with N != 0), and in AGC mode (rtl_sdr -g 0).
RTL_MANUAL_VGA = 8
RTL_AGC_VGA = 11

# Highest LNA index libairspy accepts (it clamps 15 to 14); librtlsdr
# uses 15 for its top gain, 49.6 dB.
AIRSPY_MAX_LNA = 14


def nearest_rtl_gain(gain_db: float) -> int:
    """Supported RTL-SDR gain (tenths of a dB) nearest *gain_db*.

    Mirrors fastcard's ``nearest_gain`` (ties go to the lower gain).

    >>> nearest_rtl_gain(30)
    297
    """
    target = int(round(float(gain_db) * 10))
    return min(RTL_GAINS, key=lambda g: (abs(target - g), g))


def rtl_gain_indices(gain_tenths: int) -> tuple[int, int]:
    """LNA and mixer indices librtlsdr writes for a manual gain.

    Replays ``r82xx_set_gain``: LNA and mixer steps alternate until the
    running total reaches *gain_tenths*.

    >>> rtl_gain_indices(0)
    (0, 0)
    >>> rtl_gain_indices(297)
    (8, 8)
    """
    total = lna = mixer = 0
    for _ in range(15):
        if total >= gain_tenths:
            break
        lna += 1
        total += LNA_GAIN_STEPS[lna]
        if total >= gain_tenths:
            break
        mixer += 1
        total += MIXER_GAIN_STEPS[mixer]
    return lna, mixer


def airspy_equivalent_of_rtl_gain(gain_db: float) -> tuple[int, int, int]:
    """Airspy (LNA, mixer, VGA) indices matching an RTL-SDR gain.

    The RTL-SDR gain is taken as upstream fastcard applies it: snapped
    to a supported value and set in *manual* mode, 0 dB included, so
    VGA is librtlsdr's fixed code 8.

    Raises
    ------
    ValueError
        The gain needs LNA index 15, which libairspy cannot set.

    >>> airspy_equivalent_of_rtl_gain(0)
    (0, 0, 8)
    >>> airspy_equivalent_of_rtl_gain(30)
    (8, 8, 8)
    """
    lna, mixer = rtl_gain_indices(nearest_rtl_gain(gain_db))
    if lna > AIRSPY_MAX_LNA:
        raise ValueError(
            f"RTL-SDR gain {gain_db:g} dB uses LNA index {lna}; libairspy "
            f"stops at {AIRSPY_MAX_LNA}")
    return lna, mixer, RTL_MANUAL_VGA


def vga_gain_db(code: int) -> tuple[float, float]:
    """VGA gain of *code*: (datasheet, librtlsdr-measured) in dB.

    >>> vga_gain_db(8)
    (16.0, 16.3)
    """
    datasheet = -12.0 + 3.5 * int(code)
    measured = (VGA_BASE_GAIN + sum(VGA_GAIN_STEPS[1:int(code) + 1])) / 10
    return datasheet, measured


def stage_gain_db(steps: tuple[int, ...], index: int) -> float:
    """Measured gain of an LNA or mixer *index* above index 0, in dB."""
    return sum(steps[1:int(index) + 1]) / 10
