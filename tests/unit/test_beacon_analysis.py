# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""analyze_beacon: SoA jumps between two receivers and residuals in m.

Receiver clocks are free-running (either one may be the faster),
coherent, or drifting; beacon transmissions are sometimes missed.  Only
a jump in one receiver's sample count is a discontinuity.
"""

import sys

import numpy as np
import pytest

pytest.importorskip('matplotlib')

from thriftyx import beacon_analysis, toads_data  # noqa: E402
from thriftyx.exceptions import EstimationError  # noqa: E402

FS = 2.4e6


@pytest.fixture(autouse=True)
def _no_windows(monkeypatch):
    monkeypatch.setattr(beacon_analysis.plt, 'show', lambda: None)
    yield
    beacon_analysis.plt.close('all')


def _soas(ppm0, ppm1, n=80, noise=0.05, seed=1):
    """SoAs of a beacon sent every second, of which every seventh is
    missed, at two receivers with the given clock errors."""
    rng = np.random.default_rng(seed)
    t = np.array([10.0 + k for k in range(n) if k % 7 != 3])
    soa0 = 1000.0 + t * FS * (1 + ppm0 * 1e-6) + rng.normal(0, noise, len(t))
    soa1 = 52000.0 + t * FS * (1 + ppm1 * 1e-6) + rng.normal(0, noise,
                                                              len(t))
    return np.column_stack([soa0, soa1])


@pytest.mark.parametrize('ppm0, ppm1', [
    (-11.29, -4.6),    # rx1 faster
    (-4.6, -11.29),    # rx1 slower: every step used to be flagged
    (19.66, -5.45),
    (0.0, 0.0),        # coherent clocks: noise used to decide
])
def test_steady_clocks_have_no_discontinuities(ppm0, ppm1):
    assert list(beacon_analysis.find_discontinuities(_soas(ppm0, ppm1))) == []


def test_drifting_clock_is_not_a_discontinuity():
    soa = _soas(3.0, -8.0, n=600)
    t = (soa[:, 0] - soa[0, 0]) / FS
    soa[:, 1] += 0.5 * FS * 2e-9 * t ** 2     # the rate drifts 1.2 ppm
    assert list(beacon_analysis.find_discontinuities(soa)) == []


@pytest.mark.parametrize('ppm0, ppm1', [(-11.29, -4.6), (-4.6, -11.29),
                                        (0.0, 0.0)])
def test_jumps_of_either_sign_are_found(ppm0, ppm1):
    """The signed test (dsdoa > 10 * mean) could never flag a negative
    jump, e.g. rx0 losing samples."""
    soa = _soas(ppm0, ppm1)
    soa[21:, 0] += 16384        # rx0 counts a block it never received
    soa[45:, 1] += 3            # rx1 counts three extra samples
    assert list(beacon_analysis.find_discontinuities(soa)) == [20, 44]


@pytest.mark.parametrize('rx, steps, size', [
    (1, range(27, 32), 16384),       # a burst of dropped USB transfers
    (0, range(27, 32), -16384),
    (1, range(27, 37, 2), 5),        # every other step
])
def test_runs_of_jumps_are_found(rx, steps, size):
    """Regression: jumps in 5 of the 9 steps around one set the local
    (median) rate, and the jumps it covered were missed."""
    soa = _soas(-11.29, -4.6)
    for k in steps:
        soa[k + 1:, rx] += size
    assert list(beacon_analysis.find_discontinuities(soa)) == list(steps)


def _analysis_input(soa):
    detections = [
        toads_data.DetectionResult(
            10.0 + i, 0, soa[i, rx], toads_data.CarrierSyncInfo(
                0, 0.0, 100.0, 1.0),
            toads_data.CorrDetectionInfo(0, 0.0, 100.0, 1.0),
            rxid=rx, txid=0)
        for i in range(len(soa)) for rx in (0, 1)]
    matches = np.arange(2 * len(soa)).reshape(-1, 2)
    return toads_data.toads_array(detections, with_ids=True), matches


def _printed_std(out):
    line = next(li for li in out.splitlines() if li.startswith('residuals'))
    return float(line.split('std dev = ')[1].split(' m')[0])


def test_rx1_slower_is_analysed(capsys):
    """Regression: every beacon was a discontinuity and analyze crashed
    with 'need at least one array to concatenate'."""
    detections, matches = _analysis_input(_soas(-4.6, -11.29))
    beacon_analysis.analyze(detections, matches, FS, deg=1)
    out = capsys.readouterr().out
    assert 'Number of discontinuities: 0' in out
    # sqrt(2) * 0.05 samples at 124.9 m per sample
    assert _printed_std(out) == pytest.approx(8.8, abs=1.5)


def test_residuals_scale_with_the_sample_rate(capsys):
    """Regression: metres were always computed at 6 MSPS."""
    detections, matches = _analysis_input(_soas(-11.29, -4.6, noise=0.5))
    stds = []
    for rate in (3e6, 6e6, 10e6):
        beacon_analysis.analyze(detections, matches, rate, deg=1)
        stds.append(_printed_std(capsys.readouterr().out))
    assert stds[0] == pytest.approx(2 * stds[1], rel=0.01)
    assert stds[2] == pytest.approx(0.6 * stds[1], rel=0.01)


def test_nothing_to_fit_is_an_estimation_error():
    detections, matches = _analysis_input(_soas(0.0, 0.0, n=8))
    with pytest.raises(EstimationError, match='nothing to fit'):
        beacon_analysis.analyze(detections, matches, FS)
    with pytest.raises(EstimationError, match='fewer than two'):
        beacon_analysis.analyze(detections, np.array([]), FS)


def _write_cli_input(tmp_path, soa):
    (tmp_path / 'data.toads').write_text(''.join(
        '{} 0 {:.6f} 0 {:.6f} 0 0.0 100.0 1.0 0 0.0 100.0 1.0\n'.format(
            rx, 10.0 + i, soa[i, rx])
        for i in range(len(soa)) for rx in (0, 1)))
    (tmp_path / 'data.match').write_text(''.join(
        '{} {}\n'.format(2 * i, 2 * i + 1) for i in range(len(soa))))


def test_cli_sample_rate(tmp_path, monkeypatch, capsys):
    soa = _soas(-11.29, -4.6, noise=0.5)
    monkeypatch.chdir(tmp_path)
    _write_cli_input(tmp_path, soa)
    stds = []
    for rate in ('2.4M', '10M'):
        monkeypatch.setattr(sys, 'argv', ['analyze_beacon', '--deg', '1',
                                          '-s', rate])
        beacon_analysis._main()
        stds.append(_printed_std(capsys.readouterr().out))
    assert stds[0] == pytest.approx(10 / 2.4 * stds[1], rel=0.01)


def test_cli_default_rate_warning_names_the_residuals(tmp_path, monkeypatch,
                                                      caplog):
    """The fallback warning, shared with tdoa, spoke of TDOA and position
    estimates."""
    soa = _soas(-11.29, -4.6, noise=0.5)
    monkeypatch.chdir(tmp_path)
    _write_cli_input(tmp_path, soa)
    monkeypatch.setattr(sys, 'argv', ['analyze_beacon', '--deg', '1'])
    beacon_analysis._main()
    assert 'The residuals in metres are wrong' in caplog.text
    assert 'position' not in caplog.text
