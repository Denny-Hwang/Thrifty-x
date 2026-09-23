# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Airspy R2 SDR driver."""

from thriftyx.hal.airspy_mini import AirspyMiniDevice
from thriftyx.hal.profiles import AIRSPY_R2


class AirspyR2Device(AirspyMiniDevice):
    """Airspy R2 SDR driver.

    The R2 uses the same R820T2 tuner and libairspy API as the Mini; only
    the ADC clock, and therefore the sample rates, differ.  Everything
    model-specific comes from the profile.
    """

    PROFILE = AIRSPY_R2
