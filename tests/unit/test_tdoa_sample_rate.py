# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for tdoa_est._resolve_sample_rate."""

import pytest

from thriftyx.tdoa_est import _resolve_sample_rate


def _write_cfg(tmp_path, body):
    cfg = tmp_path / "detector.cfg"
    cfg.write_text(body)
    return str(cfg)


def test_cli_value_takes_precedence(tmp_path):
    cfg = _write_cfg(tmp_path, "sample_rate: 6M\ndevice_type: airspy_mini\n")
    assert _resolve_sample_rate(cli_value=2.4e6, config_path=cfg) == 2.4e6


def test_explicit_sample_rate_in_config(tmp_path):
    cfg = _write_cfg(tmp_path, "sample_rate: 6M\n")
    assert _resolve_sample_rate(cli_value=None, config_path=cfg) == 6e6


# Without a configured rate, tdoa uses the same device default that
# capture used (thriftyx.hal.profiles), so default captures and default
# TDOA runs agree.

def test_infer_from_device_type_mini(tmp_path):
    cfg = _write_cfg(tmp_path, "device_type: airspy_mini\n")
    assert _resolve_sample_rate(cli_value=None, config_path=cfg) == 6e6


def test_infer_from_device_type_r2(tmp_path):
    cfg = _write_cfg(tmp_path, "device_type: airspy_r2\n")
    assert _resolve_sample_rate(cli_value=None, config_path=cfg) == 10e6


def test_infer_from_device_type_rtlsdr(tmp_path):
    cfg = _write_cfg(tmp_path, "device_type: rtlsdr\n")
    assert _resolve_sample_rate(cli_value=None, config_path=cfg) == 2.4e6


def test_explicit_missing_config_is_an_error(tmp_path):
    """A mistyped ``-c`` must not silently fall back to device defaults.

    Every other command fails on an unreadable explicit config; tdoa used
    to swallow it and scale every TDOA by the default rate.
    """
    missing = str(tmp_path / "does_not_exist.cfg")
    with pytest.raises(FileNotFoundError):
        _resolve_sample_rate(cli_value=None, config_path=missing)


def test_fallback_when_default_config_missing(tmp_path, monkeypatch,
                                              caplog):
    from thriftyx import settings
    monkeypatch.setattr(settings, 'DEFAULT_CONFIG_PATH',
                        str(tmp_path / "detector.cfg"))
    assert _resolve_sample_rate(cli_value=None, config_path=None) == 6e6
    assert "--sample-rate not specified" in caplog.text


def test_fallback_when_config_empty(tmp_path):
    cfg = _write_cfg(tmp_path, "# comment only\n")
    assert _resolve_sample_rate(cli_value=None, config_path=cfg) == 6e6


def test_cli_flag_accepts_metric_suffix():
    """`tdoa -s 2.4M` must parse like every other command's -s flag.

    Regression: the argparse type used to be a bare float, rejecting
    metric-suffixed values that capture/detect accept.
    """
    import argparse

    from thriftyx.setting_parsers import metric_float

    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--sample-rate', dest='sample_rate',
                        type=metric_float, default=None)
    assert parser.parse_args(['-s', '2.4M']).sample_rate == 2.4e6
    assert parser.parse_args(['-s', '2400000']).sample_rate == 2.4e6

    # And the real wiring in tdoa_est._main uses metric_float:
    import inspect

    from thriftyx import tdoa_est
    src = inspect.getsource(tdoa_est._main)
    assert 'metric_float' in src
