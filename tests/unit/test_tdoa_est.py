# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Unit tests for thriftyx.tdoa_est — physical-constant + dtype contracts,
the nearest-beacon model, .tdoa file loading and the command line.

These tests pin the small but high-blast-radius contracts: the
speed-of-light constant, the saturating MAX_TDOA cap, and the named
tuples + structured dtypes that downstream code (`tdoa_analysis`,
`pos_est`) reads back.  The estimator itself is tested against exact
arrival times in test_tdoa_model.py.
"""

import io
import sys

import numpy as np
import pytest

from thriftyx import cli, tdoa_est, toads_data
from thriftyx.exceptions import EXIT_CONFIG


def test_speed_of_light_constant_within_tolerance():
    """Constant matches CODATA c to ~0.1% — enough for TDOA ranging."""
    c_codata = 299_792_458.0
    assert abs(tdoa_est.SPEED_OF_LIGHT - c_codata) / c_codata < 1e-3


def test_max_tdoa_corresponds_to_30km_baseline():
    """MAX_TDOA caps at the light-travel time of a 30 km baseline."""
    # MAX_TDOA = 30 km / c
    expected = 30_000.0 / tdoa_est.SPEED_OF_LIGHT
    assert abs(tdoa_est.MAX_TDOA - expected) < 1e-12
    # Sanity: somewhere around 100 microseconds.
    assert 9e-5 < tdoa_est.MAX_TDOA < 1.1e-4


def test_tdoa_info_named_tuple_fields():
    """TdoaInfo field names are frozen — downstream tooling unpacks by name."""
    expected = ('rx0', 'rx1', 'tdoa', 'snr', 'model_quality',
                'det0_idx', 'det1_idx')
    assert tdoa_est.TdoaInfo._fields == expected


def test_tdoa_group_named_tuple_fields():
    """TdoaGroup field names are frozen."""
    assert tdoa_est.TdoaGroup._fields == (
        'group_id', 'timestamp', 'tx', 'tdoas')


def test_tdoa_dtype_round_trips_through_numpy():
    """A row using TDOA_DTYPE survives a NumPy structured-array round-trip."""
    row = np.array([(1, 2, 1.5e-5, 30.0, 0.99, 7, 11)],
                   dtype=list(zip(tdoa_est.TDOA_DTYPE['names'],
                                  tdoa_est.TDOA_DTYPE['formats'],
                                  strict=True)))
    assert int(row['rx0'][0]) == 1
    assert int(row['rx1'][0]) == 2
    assert abs(float(row['tdoa'][0]) - 1.5e-5) < 1e-12
    assert abs(float(row['snr'][0]) - 30.0) < 1e-6


def test_matrix_dtype_extends_tdoa_dtype_with_group_keys():
    """MATRIX_DTYPE prepends (group_id, timestamp, tx) to TDOA_DTYPE."""
    assert tdoa_est.MATRIX_DTYPE['names'][:3] == ('group_id', 'timestamp', 'tx')
    assert tdoa_est.MATRIX_DTYPE['names'][3:] == tdoa_est.TDOA_DTYPE['names']


# --- the nearest-beacon model -----------------------------------------------

def test_find_nearest_value():
    """(Was defined in tdoa_est.py itself, where pytest never collected
    it.)"""
    list_ = [5, 10, 15]
    values = [4, 5, 6, 9, 10, 11, 14, 16]
    expected_output = [0, 0, 0, 1, 1, 1, 2, 2]
    nearest = [tdoa_est.find_nearest_value(list_, v) for v in values]
    np.testing.assert_equal(nearest, expected_output)


def _pair(timestamp, soa0, soa1):
    info = toads_data.CorrDetectionInfo(0, 0.0, 100.0, 1.0)
    return (toads_data.DetectionResult(timestamp, 0, soa0, None, info,
                                       rxid=0, txid=0),
            toads_data.DetectionResult(timestamp, 0, soa1, None, info,
                                       rxid=1, txid=0))


def test_nearest_model_uses_the_closest_beacon():
    """build_model_nearest takes the clock offset of the beacon nearest
    in time.  rx1's clock steps by 10 samples per beacon, so another
    beacon would put the TDOA 10 samples off."""
    fs = 1e6
    beacon_sdoa = np.full(3, 50.0)   # the beacon is 50 samples nearer rx1

    def rx1_soa(t, sdoa, clock_offset):
        # arrival at rx1, in rx1's clock, of what reaches rx0 at t
        return fs * t - sdoa + clock_offset

    beacons = [_pair(float(i), fs * i, rx1_soa(i, 50.0, 1000.0 + 10 * i))
               for i in range(3)]
    model = tdoa_est.build_model_nearest(beacons, beacon_sdoa, fs)
    for t, nearest in ((0.2, 0), (1.2, 1), (1.7, 2), (5.0, 2)):
        mobile = _pair(t, fs * t, rx1_soa(t, 20.0, 1000.0 + 10 * nearest))
        assert model(*mobile) == pytest.approx(20.0 / fs, abs=1e-12)
    assert tdoa_est.build_model_nearest([], [], fs) is None


# --- .tdoa files ------------------------------------------------------------

def _tdoa_file(tmp_path, rows):
    path = tmp_path / 'data.tdoa'
    path.write_text(''.join(
        '{} {:.6f} {} {} {} {} 30.0 0.9 {} {}\n'.format(
            group, 10.0 + group, tx, rx0, rx1, tdoa_ns, 2 * group, 2 * group + 1)
        for group, tx, rx0, rx1, tdoa_ns in rows))
    return str(path)


def test_load_tdoa_groups_one_row(tmp_path):
    """Regression: a one-row file loaded as a 0-d array, which cannot be
    iterated (TypeError in `thriftyx pos`)."""
    groups = tdoa_est.load_tdoa_groups(_tdoa_file(tmp_path,
                                                  [(4, 1, 0, 1, 12.5)]))
    assert len(groups) == 1
    assert groups[0].group_id == 4
    assert groups[0].tdoas['tdoa'][0] == 12.5e-9


def test_load_tdoa_groups_keeps_rows_and_order(tmp_path):
    """Rows are grouped in one pass (a mask per group took quadratic
    time), in order of first appearance, even when a group's rows are
    not adjacent."""
    rows = [(7, 1, 0, 1, 1.0), (3, 2, 0, 1, 2.0), (7, 1, 0, 2, 3.0),
            (3, 2, 1, 2, 4.0), (9, 1, 0, 1, 5.0)]
    groups = tdoa_est.load_tdoa_groups(_tdoa_file(tmp_path, rows))
    assert [g.group_id for g in groups] == [7, 3, 9]
    assert [g.tx for g in groups] == [1, 2, 1]
    assert [g.timestamp for g in groups] == [17.0, 13.0, 19.0]
    assert [list(g.tdoas['tdoa'] * 1e9) for g in groups] == [
        pytest.approx([1.0, 3.0]), pytest.approx([2.0, 4.0]),
        pytest.approx([5.0])]
    assert groups[0].tdoas[['rx0', 'rx1']].tolist() == [(0, 1), (0, 2)]


# --- the command line -------------------------------------------------------

def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['thriftyx', *map(str, argv)])
    try:
        cli._main()
    except SystemExit as exc:
        return exc.code
    return 0


def _pipeline_files(tmp_path):
    """Two receivers, a beacon (tx 0) and a mobile (tx 1) at 6 MSPS."""
    lines, matches = [], []
    for k in range(6):
        for tx, dt in ((0, 0.0), (1, 0.5)):
            t = 10.0 + k + dt
            group = []
            for rxid in (0, 1):
                soa = t * 6e6 + rxid * 1000.0 + tx * 10.0 * (1 - 2 * rxid)
                group.append(len(lines))
                lines.append('{} {} {:.6f} 0 {:.3f} 0 0.0 100.0 1.0 0 0.0 '
                             '100.0 1.0\n'.format(rxid, tx, t, soa))
            matches.append(group)
    (tmp_path / 'data.toads').write_text(''.join(lines))
    (tmp_path / 'data.match').write_text(
        ''.join('{} {}\n'.format(*m) for m in matches))
    (tmp_path / 'pos-rx.cfg').write_text('0: 0 0\n1: 1000 0\n')
    (tmp_path / 'pos-beacon.cfg').write_text('0: 500 300\n')


def test_tdoa_to_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _pipeline_files(tmp_path)
    assert _run(monkeypatch, 'tdoa', '-s', '6M', '-o', '-') == 0
    out, err = capsys.readouterr()
    assert 'Number of TDOA estimations: 6' in err
    rows = np.loadtxt(io.StringIO(out), dtype=tdoa_est.MATRIX_DTYPE)
    assert len(rows) == 6
    # 20 samples of SDOA beyond the beacon's = 20 / 6 MHz
    np.testing.assert_allclose(rows['tdoa'], 20 / 6e6 * 1e9, atol=1e-3)


def test_failed_tdoa_run_keeps_the_previous_output(tmp_path, monkeypatch,
                                                   capsys):
    """argparse.FileType('w') truncated data.tdoa before any input was
    read; a receiver missing from pos-rx.cfg then raised KeyError."""
    monkeypatch.chdir(tmp_path)
    _pipeline_files(tmp_path)
    (tmp_path / 'pos-rx.cfg').write_text('0: 0 0\n2: 1000 0\n')
    (tmp_path / 'data.tdoa').write_text('previous result\n')
    assert _run(monkeypatch, 'tdoa', '-s', '6M') == EXIT_CONFIG
    assert 'no coordinates for receiver(s) 1' in capsys.readouterr().err
    assert (tmp_path / 'data.tdoa').read_text() == 'previous result\n'


def test_beacon_and_receiver_coordinates_must_match(tmp_path, monkeypatch,
                                                    capsys):
    monkeypatch.chdir(tmp_path)
    _pipeline_files(tmp_path)
    (tmp_path / 'pos-beacon.cfg').write_text('0: 500 300 2\n')
    assert _run(monkeypatch, 'tdoa', '-s', '6M') == EXIT_CONFIG
    assert 'different numbers of coordinates' in capsys.readouterr().err
