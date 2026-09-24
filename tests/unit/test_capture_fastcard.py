# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""RTL-SDR capture through the upstream ``fastcard`` binary.

The binary is replaced by a recorder of its command line, or by a
script that waits for a stop signal, so these run without it (or a
dongle).
"""

import os
import signal
import subprocess
import sys
import time

import pytest

import thriftyx.airspy_capture as ac
from thriftyx.settings import Namespace


class _Process:
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
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


# --- stopping fastcard: signals and --duration -------------------------------

# Stands in for fastcard: records its pid, then runs until SIGINT or
# SIGTERM, which it records, exiting 0 as fastcard does.
_FAKE_FASTCARD = """#!/bin/sh
dir=$(dirname "$0")
trap 'echo INT > "$dir/stopped"; exit 0' INT
trap 'echo TERM > "$dir/stopped"; exit 0' TERM
echo $$ > "$dir/pid"
while :; do sleep 0.1; done
"""


def _wait_for(path, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not path.exists() or not path.read_text().strip():
        if time.monotonic() > deadline:
            raise AssertionError("{} never appeared".format(path))
        time.sleep(0.05)


def _run_capture(tmp_path, *extra):
    fake = tmp_path / 'fastcard'
    fake.write_text(_FAKE_FASTCARD)
    fake.chmod(0o755)
    return subprocess.Popen(
        [sys.executable, '-m', 'thriftyx.cli', 'capture',
         str(tmp_path / 'rx0.card'), '--device-type', 'rtlsdr',
         '-c', os.devnull, '--fastcard', str(fake), *extra],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def _finish(process, tmp_path, timeout=20):
    """Wait for capture; on a hang kill it and the fake fastcard."""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        pid = tmp_path / 'pid'
        if pid.exists() and not (tmp_path / 'stopped').exists():
            try:
                os.kill(int(pid.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
    return process.returncode


@pytest.mark.parametrize('sig, name', [(signal.SIGTERM, 'TERM'),
                                       (signal.SIGINT, 'INT')])
def test_stop_signal_is_passed_to_fastcard(tmp_path, sig, name):
    """SIGTERM hung capture for good -- its handler waited for fastcard
    inside the main thread's own wait(), so `systemctl stop` timed out
    and fastcard stayed a zombie -- and Ctrl-C returned without
    fastcard, which kept running."""
    process = _run_capture(tmp_path)
    try:
        _wait_for(tmp_path / 'pid')
        process.send_signal(sig)
    finally:
        returncode = _finish(process, tmp_path)
    assert returncode == 0
    assert (tmp_path / 'stopped').read_text().strip() == name


def test_duration_stops_fastcard(tmp_path):
    """--duration used to be ignored with the fastcard binary."""
    process = _run_capture(tmp_path, '--duration', '1')
    assert _finish(process, tmp_path) == 0
    assert (tmp_path / 'stopped').read_text().strip() == 'INT'


def test_fastcard_status_is_the_exit_status(monkeypatch):
    """A fastcard killed by a signal exits 128 + signal, as a shell
    reports it (sys.exit(-9) gave 247)."""
    class Killed(_Process):
        returncode = -signal.SIGKILL

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(ac.subprocess, 'Popen', lambda _call: Killed())
    monkeypatch.setattr(signal, 'signal', lambda *_a: None)
    monkeypatch.setattr(os, 'setpgrp', lambda: None)
    with pytest.raises(SystemExit) as excinfo:
        ac._capture_rtlsdr_fastcard(_config(), {'output': 'rx0.card'})
    assert excinfo.value.code == 128 + signal.SIGKILL
