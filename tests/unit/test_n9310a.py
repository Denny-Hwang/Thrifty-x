"""Tests for the optional N9310A USBTMC controller."""

import pytest

from thriftyx.instruments.n9310a import (
    N9310A,
    N9310AError,
    discover_n9310a,
)


class FakeInstrument:
    def __init__(self):
        self.timeout = None
        self.idn = 'Agilent Technologies,N9310A,CN0116A347, ,-0-2-2-0'
        self.frequency = 161_300_000.0
        self.power = -100.0
        self.output = False
        self.errors = []
        self.writes = []
        self.closed = False

    def write(self, command):
        self.writes.append(command)
        upper = command.upper()
        if upper.startswith(':FREQUENCY:CW '):
            self.frequency = float(command.split()[1])
        elif upper.startswith(':AMPLITUDE:CW '):
            self.power = float(command.split()[1])
        elif upper == ':RFOUTPUT:STATE ON':
            self.output = True
        elif upper == ':RFOUTPUT:STATE OFF':
            self.output = False

    def query(self, command):
        upper = command.upper()
        if upper == '*IDN?':
            return self.idn + '\n'
        if upper == ':FREQUENCY:CW?':
            return f'{self.frequency:.10E}\n'
        if upper == ':AMPLITUDE:CW?':
            return f'{self.power:.10E}\n'
        if upper == ':RFOUTPUT:STATE?':
            return ('1' if self.output else '0') + '\n'
        if upper == ':SYSTEM:ERROR?':
            if self.errors:
                return self.errors.pop(0) + '\n'
            return '+0,"No error"\n'
        raise AssertionError(command)

    def close(self):
        self.closed = True


class FakeRM:
    def __init__(self, resources=None, instrument=None):
        self.resources = tuple(resources or ())
        self.instrument = instrument or FakeInstrument()
        self.opened = []
        self.closed = False

    def list_resources(self, query):
        assert query == 'USB?*::INSTR'
        return self.resources

    def open_resource(self, resource):
        self.opened.append(resource)
        return self.instrument

    def close(self):
        self.closed = True


def test_discover_n9310a_uses_vid_pid_and_usb_only():
    rm = FakeRM([
        'USB0::1234::5678::OTHER::0::INSTR',
        'USB0::2391::8216::0116A347::0::INSTR',
    ])
    assert discover_n9310a(rm) == 'USB0::2391::8216::0116A347::0::INSTR'


def test_discover_refuses_ambiguous_generators():
    rm = FakeRM([
        'USB0::2391::8216::A::0::INSTR',
        'USB0::2391::8216::B::0::INSTR',
    ])
    with pytest.raises(N9310AError, match='Multiple N9310A'):
        discover_n9310a(rm)


def test_configure_cw_is_rf_off_while_reprogramming_and_verifies_readback():
    inst = FakeInstrument()
    rm = FakeRM(['USB0::2391::8216::0116A347::0::INSTR'], inst)
    sg = N9310A('auto', resource_manager=rm)
    status = sg.configure_cw(161_315_000.0, -100.0, rf_on=True)

    assert status.frequency_hz == pytest.approx(161_315_000.0)
    assert status.power_dbm == pytest.approx(-100.0)
    assert status.rf_on is True
    assert inst.writes[0] == '*CLS'
    assert inst.writes[1] == ':RFOutput:STATe OFF'
    assert inst.writes[-1] == ':RFOutput:STATe ON'
    assert ':MOD:STATe OFF' in inst.writes


def test_prepare_off_never_changes_frequency_or_power():
    inst = FakeInstrument()
    inst.output = True
    rm = FakeRM(['USB0::2391::8216::0116A347::0::INSTR'], inst)
    sg = N9310A('auto', resource_manager=rm)
    status = sg.prepare_level(161_315_000.0, None)
    assert status.rf_on is False
    assert inst.frequency == 161_300_000.0
    assert inst.power == -100.0
