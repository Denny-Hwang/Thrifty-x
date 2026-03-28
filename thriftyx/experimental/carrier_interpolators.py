# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""A host of different carrier peak interpolation techniques."""

import numpy as np
from scipy.optimize import curve_fit


def _dirichlet_kernel(xdata, block_len, carrier_len):
    # pylint: disable=invalid-name
    N, W = block_len, carrier_len
    xdata = np.array(xdata, dtype=np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        weights = np.sin(np.pi*W*xdata/N) / np.sin(np.pi*xdata/N) / W
        weights[np.isnan(weights)] = 1
    return weights


def none(fft_mag, peak):
    return 0


def make_dirichlet(block_len, carrier_len, width=6):
    def _fit_model(xdata, amplitude, time_offset):
        xdata = np.array(xdata, dtype=np.float64)
        dirichlet = _dirichlet_kernel(xdata-time_offset,
                                      block_len,
                                      carrier_len)
        return amplitude * np.abs(dirichlet)

    def _interpolator(fft_mag, peak):
        xdata = np.array(np.arange(-(width//2), width//2+1))
        ydata = fft_mag[peak + xdata]
        initial_guess = (fft_mag[peak], 0)
        popt, _ = curve_fit(_fit_model, xdata, ydata, p0=initial_guess)
        _, fit_offset = popt
        return fit_offset

    return _interpolator


def parabolic(fft_mag, peak):
    """Estimate sub-bin carrier frequency by fitting a parabola."""
    # pylint: disable=invalid-name
    if peak == 0 or peak >= len(fft_mag) - 1:
        return 0
    a, b, c = fft_mag[peak-1], fft_mag[peak], fft_mag[peak+1]
    denom = 4*b - 2*a - 2*c
    if abs(denom) < 1e-12:
        return 0
    offset = (c - a) / denom
    return offset


def gaussian(fft_mag, peak):
    """Estimate sub-bin carrier frequency by fitting a Gaussian."""
    # pylint: disable=invalid-name
    if peak == 0 or peak >= len(fft_mag) - 1:
        return 0
    a, b, c = fft_mag[peak-1], fft_mag[peak], fft_mag[peak+1]
    if a <= 0 or b <= 0 or c <= 0:
        return 0
    a, b, c = np.log(a), np.log(b), np.log(c)
    denom = 4*b - 2*a - 2*c
    if abs(denom) < 1e-12:
        return 0
    offset = (c - a) / denom
    return offset


def make_parabole_fit(width):
    def _interpolator(fft_mag, peak):
        xdata = np.array(np.arange(-(width//2), width//2+1))
        ydata = fft_mag[peak + xdata]
        coeffs = np.polyfit(xdata, ydata, 2)
        offset = -coeffs[1] / coeffs[0] / 2

        return offset

    return _interpolator


def make_corr_parabolic(corr_width, block_len, carrier_len):
    rel = np.arange(-(corr_width//2), corr_width//2+1)
    dirichlet = _dirichlet_kernel(rel, block_len, carrier_len)

    def _interpolator(fft_mag, peak):
        a = np.sum(fft_mag[peak+rel-1] * dirichlet)
        b = np.sum(fft_mag[peak+rel] * dirichlet)
        c = np.sum(fft_mag[peak+rel+1] * dirichlet)

        offset = (c - a) / (4*b - 2*a - 2*c)
        return offset

    return _interpolator


def cosine(fft_mag, peak):
    if peak == 0 or peak >= len(fft_mag) - 1:
        return 0
    a, b, c = fft_mag[peak-1], fft_mag[peak], fft_mag[peak+1]
    if abs(2*b) < 1e-12:
        return 0
    cos_omega = (a + c) / (2*b)
    if cos_omega > 1 or cos_omega < -1:
        return 0
    omega = np.arccos(cos_omega)
    sin_omega = np.sin(omega)
    if abs(sin_omega) < 1e-12 or abs(omega) < 1e-12:
        return 0
    theta = np.arctan((a - c) / (2*b*sin_omega))
    offset = -theta / omega
    return offset


INTERPOLATORS = {
    'none': none,
    'parabolic': parabolic,
    'gaussian': gaussian,
    'cosine': cosine,
    'dirichlet': make_dirichlet,
    }
