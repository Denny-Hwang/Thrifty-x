# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Operator mistakes end in one clear message, not a traceback.

Each case here used to crash with an unrelated exception (argparse
format error, ZeroDivisionError, "negative dimensions", ...) or, worse,
to carry on with a silently wrong setting.
"""

import io
import logging
import subprocess
import sys
import types

import numpy as np
import pytest

from thriftyx import block_data, cli, pos_est, settings, tdoa_est
from thriftyx import detect, template_extract
from thriftyx.exceptions import (EXIT_CONFIG, ConfigValidationError,
                                 DetectionError, FileFormatError,
                                 TemplateError)
from thriftyx.soa_estimator import SoaEstimator
from thriftyx.template_generate import generate


def _run_cli(args, cwd):
    return subprocess.run([sys.executable, '-m', 'thriftyx.cli', *args],
                          cwd=cwd, capture_output=True, text=True,
                          timeout=120)


# --- every command's --help works ------------------------------------------

_NEEDS_MATPLOTLIB = frozenset({'analyze_beacon', 'analyze_detect',
                               'analyze_tdoa', 'analyze_toads', 'scope'})


@pytest.mark.parametrize('command', sorted(cli.MODULES))
def test_help_exits_zero(command, tmp_path):
    """Regression: a literal '%' in a setting description ("saves ~33%
    bandwidth") crashed argparse's help formatter for capture and scope."""
    if command in _NEEDS_MATPLOTLIB:
        pytest.importorskip('matplotlib')
    result = _run_cli([command, '--help'], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'usage' in result.stdout.lower()


# --- bad setting values: one line, sysexits EX_CONFIG ----------------------

@pytest.mark.parametrize('flag, value, key', [
    ('--sample-rate', 'six', 'sample_rate'),
    ('--device-type', 'airspy_mini2', 'device_type'),
    ('--soa-interpolation', 'cubic', 'soa_interpolation'),
    ('--bit-depth', '16', 'bit_depth'),
    ('--chip-rate', '0', 'chip_rate'),
    ('--chip-rate', '0.999707m', 'chip_rate'),
])
def test_bad_setting_exits_78_without_traceback(tmp_path, flag, value, key):
    card = tmp_path / 'empty.card'
    card.write_text('')
    result = _run_cli(['detect', str(card), '{}={}'.format(flag, value)],
                      cwd=tmp_path)
    assert result.returncode == EXIT_CONFIG, result.stderr
    assert 'Traceback' not in result.stderr
    assert key in result.stderr


@pytest.mark.parametrize('line', [
    'airspy_serial: 0xABCDEF012345678G',
    'carrier_window: 50-60k',           # bins, not kHz: beyond the FFT
    'chip_rate: 0.999707m',             # milli
])
def test_bad_capture_setting_exits_78_before_any_output(tmp_path, line):
    """These used to exit 1 (a traceback, or a device error), so the
    systemd unit restarted forever -- each run leaving a header-only
    card, for the window."""
    (tmp_path / 'capture.cfg').write_text(
        'device_type: airspy_mini\n' + line + '\n')
    result = _run_cli(['capture', 'rx0_%Y%m%dT%H%M%S.card', '--rotate',
                       '3600', '-c', 'capture.cfg'], cwd=tmp_path)
    assert result.returncode == EXIT_CONFIG, result.stderr
    assert 'Traceback' not in result.stderr
    assert line.split(':')[0] in result.stderr
    assert [p.name for p in tmp_path.iterdir()] == ['capture.cfg']


def test_load_rejects_unknown_choices():
    for key, value in [('freq_shift_method', 'integr'),
                       ('device_type', 'hackrf')]:
        with pytest.raises(ConfigValidationError, match=key):
            settings.load({key: value}, None)


def test_os_error_is_one_line(monkeypatch, capsys):
    def boom():
        raise IsADirectoryError(21, 'Is a directory', 'template.npy')

    monkeypatch.setitem(cli.MODULES, 'boom', 'fake_boom')
    monkeypatch.setattr(cli.importlib, 'import_module',
                        lambda name: types.SimpleNamespace(_main=boom))
    monkeypatch.setattr(sys, 'argv', ['thriftyx', 'boom'])
    with pytest.raises(SystemExit) as excinfo:
        cli._main()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.strip() == "thriftyx boom: template.npy: Is a directory"


# --- .card input ------------------------------------------------------------

def test_binary_input_is_a_format_error():
    stream = io.BytesIO(b'\x93NUMPY\x01\x00\xff\xfe binary')
    with pytest.raises(FileFormatError, match='--raw'):
        list(block_data.card_reader(stream))


def test_crlf_and_blank_lines_are_tolerated():
    block = (np.arange(8) + 1j * np.arange(8)).astype(np.complex64) / 128
    out = io.StringIO()
    block_data.card_writer(out, 1.5, 3, block)
    line = out.getvalue().rstrip('\n')
    text = '\r\n' + line + '\r\n\r\n' + '   \n'
    blocks = list(block_data.card_reader(io.StringIO(text)))
    assert len(blocks) == 1
    timestamp, idx, data = blocks[0]
    assert (timestamp, idx, len(data)) == (1.5, 3, 8)


def test_malformed_line_is_a_format_error():
    with pytest.raises(FileFormatError, match='Malformed'):
        list(block_data.card_reader(io.StringIO('garbage\n')))


def test_header_with_unsupported_bit_depth_is_rejected():
    config = settings.Namespace(settings.load(None, None))
    with pytest.raises(FileFormatError, match='bit_depth'):
        settings.apply_card_header(config, {'bit_depth': '16'})
    text = '#v2 bit_depth=16 sample_rate=6000000\n'
    with pytest.raises(FileFormatError, match='bit_depth'):
        list(block_data.card_reader(io.StringIO(text)))


def test_header_block_history_zero_is_adopted():
    config = settings.Namespace(settings.load(None, None))
    new = settings.apply_card_header(
        config, {'sample_rate': '6000000', 'block_size': '32768',
                 'block_history': '0'})
    assert new.block_history == 0
    assert new.block_size == 32768


# --- template / detection mismatches ---------------------------------------

def test_template_longer_than_history_is_a_template_error():
    template = np.ones(1000)
    with pytest.raises(TemplateError, match='block_history'):
        SoaEstimator(template, (0, 0, 0), block_len=4096, history_len=500)


def test_template_longer_than_block_is_a_template_error():
    """Used to fail with numpy's bare 'negative dimensions' ValueError."""
    template = np.ones(5000)
    with pytest.raises(TemplateError, match='block_size'):
        SoaEstimator(template, (0, 0, 0), block_len=4096, history_len=8000)


def test_template_error_is_still_a_value_error():
    with pytest.raises(ValueError):
        SoaEstimator(np.ones(1000), (0, 0, 0), block_len=4096,
                     history_len=500)


def _template_file(tmp_path, bits, idx, family, rate):
    path = tmp_path / 'template.npy'
    np.save(path, generate(bits, idx, rate / 0.999707e6, family))
    return str(path)


def test_load_template_names_its_code(tmp_path, caplog):
    path = _template_file(tmp_path, 10, 3, 'legacy', 6e6)
    with caplog.at_level(logging.INFO):
        template = detect.load_template(path, sample_rate=6e6)
    assert len(template) == 6139
    assert 'the 10-bit legacy (not Gold) code 3' in caplog.text
    assert 'WARNING' not in caplog.text


@pytest.mark.parametrize('bits, made_at, used_at', [
    (11, 2.4e6, 6e6),     # the shipped template on an Airspy
    # Rates 2x apart: an n-bit template at R is as long as an
    # (n+1)-bit one at R/2, so its length alone looks valid.
    (10, 6e6, 3e6), (11, 3e6, 6e6), (9, 10e6, 2.5e6), (11, 2.5e6, 10e6),
    (10, 3e6, 6e6)])
def test_load_template_warns_on_rate_mismatch(tmp_path, caplog, bits,
                                              made_at, used_at):
    path = _template_file(tmp_path, bits, 5, 'gold', made_at)
    with caplog.at_level(logging.WARNING):
        detect.load_template(path, sample_rate=used_at)
    assert 'different sample rate' in caplog.text


def test_detect_warns_when_carriers_never_correlate(caplog):
    with caplog.at_level(logging.WARNING):
        detect._check_yield(50, 0, 'template.npy', 'rx0.card')
        detect._check_yield(50, 1, 'template.npy', 'rx0.card')
        detect._check_yield(5, 0, 'template.npy', 'rx0.card')
    assert caplog.text.count('none correlated') == 1
    assert '--identify rx0.card' in caplog.text


def test_load_template_rejects_non_npy(tmp_path):
    path = tmp_path / 'template.npy'
    path.write_text('not an array')
    with pytest.raises(TemplateError, match='cannot load'):
        detect.load_template(str(path))


def test_template_extract_without_detection_is_a_detection_error():
    """Used to crash in np.fft.ifft(None)."""
    with pytest.raises(DetectionError, match='no block'):
        template_extract.best_detection(iter([]), 0.2)


# --- tdoa / pos -------------------------------------------------------------

def test_zero_noise_estimate_does_not_abort_tdoa():
    info = types.SimpleNamespace(energy=10.0, noise=0.0)
    assert tdoa_est._power_snr(info) == np.inf
    pair = (types.SimpleNamespace(corr_info=info),
            types.SimpleNamespace(corr_info=info))
    assert tdoa_est.estimate_model_quality(None, [pair]) == np.inf


def test_two_receiver_1d_position():
    """Regression: solve_1d returned an array coordinate that NumPy 2
    cannot pack into the structured result ('setting an array element
    with a sequence')."""
    rx_pos = {0: np.array([0.0]), 1: np.array([100.0])}
    tdoa = 10.0 / tdoa_est.SPEED_OF_LIGHT  # 10 m closer to rx1
    tdoas = np.array([(0, 1, tdoa, 5.0, 1.0, 0, 0)],
                     dtype=tdoa_est.TDOA_DTYPE)
    results = pos_est.solve([(7, 1.0, 2, tdoas)], rx_pos)
    assert len(results) == 1
    assert results[0]['group_id'] == 7
    assert results[0]['x'] == pytest.approx(55.0)


def test_1d_solver_rejects_wrong_receiver_count():
    rx_pos = {0: np.array([0.0])}
    tdoas = np.array([(0, 1, 0.0, 5.0, 1.0, 0, 0)],
                     dtype=tdoa_est.TDOA_DTYPE)
    with pytest.raises(pos_est.EstimationError, match='2 receivers'):
        pos_est.solve_1d(tdoas, rx_pos)
