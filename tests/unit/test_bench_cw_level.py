# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/bench_cw_level.py: CW level figures, receivers, report."""

import argparse
import csv
import importlib.util
import math
import os
import stat
import sys
from pathlib import Path

import numpy as np
import pytest

from thriftyx.block_data import complex_to_raw
from tests.mocks.scripted_device import ScriptedSDRDevice
from thriftyx.hal.profiles import AIRSPY_R2

_SPEC = importlib.util.spec_from_file_location(
    'bench_cw_level',
    Path(__file__).parents[2] / 'scripts' / 'bench_cw_level.py')
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def _tone(rate, seconds, amp, offset, noise_n0, seed=1):
    """Complex tone of amplitude *amp* plus white noise of density N0."""
    n = int(rate * seconds)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / rate
    sigma = math.sqrt(noise_n0 * rate / 2)
    noise = sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (amp * np.exp(2j * np.pi * offset * t) + noise).astype(
        np.complex64)


@pytest.mark.parametrize('rate', [2.4e6, 2.5e6, 10e6])
def test_figures_recover_tone_and_noise(rate):
    amp, n0 = 10 ** (-40 / 20), 10 ** (-120 / 10)      # -40 dBFS, -120/Hz
    acc = bench.Accumulator(bench.fft_size(rate))
    acc.add(_tone(rate, 0.5, amp, 15_037.0, n0))
    fig = bench.figures(acc, rate, 15e3, center_hz=161.3e6)
    assert fig['detected'] == 1
    assert fig['carrier_dbfs'] == pytest.approx(-40, abs=0.1)
    assert fig['noise_dbfs_hz'] == pytest.approx(-120, abs=0.1)
    assert fig['cn0_dbhz'] == pytest.approx(80, abs=0.15)
    assert fig['tone_offset_hz'] == pytest.approx(15_037, abs=15)
    assert fig['near_fs_frac'] == 0


def test_rbw_is_about_150_hz_at_every_rate():
    for rate in (2.4e6, 2.5e6, 6e6, 10e6):
        assert 70 < rate / bench.fft_size(rate) <= 150


def test_noise_only_is_not_detected():
    rate = 2.4e6
    acc = bench.Accumulator(bench.fft_size(rate))
    acc.add(_tone(rate, 0.5, 0.0, 15e3, 1e-12))
    fig = bench.figures(acc, rate, 15e3)
    assert fig['detected'] == 0
    assert math.isnan(fig['carrier_dbfs'])
    assert fig['noise_dbfs_hz'] == pytest.approx(-120, abs=0.1)


def test_clipping_is_counted():
    acc = bench.Accumulator(256)
    acc.add(np.full(1024, 0.99 + 0j, dtype=np.complex64))
    assert acc.near_fs == 1024


def test_rtl_gain_argument_is_manual():
    # rtl_sdr -g 0 means AGC; 0.1 snaps to manual 0 dB.
    assert bench.RtlSdr.gain_argument(0) == '0.1'
    assert bench.RtlSdr.gain_argument(29.7) == '29.7'


def test_parse_levels_and_stages():
    assert bench.parse_levels('off,-110:-100:5,-55') == \
        [None, -110.0, -105.0, -100.0, -55.0]
    with pytest.raises(argparse.ArgumentTypeError):
        bench.parse_levels('-60:-110:5')
    stages = bench.parse_stages('0/0/8, 14/15/15')
    assert [s['setting'] for s in stages] == ['0/0/8', '14/15/15']
    with pytest.raises(argparse.ArgumentTypeError):
        bench.parse_stages('15/0/0')


def _args(tmp_path, **kw):
    args = bench.build_parser().parse_args(
        ['measure', '--unit', kw.pop('unit', 'X'), '--device',
         kw.pop('device', 'airspy_r2'), '--out', str(tmp_path / 'r.csv'),
         '--seconds', '0.2', '--settle', '0.01'] + kw.pop('extra', []))
    bench._defaults(args)
    return args


def test_airspy_capture_skips_dropped_chunks(tmp_path, monkeypatch):
    rate = 10e6
    z = _tone(rate, 0.3, 10 ** (-30 / 20), 15e3, 1e-13)
    device = ScriptedSDRDevice(stream=complex_to_raw(z, bit_depth=12),
                               profile=AIRSPY_R2,
                               registers={0x0C: 0x48})
    real_read = device.read_sync
    calls = {'n': 0}

    def read_with_drop(n):
        calls['n'] += 1
        if calls['n'] == 3:               # third chunk "loses" samples
            device.dropped_samples += 10
        return real_read(n)
    device.read_sync = read_with_drop
    monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                        lambda *_a, **_k: device)
    args = _args(tmp_path, extra=['--stages', '0/0/8', '--tx-dbm', '-80'])
    assert bench.cmd_measure(args) == 0
    row = next(csv.DictReader(open(args.out)))
    assert row['setting'] == '0/0/8' and float(row['tx_dbm']) == -80
    assert row['registers'] == '0x0C=0x48'
    assert int(row['dropped']) == 10
    assert float(row['carrier_dbfs']) == pytest.approx(-30, abs=0.2)
    assert device.applied_kwargs['vga'] == 8
    assert device.applied_kwargs['lna_agc'] is False


def _fake_rtl_sdr(tmp_path, z, gain_line='Tuner gain set to 0.00 dB.'):
    data = tmp_path / 'iq.u8'
    complex_to_raw(z, bit_depth=8).tofile(data)
    script = tmp_path / 'rtl_sdr'
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stderr.write({gain_line!r} + '\\n')\n"
        "sys.stderr.write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.buffer.write(open({str(data)!r}, 'rb').read())\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_rtl_capture_through_rtl_sdr(tmp_path):
    rate = 2.4e6
    z = _tone(rate, 0.3, 10 ** (-25 / 20), 15e3, 1e-11)
    args = _args(tmp_path, device='rtlsdr', unit='RTL',
                 extra=['--rtl-sdr', _fake_rtl_sdr(tmp_path, z),
                        '--rtl-gains', '0'])
    assert bench.cmd_measure(args) == 0
    row = next(csv.DictReader(open(args.out)))
    assert row['setting'] == 'g0' and row['unit'] == 'RTL'
    assert 'Tuner gain set to 0.00 dB.' in row['notes']
    # 8-bit quantisation adds noise but the tone level holds.
    assert float(row['carrier_dbfs']) == pytest.approx(-25, abs=0.3)


def test_rtl_capture_refuses_agc(tmp_path):
    z = _tone(2.4e6, 0.3, 0.1, 15e3, 1e-11)
    args = _args(tmp_path, device='rtlsdr',
                 extra=['--rtl-sdr', _fake_rtl_sdr(
                     tmp_path, z, 'Tuner gain set to automatic.')])
    with pytest.raises(RuntimeError, match='AGC'):
        bench.cmd_measure(args)


def test_sweep_refuses_overdrive(tmp_path):
    args = bench.build_parser().parse_args(
        ['sweep', '--unit', 'X', '--device', 'airspy_r2', '--levels', '-10',
         '--amp-gain', '20', '--out', str(tmp_path / 'r.csv')])
    bench._defaults(args)
    with pytest.raises(SystemExit, match='into the receiver'):
        bench.cmd_sweep(args)


def _row(unit, setting, tx, cn0, carrier, noise=-120.0, clip=0.0):
    return {'unit': unit, 'setting': setting, 'tx_dbm': tx,
            'cn0_dbhz': cn0, 'carrier_dbfs': carrier,
            'noise_dbfs_hz': noise, 'near_fs_frac': clip,
            'detected': not math.isnan(cn0)}


def test_report_deltas_skip_clipped_levels():
    nan = float('nan')
    rows = [_row('RTL', 'g0', nan, nan, nan),
            _row('RTL', 'g0', -90, 60.0, -50.0),
            _row('RTL', 'g0', -80, 70.0, -40.0),
            _row('RTL', 'g0', -70, 80.0, -30.0, clip=1e-3),
            _row('R2-A', '0/0/10', -90, 60.5, -52.0),
            _row('R2-A', '0/0/10', -80, 70.3, -42.0),
            _row('R2-A', '0/0/10', -70, 80.2, -32.0)]
    table = bench.summarize(rows)
    d = bench.deltas(table, ('RTL', 'g0'))
    assert d[('R2-A', '0/0/10')]['levels'] == [-90, -80]
    assert d[('R2-A', '0/0/10')]['d_cn0'] == pytest.approx(0.4)
    assert d[('R2-A', '0/0/10')]['d_carrier'] == pytest.approx(-2.0)
    assert bench.slope(table[('R2-A', '0/0/10')]) == pytest.approx(1.0)


def test_report_command(tmp_path, capsys):
    path = tmp_path / 'r.csv'
    for unit, setting, tx, c in (('RTL', 'g0', '', ''),
                                 ('RTL', 'g0', -80.0, -40.0),
                                 ('R2-A', '0/0/8', -80.0, -45.0)):
        bench.append_row(str(path), {
            'unit': unit, 'setting': setting, 'tx_dbm': tx,
            'carrier_dbfs': c, 'noise_dbfs_hz': -110.0,
            'cn0_dbhz': (c + 110.0) if c != '' else '',
            'detected': 1 if c != '' else 0, 'near_fs_frac': 0.0})
    plot = tmp_path / 'p.png'
    assert bench.main(['report', str(path), '--plot', str(plot)]) == 0
    out = capsys.readouterr().out
    assert '| R2-A:0/0/8 | -5.00 |' in out
    assert os.path.getsize(plot) > 0
