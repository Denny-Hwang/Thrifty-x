# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for Airspy device selection (index/serial) and dynamic
sample-rate enumeration in :mod:`thriftyx.hal.airspy_mini`.

These tests do not require any libairspy or hardware: the ctypes
binding (``thriftyx.hal.airspy_mini._lib``) is mocked.
"""

import ctypes

import pytest

from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import (AirspyMiniDevice, parse_airspy_serial,
                                       _rate_is_supported)
from thriftyx.exceptions import DeviceNotFoundError, DeviceConfigError


class _FakeLib:
    """Minimal libairspy stand-in.  Records the serial used to open."""

    AIRSPY_SUCCESS = 0

    def __init__(self, serials=(), open_returns_zero=True,
                 sample_rates=None, sample_type_returns_zero=True):
        self._serials = list(serials)
        self._open_ok = open_returns_zero
        self._sample_rates = sample_rates  # None => API absent
        self._sample_type_ok = sample_type_returns_zero
        self.opened_with_serial = None
        self.opened_default = False
        self.set_sample_type_called = False
        self.closed = False
        self.last_set_rate = None

    # airspy_open / airspy_open_sn
    def airspy_open(self, handle_ptr):
        self.opened_default = True
        return 0 if self._open_ok else -1

    def airspy_open_sn(self, handle_ptr, serial):
        # ctypes converts the int we pass to c_uint64 before invoking
        # the function pointer; with a Python-callable fake we get the
        # raw c_uint64 wrapper here.
        sn = serial.value if hasattr(serial, 'value') else int(serial)
        self.opened_with_serial = int(sn)
        if int(sn) in self._serials:
            return 0 if self._open_ok else -1
        return -2  # not found

    # airspy_list_devices: first call (NULL, 0) returns count;
    # subsequent call fills the array.
    def airspy_list_devices(self, buf, count):
        if buf is None or count == 0:
            return len(self._serials)
        n = min(count, len(self._serials))
        for i in range(n):
            buf[i] = ctypes.c_uint64(self._serials[i]).value
        return n

    # airspy_get_samplerates: NULL/0 returns rate count via out-param.
    def airspy_get_samplerates(self, handle, count_or_buf, count):
        if self._sample_rates is None:
            return -1
        # The HAL passes the request as either ``byref(c_uint32)`` (count
        # query) or ``(c_uint32 * N)()`` (rate buffer).  ctypes wraps both
        # in some way — handle both shapes generically.
        c = int(count.value) if hasattr(count, 'value') else int(count)
        if c == 0:
            # Count query: write to first slot of the buffer.
            try:
                count_or_buf._obj.value = len(self._sample_rates)
            except AttributeError:
                count_or_buf[0] = len(self._sample_rates)
            return 0
        n = min(c, len(self._sample_rates))
        for i in range(n):
            count_or_buf[i] = self._sample_rates[i]
        return 0

    # Functions invoked during open() that we don't care to model.
    def airspy_set_sample_type(self, handle, fmt):
        self.set_sample_type_called = True
        return 0 if self._sample_type_ok else -1

    def airspy_close(self, handle):
        self.closed = True
        return 0

    def airspy_board_partid_serialno_read(self, handle, info_ptr):
        return -1  # signal "no serial readable" — open() should still succeed

    def airspy_set_samplerate(self, handle, rate):
        self.last_set_rate = int(rate.value if hasattr(rate, 'value')
                                 else rate)
        return 0


@pytest.fixture
def fake_lib(monkeypatch):
    """Replace the module-level ``_lib`` with a fake."""
    fl = _FakeLib(serials=[0x1111111111111111, 0x2222222222222222])
    monkeypatch.setattr(am, '_lib', fl, raising=False)
    return fl


def test_parse_airspy_serial_hex():
    assert parse_airspy_serial('0xDEADBEEFCAFEBABE') == 0xDEADBEEFCAFEBABE
    assert parse_airspy_serial('deadbeefcafebabe') == 0xDEADBEEFCAFEBABE


def test_parse_airspy_serial_decimal_short():
    # Plain decimal, not hex
    assert parse_airspy_serial('123456') == 123456


def test_parse_airspy_serial_int_passthrough():
    assert parse_airspy_serial(42) == 42


def test_parse_airspy_serial_none():
    with pytest.raises(ValueError):
        parse_airspy_serial(None)


def test_rate_supported_with_tolerance():
    assert _rate_is_supported(6_000_000, (3_000_000, 6_000_000))
    assert _rate_is_supported(5_999_950, (3_000_000, 6_000_000))
    assert not _rate_is_supported(5_990_000, (3_000_000, 6_000_000))


def test_open_default_uses_airspy_open(fake_lib):
    dev = AirspyMiniDevice()
    dev.open()
    assert fake_lib.opened_default is True
    assert fake_lib.opened_with_serial is None
    dev._open = False  # avoid close-time ctypes call


def test_open_by_serial_calls_airspy_open_sn(fake_lib):
    dev = AirspyMiniDevice(serial='0x2222222222222222')
    dev.open()
    assert fake_lib.opened_with_serial == 0x2222222222222222
    assert fake_lib.opened_default is False
    dev._open = False


def test_open_by_index_resolves_serial(fake_lib):
    dev = AirspyMiniDevice(device_index=1)
    dev.open()
    assert fake_lib.opened_with_serial == 0x2222222222222222
    dev._open = False


def test_open_by_unknown_serial_raises(fake_lib):
    dev = AirspyMiniDevice(serial='0x3333333333333333')
    with pytest.raises(DeviceNotFoundError):
        dev.open()


def test_open_by_index_out_of_range_raises(fake_lib):
    dev = AirspyMiniDevice(device_index=99)
    with pytest.raises(DeviceNotFoundError):
        dev.open()


def test_open_raises_when_set_sample_type_fails(monkeypatch):
    """A failed airspy_set_sample_type must fail-fast, not warn-and-continue.

    Regression for the silent-FLOAT32-fallback bug: if libairspy refuses
    INT16_IQ the device stays in its default FLOAT32 mode and the int16
    capture path silently misreads the byte stream, corrupting the
    spectrum. open() must raise DeviceConfigError and release the handle
    rather than hand back a device that would capture a ruined session.
    """
    fl = _FakeLib(serials=[0x1], sample_type_returns_zero=False)
    monkeypatch.setattr(am, '_lib', fl, raising=False)

    dev = AirspyMiniDevice()
    with pytest.raises(DeviceConfigError, match="set_sample_type"):
        dev.open()

    # The handle must have been closed and the device marked not-open,
    # so a later close()/__del__ does not double-free or leak it.
    assert fl.closed is True
    assert dev._open is False


def test_dynamic_sample_rates_query(monkeypatch):
    fl = _FakeLib(serials=[0x1], sample_rates=(3_000_000, 6_000_000,
                                                10_000_000))
    monkeypatch.setattr(am, '_lib', fl, raising=False)
    dev = AirspyMiniDevice(serial=0x1)
    dev.open()
    assert dev._supported_sample_rates == (3_000_000, 6_000_000, 10_000_000)
    # set_sample_rate should now accept the device-reported rate.
    dev.set_sample_rate(10_000_000)
    assert fl.last_set_rate == 10_000_000
    dev._open = False


def test_dynamic_sample_rates_falls_back_when_api_missing(monkeypatch):
    fl = _FakeLib(serials=[0x1], sample_rates=None)
    monkeypatch.setattr(am, '_lib', fl, raising=False)
    dev = AirspyMiniDevice(serial=0x1)
    dev.open()
    # Falls back to the class-level default
    assert dev._supported_sample_rates == AirspyMiniDevice._SUPPORTED_SAMPLE_RATES
    dev._open = False


def test_set_sample_rate_rejects_unsupported(fake_lib):
    fake_lib._sample_rates = (3_000_000, 6_000_000)
    dev = AirspyMiniDevice(serial=0x1111111111111111)
    dev.open()
    with pytest.raises(DeviceConfigError):
        dev.set_sample_rate(2_400_000)
    dev._open = False
