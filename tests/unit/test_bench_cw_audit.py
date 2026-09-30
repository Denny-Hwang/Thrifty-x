# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/bench_cw_audit.py: cross-device statistics from bench CSVs."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parents[2] / 'scripts'


def _load(name):
    spec = importlib.util.spec_from_file_location(name,
                                                  _SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load('bench_cw_level')
audit = _load('bench_cw_audit')

TONES = (161.285e6, 161.315e6, 161.322e6)
LEVELS = (-100.0, -80.0, -70.0, -60.0)


def _write(path, unit, device, rate, carrier_off, noise_off, spread=0.0,
           off_detected=0, dropped=0, setting=None):
    """A sweep per tone: RF off, four levels, RF off."""
    for t_i, tone in enumerate(TONES):
        for l_i, tx in enumerate((None,) + LEVELS + (None,)):
            jitter = spread * ((t_i + l_i) % 3 - 1)
            noise = -130.0 + noise_off
            row = {'unit': unit, 'device': device, 'rate': int(rate),
                   'tone_hz': int(tone), 'center_hz': int(161.3e6),
                   'setting': setting or ('g0' if device == 'rtlsdr'
                                          else '0/0/8'),
                   'tx_dbm': '' if tx is None else tx,
                   'noise_dbfs_hz': noise, 'near_fs_frac': 0.0,
                   'dropped': dropped if tx == -80.0 else ''}
            if tx is None:
                row['detected'] = off_detected
            else:
                carrier = tx - 30.0 + carrier_off + jitter
                row.update(carrier_dbfs=carrier, cn0_dbhz=carrier - noise,
                           detected=1, freq_error_ppm=-5.0)
            bench.append_row(str(path), row)


def test_statistics_are_recomputed_from_row_contents(tmp_path, capsys):
    # File names deliberately say nothing about the contents.
    _write(tmp_path / 'a.csv', 'RTL', 'rtlsdr', 2.4e6, 0.0, 0.0)
    _write(tmp_path / 'b.csv', 'R2-A', 'airspy_r2', 2.5e6, -21.0, -13.0,
           spread=0.3, off_detected=1)
    _write(tmp_path / 'c.csv', 'R2-A', 'airspy_r2', 10e6, -20.9, -20.0,
           dropped=65536)
    _write(tmp_path / 'd.csv', 'R2-B', 'airspy_r2', 10e6, -20.8, -19.2)
    assert audit.main([str(tmp_path / n) for n in
                       ('d.csv', 'a.csv', 'c.csv', 'b.csv')]) == 0
    out = capsys.readouterr().out
    lines = {line.split('|')[1].strip(): line for line in out.splitlines()
             if line.startswith('| R2') and '−' in line}

    def cells(name):
        return [c.split() for c in lines[name].split('|')[2:6]]

    car, noise, cn0, points = cells('R2-A 2.5M 0/0/8 − RTL 2.4M g0')
    assert float(car[0]) == pytest.approx(-21.0)
    # The jitter pattern over 3 tones x 4 levels: population sigma.
    assert float(car[1]) == pytest.approx(0.3 * (8 / 12) ** 0.5, abs=1e-3)
    assert float(noise[0]) == pytest.approx(-13.0)
    assert float(cn0[0]) == pytest.approx(-8.0)
    assert points == ['12']
    car, noise, cn0, _ = cells('R2-A 10M 0/0/8 − R2-A 2.5M 0/0/8')
    assert float(car[0]) == pytest.approx(0.1)
    assert float(noise[0]) == pytest.approx(-7.0)
    assert float(cn0[0]) == pytest.approx(7.1)
    car, noise, cn0, _ = cells('R2-B 10M 0/0/8 − R2-A 10M 0/0/8')
    assert float(car[0]) == pytest.approx(0.1)
    assert float(noise[0]) == pytest.approx(0.8)
    assert float(cn0[0]) == pytest.approx(-0.7)

    groups = [line for line in out.splitlines()
              if line.startswith('| R2-A | airspy_r2 | 2.5M | 161.315')]
    assert len(groups) == 1
    cols = [c.strip() for c in groups[0].split('|')]
    assert cols[6:8] == ['4', '2']                 # RF-ON, RF-OFF rows
    assert float(cols[8]) == pytest.approx(1.0, abs=0.02)   # slope
    assert cols[12] == '2/2'                       # RF-OFF detected
    ten = [line for line in out.splitlines()
           if line.startswith('| R2-A | airspy_r2 | 10M | 161.285')][0]
    cols = [c.strip() for c in ten.split('|')]
    assert cols[10:12] == ['1/6', '65536']         # dropped rows, samples


def test_rf_off_and_undetected_rows_stay_out_of_the_averages(tmp_path):
    _write(tmp_path / 'r.csv', 'RTL', 'rtlsdr', 2.4e6, 0.0, 0.0,
           off_detected=1)
    rows = audit.load([str(tmp_path / 'r.csv')])
    rows.append(dict(rows[1], detected=False, carrier_dbfs=0.0))
    for row in rows:
        if row['tx'] is None:
            row['carrier_dbfs'] = 99.0       # a detected RF-off "tone"
    cells = audit.levels(rows, 1e-5)
    assert sorted(cells) == sorted(LEVELS)
    assert max(c['carrier_dbfs'] for c in cells.values()) < 0


def test_gain_settings_are_never_pooled(tmp_path, capsys):
    """A sweep with two R2 settings compares each setting on its own."""
    _write(tmp_path / 'a.csv', 'RTL', 'rtlsdr', 2.4e6, 0.0, 0.0)
    _write(tmp_path / 'b.csv', 'R2-A', 'airspy_r2', 10e6, -20.9, -20.0)
    _write(tmp_path / 'b.csv', 'R2-A', 'airspy_r2', 10e6, -40.0, -25.0,
           setting='0/0/0')
    _write(tmp_path / 'c.csv', 'R2-B', 'airspy_r2', 10e6, -20.8, -19.2)
    audit.main([str(tmp_path / n) for n in ('a.csv', 'b.csv', 'c.csv')])
    out = capsys.readouterr().out
    rows = {line.split('|')[1].strip(): line.split('|')
            for line in out.splitlines()
            if line.startswith('| R2') and '−' in line}
    assert float(rows['R2-A 10M 0/0/8 − RTL 2.4M g0'][2].split()[0]) == \
        pytest.approx(-20.9)
    assert float(rows['R2-A 10M 0/0/0 − RTL 2.4M g0'][2].split()[0]) == \
        pytest.approx(-40.0)
    assert rows['R2-A 10M 0/0/8 − RTL 2.4M g0'][5].strip() == '12'
    # B has no 0/0/0: B - A compares 0/0/8 only.
    assert set(k for k in rows if k.startswith('R2-B 10M')
               and 'R2-A' in k) == {'R2-B 10M 0/0/8 − R2-A 10M 0/0/8'}
    assert float(rows['R2-B 10M 0/0/8 − R2-A 10M 0/0/8'][2].split()[0]) \
        == pytest.approx(0.1)
