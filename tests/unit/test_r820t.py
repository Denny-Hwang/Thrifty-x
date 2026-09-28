# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""R820T2 gain facts: RTL-SDR dB gain <-> Airspy stage indices."""

import pytest

from thriftyx.hal import r820t


def test_every_rtl_gain_reproduces_its_own_table_entry():
    # librtlsdr's gain list is the running LNA/mixer step sum, so each
    # entry must map to indices whose summed steps give it back (within
    # the rounding librtlsdr's published list carries).
    for tenths in r820t.RTL_GAINS:
        lna, mixer = r820t.rtl_gain_indices(tenths)
        total = (r820t.stage_gain_db(r820t.LNA_GAIN_STEPS, lna)
                 + r820t.stage_gain_db(r820t.MIXER_GAIN_STEPS, mixer))
        assert total * 10 >= tenths - 20
        assert abs(lna - mixer) <= 1


@pytest.mark.parametrize('gain_db, expected', [
    (0, (0, 0, 8)),
    (14.4, (4, 4, 8)),
    (30, (8, 8, 8)),          # snaps to 29.7 dB
    (40.2, (11, 11, 8)),
    (48.0, (14, 13, 8)),
])
def test_airspy_equivalent_of_rtl_gain(gain_db, expected):
    assert r820t.airspy_equivalent_of_rtl_gain(gain_db) == expected


def test_top_rtl_gain_has_no_airspy_equivalent():
    # 49.6 dB needs LNA 15; libairspy clamps the LNA to 14.
    with pytest.raises(ValueError, match="LNA index 15"):
        r820t.airspy_equivalent_of_rtl_gain(49.6)


def test_nearest_gain_ties_go_low():
    assert r820t.nearest_rtl_gain(0.45) == 0      # 0 and 0.9 equidistant
    assert r820t.nearest_rtl_gain(100) == 496


def test_vga_code_gain():
    # Datasheet R12: -12 dB at code 0, +40.5 dB at 15, 3.5 dB per step.
    assert r820t.vga_gain_db(0)[0] == -12.0
    assert r820t.vga_gain_db(15)[0] == 40.5
    # librtlsdr's comments: code 8 is 16.3 dB, code 11 is 26.5 dB.
    assert r820t.vga_gain_db(8)[1] == pytest.approx(16.3)
    assert r820t.vga_gain_db(11)[1] == pytest.approx(26.5)
