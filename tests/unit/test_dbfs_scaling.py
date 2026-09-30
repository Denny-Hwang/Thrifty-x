# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""dBFS scaling from raw SDR samples to the CW bench figures.

Locks down the software half of docs/rtl_vs_airspy_dbfs_audit.md:

* RTL-SDR uint8 and Airspy INT16_IQ raw samples map to the dBFS the
  normalisation in block_data promises (0 dBFS = |z| = 1);
* the Airspy scale follows from libairspy's INT16_IQ arithmetic
  (``(code - 2048) << 4``, a first-order DC blocker, an fs/4 shift and
  a half-band decimator), modelled here from that structure with a
  locally designed half-band kernel -- no upstream code or taps are
  copied.  scripts/airspy_scale_probe.sh runs libairspy's own code;
* carrier_dbfs and noise_dbfs_hz do not depend on the sample rate, so
  the bench's rate-to-rate and device-to-device differences are not
  artefacts of its PSD arithmetic;
* a pure scale factor on the samples moves carrier and noise by the
  same number of dB and leaves C/N0 and the detection decision alone.
"""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import lfilter

from thriftyx.block_data import (AIRSPY_INT16_FULL_SCALE, complex_to_raw,
                                 raw_to_complex)

_SPEC = importlib.util.spec_from_file_location(
    'bench_cw_level',
    Path(__file__).parents[2] / 'scripts' / 'bench_cw_level.py')
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

# libairspy remove_dc(): y[n] = x[n] - x[n-1] + (32100 / 2**15) y[n-1]
DC_POLE = 32100 / 32768
ADC_FULL_SCALE_CODES = 2048          # 12-bit offset binary, mid-code 2048


def _halfband_q15(taps=47):
    """Half-band low-pass in Q15: centre tap 0.5, zeros at even offsets."""
    n = np.arange(taps) - (taps - 1) // 2
    h = 0.5 * np.sinc(n / 2) * np.blackman(taps)
    h[n % 2 == 0] = 0.0
    h[(taps - 1) // 2] = 0.5
    side = h[n % 2 != 0]
    h[n % 2 != 0] = side * (0.5 / side.sum())      # unity DC gain
    return np.rint(h * 32768).astype(np.int64)


def libairspy_int16_iq_model(codes):
    """Real 12-bit ADC codes -> interleaved int16 I/Q, libairspy style."""
    x = (np.asarray(codes, dtype=np.int64) - 2048) << 4
    y = np.floor(lfilter([1.0, -1.0], [1.0, -DC_POLE], x.astype(float)))
    pattern = np.resize([-1.0, -0.5, 1.0, 0.5], y.size)
    s = np.floor(y * pattern)
    kernel = _halfband_q15()
    side = kernel[0::2]                    # taps on the even lane
    even = np.convolve(s[0::2], side)[:s.size // 2]
    i_lane = np.floor(even / 32768.0)
    delay = (side.size) // 2               # libairspy: (len / 2 + 1) >> 1
    q_lane = np.concatenate([np.zeros(delay), s[1::2]])[:s.size // 2]
    iq = np.empty(s.size, dtype=np.int16)
    iq[0::2] = np.clip(i_lane, -32768, 32767)
    iq[1::2] = np.clip(q_lane, -32768, 32767)
    return iq


def _adc_tone(amplitude_codes, f_over_fs, n, seed=0):
    """Offset-binary 12-bit codes of a real tone, lightly dithered."""
    rng = np.random.default_rng(seed)
    v = (2048 + amplitude_codes * np.cos(2 * np.pi * f_over_fs * np.arange(n))
         + rng.uniform(-0.5, 0.5, n))
    return np.clip(np.rint(v), 0, 4095).astype(np.int64)


def _airspy_capture(rate, amplitude_codes, offset_hz, segments=4):
    """Normalised complex samples of an Airspy tone *offset_hz* off centre.

    The ADC runs at twice the I/Q rate with the IF at fs_adc / 4; the
    fs/4 shift maps an IF tone at fs_adc/4 - df to baseband +df.
    """
    nfft = bench.fft_size(rate)
    settle = 256
    n_iq = nfft * segments + settle
    fs_adc = 2 * rate
    codes = _adc_tone(amplitude_codes, 0.25 - offset_hz / fs_adc, 2 * n_iq)
    z = raw_to_complex(libairspy_int16_iq_model(codes), bit_depth=12)
    return z[settle:]


def _figures(z, rate, offset_hz=15e3):
    acc = bench.Accumulator(bench.fft_size(rate))
    acc.add(z)
    return bench.figures(acc, rate, offset_hz, center_hz=161.3e6)


def _expected_airspy_gain(offset_hz, rate):
    """|I + jQ| per ADC code: 16 * 1/2 * DC-blocker gain at the IF."""
    w = 2 * np.pi * (0.25 - offset_hz / (2 * rate))
    dc_block = abs((1 - np.exp(-1j * w)) / (1 - DC_POLE * np.exp(-1j * w)))
    return 8.0 * dc_block


# --------------------------------------------------------------------
# Raw -> normalised amplitude
# --------------------------------------------------------------------

@pytest.mark.parametrize('dbfs', [-1.0, -20.0, -40.0])
def test_rtl_uint8_tone_reads_its_dbfs(dbfs):
    """A complex tone of |z| = a, quantised to uint8, reads 20 log10 a."""
    rate = 2.4e6
    nfft = bench.fft_size(rate)
    n = nfft * 4
    rng = np.random.default_rng(3)
    t = np.arange(n) / rate
    z = (10 ** (dbfs / 20) * np.exp(2j * np.pi * 15e3 * t)
         + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / 128)
    raw = complex_to_raw(z.astype(np.complex64), bit_depth=8)
    fig = _figures(raw_to_complex(raw, bit_depth=8), rate)
    assert fig['detected'] == 1
    assert fig['carrier_dbfs'] == pytest.approx(dbfs, abs=0.1)


def test_rtl_uint8_full_range_is_unit_magnitude():
    """uint8 0 and 255 are -127.4/128 and +127.6/128: |I|, |Q| ~ 1."""
    out = raw_to_complex(np.array([0, 255], dtype=np.uint8), bit_depth=8)
    assert out[0].real == pytest.approx(-127.4 / 128, abs=1e-6)
    assert out[0].imag == pytest.approx(127.6 / 128, abs=1e-6)


def test_libairspy_model_gain_is_eight_per_code_times_dc_blocker():
    """The model reproduces the closed-form INT16_IQ gain.

    8 = 16 (the << 4) x 1/2 (a real tone's positive-frequency half);
    the only other factor is the DC blocker's gain at the IF (+0.08 dB
    at fs/4; the half-band has unity DC gain by construction).
    """
    fs_adc = 1.0
    for f in (0.25 - 15e3 / 20e6, 0.25 - 15e3 / 5e6, 0.2, 0.3):
        codes = _adc_tone(512, f, 1 << 16)
        iq = libairspy_int16_iq_model(codes).astype(float)
        mag = np.sqrt(iq[0::2] ** 2 + iq[1::2] ** 2)[512:]
        gain = np.sqrt(np.mean(mag ** 2)) / 512
        w = 2 * np.pi * f / fs_adc
        dc = abs((1 - np.exp(-1j * w)) / (1 - DC_POLE * np.exp(-1j * w)))
        assert 20 * math.log10(gain / (8 * dc)) == pytest.approx(0, abs=0.02)


@pytest.mark.parametrize('rate', [2.5e6, 10e6])
@pytest.mark.parametrize('codes', [16, 128, 1024, 1800])
def test_airspy_adc_tone_reads_its_dbfs(rate, codes):
    """An ADC tone of A codes reads 20 log10(A / 2048) (+0.08 dB DC blocker).

    Linear from 16 codes to within 1 dB of ADC full scale, and the
    residual against /16384 is the DC blocker alone: far below any
    cross-device difference the bench sees.
    """
    fig = _figures(_airspy_capture(rate, codes, 15e3), rate)
    expected = (20 * math.log10(codes / ADC_FULL_SCALE_CODES)
                + 20 * math.log10(_expected_airspy_gain(15e3, rate) / 8))
    assert fig['detected'] == 1
    assert fig['carrier_dbfs'] == pytest.approx(expected, abs=0.05)
    assert abs(20 * math.log10(_expected_airspy_gain(15e3, rate)
                               * ADC_FULL_SCALE_CODES
                               / AIRSPY_INT16_FULL_SCALE)) < 0.1


def test_airspy_carrier_dbfs_does_not_depend_on_rate():
    """Same ADC tone at 2.5 and 10 MSPS: same carrier dBFS."""
    lo = _figures(_airspy_capture(2.5e6, 300, 15e3), 2.5e6)
    hi = _figures(_airspy_capture(10e6, 300, 15e3), 10e6)
    assert hi['carrier_dbfs'] - lo['carrier_dbfs'] == pytest.approx(
        0, abs=0.02)


# --------------------------------------------------------------------
# PSD arithmetic
# --------------------------------------------------------------------

def _noise(rate, n, n0, seed):
    rng = np.random.default_rng(seed)
    sigma = math.sqrt(n0 * rate / 2)
    return (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
            ).astype(np.complex64)


@pytest.mark.parametrize('rate', [2.4e6, 2.5e6, 10e6])
def test_same_tone_and_noise_read_the_same_at_every_rate(rate):
    amp, n0 = 10 ** (-30 / 20), 10 ** (-110 / 10)
    nfft = bench.fft_size(rate)
    n = nfft * 24
    t = np.arange(n) / rate
    z = (amp * np.exp(2j * np.pi * 15_050.0 * t)).astype(np.complex64)
    fig = _figures(z + _noise(rate, n, n0, 7), rate)
    assert fig['carrier_dbfs'] == pytest.approx(-30, abs=0.05)
    assert fig['noise_dbfs_hz'] == pytest.approx(-110, abs=0.1)
    assert fig['cn0_dbhz'] == pytest.approx(80, abs=0.12)


def test_noise_median_is_corrected_to_the_mean_for_few_segments():
    """With 3 segments the raw median reads ~0.5 dB low; corrected it doesn't."""
    rate = 2.4e6
    nfft = bench.fft_size(rate)
    fig = _figures(_noise(rate, nfft * 3, 1e-11, 11), rate)
    assert fig['segments'] == 3
    assert fig['noise_dbfs_hz'] == pytest.approx(-110, abs=0.1)


def test_scale_factor_moves_carrier_and_noise_not_cn0():
    """A gain g on the samples: carrier and noise +20 log g, C/N0 fixed."""
    rate = 10e6
    nfft = bench.fft_size(rate)
    n = nfft * 8
    t = np.arange(n) / rate
    z = (10 ** (-50 / 20) * np.exp(2j * np.pi * 22e3 * t)
         + _noise(rate, n, 1e-12, 5)).astype(np.complex64)
    g_db = -17.0
    ref = _figures(z, rate, 22e3)
    scaled = _figures((z * 10 ** (g_db / 20)).astype(np.complex64), rate,
                      22e3)
    assert scaled['carrier_dbfs'] - ref['carrier_dbfs'] == pytest.approx(
        g_db, abs=1e-3)
    assert scaled['noise_dbfs_hz'] - ref['noise_dbfs_hz'] == pytest.approx(
        g_db, abs=1e-3)
    assert scaled['cn0_dbhz'] == pytest.approx(ref['cn0_dbhz'], abs=1e-3)
    assert scaled['detected'] == ref['detected'] == 1
