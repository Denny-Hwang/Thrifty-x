# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Boolean settings work as switches on the command line.

The docs write `--packing`, `--bias-tee`, `--lna-agc` and `--mixer-agc`
as bare switches; they used to require a value and failed with
"expected one argument".
"""

import argparse

import pytest

from thriftyx import settings

KEYS = ['packing', 'bias_tee', 'lna_agc', 'mixer_agc']


def _load(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('output', nargs='?')
    config, extra = settings.load_args(parser, KEYS, argv=argv)
    return config, extra


def test_bare_flag_means_true(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config, extra = _load(['out.card', '--packing', '--bias-tee'])
    assert config.packing is True and config.bias_tee is True
    assert config.lna_agc is False
    assert extra['output'] == 'out.card'


@pytest.mark.parametrize("argv,expected", [
    (['--packing', 'false'], False),
    (['--packing', 'true'], True),
    (['--packing=on'], True),
    ([], False),
])
def test_explicit_values_still_work(tmp_path, monkeypatch, argv, expected):
    monkeypatch.chdir(tmp_path)
    config, _ = _load(argv)
    assert config.packing is expected


def test_flag_before_positional_fails_loudly(tmp_path, monkeypatch, capsys):
    """`--packing out.card` must not silently take out.card as the value."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _load(['--packing', 'out.card'])
    assert "expected true/false, got 'out.card'" in capsys.readouterr().err
