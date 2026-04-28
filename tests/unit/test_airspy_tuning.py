# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the new Airspy tuning surface: AGC, linearity / sensitivity
gain ladders, PPM correction, packing, and the libairspy version helper.

These tests do not require any libairspy or hardware: the ctypes binding
``thriftyx.hal.airspy_mini._lib`` is replaced with a recording fake.
"""

import pytest

from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_mini import AirspyMiniDevice, libairspy_version
from thriftyx.exceptions import DeviceConfigError


class _AGCFakeLib:
    """Fake _lib that records the AGC / combined-gain calls."""

    def __init__(self, *, omit=()):
        self._omit = set(omit)
        self.opened_default = False
        self.calls = []
        # libairspy_version_string returns this bytes object
        self.version_bytes = b"libairspy 1.0.10 / mock"

    # ----- open path -----
    def airspy_open(self, h):
        self.opened_default = True
        return 0

    def airspy_set_sample_type(self, h, t):
        return 0

    def airspy_board_partid_serialno_read(self, h, info):
        return -1

    def airspy_close(self, h):
        return 0

    # ----- gain ladders -----
    def _record(self, name, value):
        v = value.value if hasattr(value, 'value') else value
        self.calls.append((name, int(v)))
        return 0

    def airspy_set_lna_gain(self, h, v):     return self._record('lna', v)
    def airspy_set_mixer_gain(self, h, v):   return self._record('mixer', v)
    def airspy_set_vga_gain(self, h, v):     return self._record('vga', v)
    def airspy_set_rf_bias(self, h, v):      return self._record('bias', v)
    def airspy_set_lna_agc(self, h, v):      return self._record('lna_agc', v)
    def airspy_set_mixer_agc(self, h, v):    return self._record('mixer_agc', v)
    def airspy_set_linearity_gain(self, h, v):
        return self._record('linearity', v)
    def airspy_set_sensitivity_gain(self, h, v):
        return self._record('sensitivity', v)
    def airspy_set_packing(self, h, v):      return self._record('packing', v)

    def airspy_set_freq(self, h, v):
        return self._record('freq', v)

    def airspy_set_samplerate(self, h, v):
        return self._record('rate', v)

    def airspy_lib_version_string(self):
        return self.version_bytes


def _wire(monkeypatch, lib):
    monkeypatch.setattr(am, '_lib', lib, raising=False)
    # Drop attributes whose absence we want to simulate.
    for name in lib._omit:
        try:
            delattr(type(lib), name)
        except AttributeError:
            pass


@pytest.fixture
def agc_dev(monkeypatch):
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    dev = AirspyMiniDevice()
    dev.open()
    return dev, lib


# --------------------- AGC --------------------------------------------

def test_set_lna_agc(agc_dev):
    dev, lib = agc_dev
    dev.set_lna_agc(True)
    dev.set_lna_agc(False)
    assert ('lna_agc', 1) in lib.calls
    assert ('lna_agc', 0) in lib.calls
    dev._open = False


def test_set_mixer_agc(agc_dev):
    dev, lib = agc_dev
    dev.set_mixer_agc(True)
    assert ('mixer_agc', 1) in lib.calls
    dev._open = False


# --------------------- combined gain ladders --------------------------

def test_set_linearity_gain(agc_dev):
    dev, lib = agc_dev
    dev.set_linearity_gain(15)
    assert ('linearity', 15) in lib.calls
    dev._open = False


def test_set_sensitivity_gain(agc_dev):
    dev, lib = agc_dev
    dev.set_sensitivity_gain(7)
    assert ('sensitivity', 7) in lib.calls
    dev._open = False


def test_combined_gain_out_of_range(agc_dev):
    dev, _ = agc_dev
    with pytest.raises(DeviceConfigError):
        dev.set_linearity_gain(22)
    with pytest.raises(DeviceConfigError):
        dev.set_sensitivity_gain(-1)
    dev._open = False


# --------------------- apply_gain_mode dispatcher ---------------------

def test_apply_gain_mode_manual_sets_each_stage(agc_dev):
    dev, lib = agc_dev
    dev.apply_gain_mode('manual', lna=3, mixer=4, vga=5,
                        lna_agc=True, mixer_agc=False)
    names = [c[0] for c in lib.calls]
    assert names.count('lna') == 1
    assert names.count('mixer') == 1
    assert names.count('vga') == 1
    assert ('lna_agc', 1) in lib.calls
    assert ('mixer_agc', 0) in lib.calls
    dev._open = False


def test_apply_gain_mode_linearity_requires_combined(agc_dev):
    dev, _ = agc_dev
    with pytest.raises(DeviceConfigError):
        dev.apply_gain_mode('linearity')
    dev._open = False


def test_apply_gain_mode_invalid_name(agc_dev):
    dev, _ = agc_dev
    with pytest.raises(DeviceConfigError):
        dev.apply_gain_mode('auto', combined=0)
    dev._open = False


def test_apply_gain_mode_sensitivity_routes_to_set_sensitivity(agc_dev):
    dev, lib = agc_dev
    dev.apply_gain_mode('sensitivity', combined=10)
    assert ('sensitivity', 10) in lib.calls
    dev._open = False


# --------------------- PPM correction --------------------------------

def test_set_center_freq_zero_ppm_passes_through(monkeypatch):
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    dev = AirspyMiniDevice(ppm=0.0)
    dev.open()
    dev.set_center_freq(433_830_000)
    assert ('freq', 433_830_000) in lib.calls
    dev._open = False


def test_set_center_freq_positive_ppm_lowers_request(monkeypatch):
    """A +10 ppm correction means the crystal is fast; the LO request
    should drop by ~10 ppm so the actual LO lands on the target."""
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    dev = AirspyMiniDevice(ppm=10.0)
    dev.open()
    dev.set_center_freq(1_000_000_000)
    # 1e9 / (1 + 10e-6) ≈ 999_990_000.1
    requests = [v for n, v in lib.calls if n == 'freq']
    assert requests, "set_center_freq should have called airspy_set_freq"
    assert abs(requests[0] - 999_990_000) <= 1
    dev._open = False


def test_set_center_freq_negative_ppm_raises_request(monkeypatch):
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    dev = AirspyMiniDevice(ppm=-5.0)
    dev.open()
    dev.set_center_freq(1_000_000_000)
    requests = [v for n, v in lib.calls if n == 'freq']
    # 1e9 / (1 - 5e-6) ≈ 1_000_005_000
    assert abs(requests[0] - 1_000_005_000) <= 1
    dev._open = False


def test_ppm_property_setter(monkeypatch):
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    dev = AirspyMiniDevice()
    assert dev.ppm == 0.0
    dev.ppm = 7.5
    assert dev.ppm == 7.5


# --------------------- packing ---------------------------------------

def test_set_packing_dispatches(agc_dev):
    dev, lib = agc_dev
    dev.set_packing(True)
    assert ('packing', 1) in lib.calls
    dev._open = False


def test_set_packing_no_op_when_api_missing(monkeypatch):
    """Older libairspy lacks airspy_set_packing — no-op, no exception."""

    # Build a fake _lib that mirrors _AGCFakeLib but explicitly omits
    # airspy_set_packing so hasattr returns False.
    class _NoPackLib:
        opened_default = False

        def airspy_open(self, h):
            return 0

        def airspy_set_sample_type(self, h, t):
            return 0

        def airspy_board_partid_serialno_read(self, h, info):
            return -1

        def airspy_close(self, h):
            return 0

    lib = _NoPackLib()
    monkeypatch.setattr(am, '_lib', lib, raising=False)
    assert not hasattr(lib, 'airspy_set_packing')
    dev = AirspyMiniDevice()
    dev.open()
    # Must not raise; logs a warning instead.
    dev.set_packing(True)
    dev._open = False


# --------------------- libairspy_version helper ----------------------

def test_libairspy_version_returns_string(monkeypatch):
    lib = _AGCFakeLib()
    _wire(monkeypatch, lib)
    assert "libairspy" in libairspy_version()


def test_libairspy_version_returns_unknown_without_lib(monkeypatch):
    monkeypatch.setattr(am, '_lib', None, raising=False)
    assert libairspy_version() == "unknown"


# --------------------- bias-tee warning ------------------------------

def test_set_bias_tee_warns_when_enabled(agc_dev, capsys):
    dev, _ = agc_dev
    dev.set_bias_tee(True)
    captured = capsys.readouterr()
    assert "bias tee" in captured.err.lower()
    dev._open = False
