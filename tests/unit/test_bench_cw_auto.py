"""Automation layer tests: N9310A level control + receiver sweep."""

import argparse
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'bench_cw_auto',
    Path(__file__).parents[2] / 'scripts' / 'bench_cw_auto.py')
auto = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(auto)


class FakeStatus:
    frequency_hz = 161_315_000.0
    power_dbm = -90.0
    rf_on = True


class FakeGenerator:
    instances = []

    def __init__(self, resource, timeout):
        self.resource = 'USB0::2391::8216::0116A347::0::INSTR'
        self.idn = 'Agilent Technologies,N9310A,CN0116A347'
        self.levels = []
        self.outputs = []
        FakeGenerator.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def set_rf_output(self, enabled):
        self.outputs.append(enabled)
        return enabled

    def prepare_level(self, frequency, power):
        self.levels.append((frequency, power))
        status = FakeStatus()
        status.power_dbm = -100.0 if power is None else power
        status.rf_on = power is not None
        return status


class FakeReceiver:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeBench:
    def __init__(self):
        self.rows = []
        self.receiver = FakeReceiver()

    def build_parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest='cmd', required=True)
        s = sub.add_parser('sweep')
        s.add_argument('--unit', required=True)
        s.add_argument('--device', required=True)
        s.add_argument('--levels', required=True)
        s.add_argument('--tone', type=float, default=161_315_000.0)
        s.add_argument('--seconds', type=float, default=0.01)
        s.add_argument('--out', default='x.csv')
        s.add_argument('--notes', default='')
        return p

    def _defaults(self, _args):
        pass

    @staticmethod
    def parse_levels(_text):
        return [None, -100.0, -90.0]

    @staticmethod
    def _settings(_args):
        return [{'setting': 'g0'}]

    @staticmethod
    def check_input_levels(_levels, _args):
        pass

    def open_receiver(self, _args):
        return self.receiver

    @staticmethod
    def measure_one(_receiver, _args, _setting, tx):
        return {'setting': 'g0', 'tx': tx}

    @staticmethod
    def _print_row(_row):
        pass

    def append_row(self, _path, row):
        self.rows.append(row)


def test_auto_sweep_programs_each_level_and_leaves_rf_off(monkeypatch):
    bench = FakeBench()
    FakeGenerator.instances.clear()
    monkeypatch.setattr(auto, '_load_bench_module', lambda: bench)
    monkeypatch.setattr(auto, 'N9310A', FakeGenerator)
    monkeypatch.setattr(auto.time, 'sleep', lambda _s: None)

    rc = auto.main([
        '--generator-resource', 'auto', '--generator-settle', '0',
        '--unit', 'RTL', '--device', 'rtlsdr',
        '--levels', 'off,-100,-90', '--tone', '161315000',
        '--out', 'dummy.csv',
    ])

    assert rc == 0
    sg = FakeGenerator.instances[0]
    assert sg.levels == [
        (161_315_000.0, None),
        (161_315_000.0, -100.0),
        (161_315_000.0, -90.0),
    ]
    assert sg.outputs[0] is False
    assert sg.outputs[-1] is False
    assert len(bench.rows) == 3
    assert bench.receiver.closed
