# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""RTL-SDR capture through the upstream ``fastcard`` binary.

The binary is replaced by a recorder of its command line, so these run
without it (or a dongle).
"""

import os
import signal
import sys

import pytest

import thriftyx.airspy_capture as ac
from thriftyx.settings import Namespace


class _Process:
    returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0


@pytest.fixture
def fastcard(monkeypatch):
    """Record fastcard command lines; keep this process's group and
    signal handlers as they are."""
    calls = []

    def popen(call):
        calls.append(call)
        return _Process()

    monkeypatch.setattr(ac.subprocess, 'Popen', popen)
    monkeypatch.setattr(signal, 'signal', lambda *_a: None)
    monkeypatch.setattr(os, 'setpgrp', lambda: None)
    return calls


def _config():
    return Namespace({
        'device_type': 'rtlsdr', 'sample_rate': 2_400_000,
        'tuner_freq': 433_830_000, 'tuner_gain': 0.0,
        'block_size': 16384, 'block_history': 4920, 'capture_skip': 1,
        'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 15.0, 0.0)})


def _output_option(call):
    return call[call.index('-o') + 1] if '-o' in call else None


@pytest.mark.parametrize('output, tty, expected', [
    ('rx0.card', True, 'rx0.card'),
    # '-' and a piped stdout used to pass no -o: fastcard then writes no
    # card data, and its status text went down the pipe instead.
    ('-', True, '-'),
    (None, False, '-'),
    (None, True, None),           # display only, as fastcard's default
])
def test_card_data_goes_where_the_python_capture_sends_it(
        fastcard, monkeypatch, output, tty, expected):
    monkeypatch.setattr(ac, '_stdout_is_tty', lambda: tty)
    ac._capture_rtlsdr_fastcard(_config(), {'output': output})
    assert _output_option(fastcard[0]) == expected


def test_session_leader_does_not_regroup(fastcard, monkeypatch):
    """setpgid() on a session leader (a systemd service, setsid, an ssh
    command) fails with EPERM, which stopped capture before fastcard
    ran."""
    def setpgrp():
        raise PermissionError(1, 'Operation not permitted')

    monkeypatch.setattr(os, 'setpgrp', setpgrp)
    monkeypatch.setattr(os, 'getsid', lambda _pid: os.getpid())
    ac._capture_rtlsdr_fastcard(_config(), {'output': 'rx0.card'})
    assert len(fastcard) == 1


def test_input_is_read_by_the_python_capture(fastcard, monkeypatch,
                                             tmp_path):
    """fastcard opens the dongle itself; --input used to be ignored."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ac.shutil, 'which', lambda path: path)
    monkeypatch.setattr(ac.config_validator, 'validate_config',
                        lambda _c: [])
    read = []
    monkeypatch.setattr(ac, '_capture_rtlsdr',
                        lambda _c, extra, _o: read.append(extra['input']))
    monkeypatch.setattr(sys, 'argv', ['capture'])
    ac.capture_cli(['rx0.card', '--device-type', 'rtlsdr', '-c', os.devnull,
                    '--input', 'recording.u8'])
    assert read == ['recording.u8']
    assert fastcard == []

    ac.capture_cli(['rx0.card', '--device-type', 'rtlsdr', '-c', os.devnull])
    assert len(fastcard) == 1
