# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/bench_cw_level.py: tone detection against the local noise.

With RF off, the CW bench reported a "tone" on every Airspy R2 10 MSPS
capture.  Detection used to compare the tone bins with the 50-300 kHz
noise band and a sigma that assumed independent FFT bins; with seconds
of averaging, a noise floor only a few tenths of a dB higher around the
tone than in that band read as a tone.  These tests pin the fix: the
excess is taken over the noise around the tone, and its sigma counts
the Hann window's bin correlation and the noise estimate's own error.
"""

import csv
import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

_SPEC = importlib.util.spec_from_file_location(
    'bench_cw_level',
    Path(__file__).parents[2] / 'scripts' / 'bench_cw_level.py')
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

RATE = 2.4e6


def _acc(k, shape=None, tones=(), seed=0, rate=RATE):
    """Accumulator of k segments of unit-variance noise with PSD *shape*.

    *shape* (linear, a function of frequency) is applied per FFT bin
    after the time-domain window, so neighbouring bins stay correlated
    exactly as for a real capture.  *tones* are (Hz, amplitude) pairs.
    """
    rng = np.random.default_rng(seed)
    nfft = bench.fft_size(rate)
    acc = bench.Accumulator(nfft)
    freqs = np.fft.fftfreq(nfft, 1.0 / rate)
    gain = np.sqrt(shape(freqs)) if shape else 1.0
    t = np.arange(nfft) / rate
    for start in range(0, k, 32):
        m = min(32, k - start)
        z = (rng.standard_normal((m, nfft))
             + 1j * rng.standard_normal((m, nfft))) / np.sqrt(2)
        spec = np.fft.fft(z * acc.window, axis=1) * gain
        seg_t = t[None, :] + (start + np.arange(m))[:, None] * nfft / rate
        for f, amp in tones:
            spec += np.fft.fft(amp * np.exp(2j * np.pi * f * seg_t)
                               * acc.window, axis=1)
        acc.power += np.sum(spec.real ** 2 + spec.imag ** 2, axis=0)
    acc.segments, acc.samples, acc.energy = k, k * nfft, float(k * nfft)
    return acc


def _raised(db, width=40e3):
    """Noise floor *db* higher within +/- *width* of the centre."""
    return lambda f: np.where(np.abs(f) < width, 10 ** (db / 10), 1.0)


def _skirt(db, corner=60e3):
    """Noise floor *db* higher at the centre, falling linearly (in dB)."""
    return lambda f: 10 ** (db * np.clip(1 - np.abs(f) / corner, 0, None)
                            / 10)


def test_hann_bin_power_correlation():
    rho2 = bench._bin_power_correlation(np.hanning(4096), 3)
    assert rho2[0] == pytest.approx(1.0)
    assert rho2[1] == pytest.approx(4 / 9, rel=1e-3)
    assert rho2[2] == pytest.approx(1 / 36, rel=1e-2)
    assert rho2[3] < 1e-6


@pytest.mark.parametrize('shape', [_raised(1.0), _skirt(4.0)],
                         ids=['raised_1dB', 'skirt_4dB'])
def test_noise_floor_raised_around_the_tone_is_not_a_tone(shape):
    """RF off, floor higher near centre than at 50-300 kHz: no tone.

    The old rule (far-band noise, independent-bin sigma) needed only
    ~0.5 dB of such excess at these k to report a tone every time.
    """
    for seed in range(3):
        fig = bench.figures(_acc(300, shape, seed=seed), RATE, 15e3)
        assert fig['detected'] == 0, seed
        assert abs(fig['excess_sigma']) < bench.DETECT_SIGMA
        assert fig['local_noise_dbfs_hz'] > fig['noise_dbfs_hz'] + 0.5


def test_excess_sigma_is_standard_normal_on_white_noise():
    """At a fixed bin the z statistic has mean ~0 and unit spread."""
    df = RATE / bench.fft_size(RATE)
    z = [bench.figures(_acc(40, seed=s), RATE, 15e3,
                       search_hz=0.5 * df)['excess_sigma']
         for s in range(60)]
    assert abs(np.mean(z)) < 0.4
    assert 0.75 < np.std(z) < 1.3


def test_white_noise_is_not_a_tone():
    for seed in range(5):
        assert bench.figures(_acc(300, seed=seed), RATE, 15e3)[
            'detected'] == 0


@pytest.mark.parametrize('shape', [None, _skirt(4.0)],
                         ids=['white', 'skirt_4dB'])
def test_weak_tone_is_still_detected_and_measured(shape):
    """C/N0 30 dB-Hz is detected; at 40 dB-Hz the carrier reads true.

    (At 30 dB-Hz and k = 300 one sigma of the carrier is ~0.3 dB, so
    accuracy is checked on the stronger tone.)
    """
    n0 = 1.0 / RATE                       # unit variance over RATE Hz
    local = shape(np.array([14_300.0]))[0] if shape else 1.0
    for cn0, seed in ((30, 0), (40, 1)):
        amp = math.sqrt(10 ** (cn0 / 10) * n0)
        fig = bench.figures(_acc(300, shape, tones=[(14_300.0, amp)],
                                 seed=seed), RATE, 15e3, center_hz=161.3e6)
        assert fig['detected'] == 1, cn0
        assert fig['tone_offset_hz'] == pytest.approx(14_300, abs=20)
    assert fig['carrier_dbfs'] == pytest.approx(20 * math.log10(amp),
                                                abs=0.1)
    assert fig['local_noise_dbfs_hz'] == pytest.approx(
        10 * math.log10(n0 * local), abs=0.2)


def test_narrowband_spur_in_the_search_window_is_reported_where_it_is():
    """A spur is real power: still detected, at its own frequency.

    ``tone_offset_hz`` and ``excess_sigma`` of RF-off rows are what
    tells a spur (a fixed offset, large z) from noise.
    """
    n0 = 1.0 / RATE
    amp = math.sqrt(10 ** (35 / 10) * n0)
    fig = bench.figures(_acc(300, tones=[(16_000.0, amp)]), RATE, 15e3)
    assert fig['detected'] == 1
    assert fig['tone_offset_hz'] == pytest.approx(16_000, abs=20)
    assert fig['excess_sigma'] > 50


def test_too_few_local_bins_falls_back_to_the_noise_band():
    fig = bench.figures(_acc(20, seed=1), RATE, 15e3, local_hz=0.0)
    assert fig['local_noise_dbfs_hz'] == fig['noise_dbfs_hz']
    assert fig['detected'] == 0


def test_csv_carries_the_detection_diagnostics():
    assert bench.CSV_FIELDS[-2:] == ('local_noise_dbfs_hz', 'excess_sigma')


def test_appending_to_an_older_csv_upgrades_its_header(tmp_path):
    """Rows from before the new columns keep them empty; nothing is lost."""
    path = tmp_path / 'r.csv'
    old_fields = bench.CSV_FIELDS[:-2]
    path.write_text(','.join(old_fields) + '\n'
                    + ','.join('old' if f == 'unit' else ''
                               for f in old_fields) + '\n')
    bench.append_row(str(path), {'unit': 'new', 'excess_sigma': 7.25,
                                 'local_noise_dbfs_hz': -120.5})
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == list(bench.CSV_FIELDS)
    assert [r['unit'] for r in rows] == ['old', 'new']
    assert rows[0]['excess_sigma'] == ''
    assert rows[1]['excess_sigma'] == '7.250'
    assert rows[1]['local_noise_dbfs_hz'] == '-120.500'
    assert not any(None in r for r in rows)


def test_appending_to_a_foreign_csv_is_refused(tmp_path):
    path = tmp_path / 'other.csv'
    path.write_text('a,b\n1,2\n')
    with pytest.raises(SystemExit):
        bench.append_row(str(path), {'unit': 'x'})
    assert path.read_text() == 'a,b\n1,2\n'
