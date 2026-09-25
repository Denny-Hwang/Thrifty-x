# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""RTL-SDR capture through the upstream ``fastcard`` binary.

The binary is replaced by a recorder of its command line, or by a
script that handles stop signals as fastcapture's sigthread.c does, so
these run without it (or a dongle).
"""

import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time

import pytest

import thriftyx.airspy_capture as ac
from thriftyx.settings import Namespace


class _Process:
    returncode = 0

    def poll(self):
        return self.returncode


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


def test_fastcard_status_is_the_exit_status(monkeypatch):
    """A fastcard killed by a signal exits 128 + signal, as a shell
    reports it (sys.exit(-9) gave 247)."""
    class Killed(_Process):
        returncode = -signal.SIGKILL

    monkeypatch.setattr(ac.subprocess, 'Popen', lambda _call: Killed())
    monkeypatch.setattr(signal, 'signal', lambda *_a: None)
    monkeypatch.setattr(os, 'setpgrp', lambda: None)
    with pytest.raises(SystemExit) as excinfo:
        ac._capture_rtlsdr_fastcard(_config(), {'output': 'rx0.card'})
    assert excinfo.value.code == 128 + signal.SIGKILL


# --- stopping fastcard: signals and --duration -------------------------------

# Stands in for fastcard, taking stop signals as fastcapture's
# sigthread.c does: the first starts a clean stop (0.3 s here), another
# during it exits at once with 128+N.  In 'stuck' mode it never stops;
# in 'slow' mode it spends 30 s starting, before it blocks the signals.
# It writes its pid once the signals are blocked, and its events to log.
_FAKE_FASTCARD = textwrap.dedent('''\
    import os
    import signal
    import sys
    import time

    here = os.path.dirname(os.path.abspath(__file__))
    stops = {signal.SIGINT, signal.SIGTERM, signal.SIGQUIT}
    if sys.argv[1] == 'slow':
        # Still starting: the stop signals have their default action.
        open(os.path.join(here, 'starting'), 'w').close()
        time.sleep(30)
    signal.pthread_sigmask(signal.SIG_BLOCK, stops)


    def log(event):
        with open(os.path.join(here, 'log'), 'a') as stream:
            stream.write(event + '\\n')


    def name(signum):
        return signal.Signals(signum).name[3:]


    with open(os.path.join(here, 'pid.tmp'), 'w') as stream:
        stream.write(str(os.getpid()))
    os.replace(os.path.join(here, 'pid.tmp'), os.path.join(here, 'pid'))
    log('stop ' + name(signal.sigwait(stops)))
    while sys.argv[1] == 'stuck':
        log('stop ' + name(signal.sigwait(stops)))
    end = time.monotonic() + 0.3
    while time.monotonic() < end:
        again = signal.sigtimedwait(stops, max(end - time.monotonic(), 0))
        if again is not None:
            log('hard ' + name(again.si_signo))
            os._exit(128 + again.si_signo)
    log('clean')
''')

_needs_sigwait = pytest.mark.skipif(not hasattr(signal, 'sigtimedwait'),
                                    reason="the fake needs sigtimedwait")


def _fake_fastcard(tmp_path, mode='stop'):
    script = tmp_path / 'fake_fastcard.py'
    script.write_text(_FAKE_FASTCARD)
    wrapper = tmp_path / 'fastcard'
    wrapper.write_text('#!/bin/sh\nexec {} {} {} "$@"\n'.format(
        shlex.quote(sys.executable), shlex.quote(str(script)), mode))
    wrapper.chmod(0o755)
    return wrapper


def _wait_for(check, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the fake fastcard")
        time.sleep(0.05)


def _log(tmp_path):
    log = tmp_path / 'log'
    return log.read_text().splitlines() if log.exists() else []


def _run_capture(tmp_path, *extra, mode='stop', new_session=False):
    return subprocess.Popen(
        [sys.executable, '-m', 'thriftyx.cli', 'capture',
         str(tmp_path / 'rx0.card'), '--device-type', 'rtlsdr',
         '-c', os.devnull, '--fastcard', str(_fake_fastcard(tmp_path, mode)),
         *extra],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=new_session)


def _kill_fake(tmp_path):
    """SIGKILL the fake fastcard if it still runs (a failed test)."""
    try:
        pid = int((tmp_path / 'pid').read_text())
        with open('/proc/{}/cmdline'.format(pid), 'rb') as cmdline:
            if b'fake_fastcard.py' in cmdline.read():
                os.kill(pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass


def _finish(process, tmp_path, timeout=20):
    """Wait for capture; on a hang kill it.  Returns its exit status."""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        _kill_fake(tmp_path)
    return process.returncode


@_needs_sigwait
@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize('group', [False, True])
def test_a_stop_signal_stops_fastcard_once(tmp_path, sig, group):
    """Signalled alone, capture hung for good (its SIGTERM handler waited
    for fastcard inside the main thread's own wait()), and Ctrl-C left
    fastcard running.  Then a signal to the whole group -- Ctrl-C at a
    terminal, systemd's stop -- reached fastcard twice, directly and
    passed on, and a second signal is fastcard's "exit now": 130 or
    143, losing buffered card lines."""
    process = _run_capture(tmp_path, new_session=group)
    try:
        _wait_for(lambda: (tmp_path / 'pid').exists())
        if group:
            os.killpg(process.pid, sig)
        else:
            process.send_signal(sig)
    finally:
        returncode = _finish(process, tmp_path)
    assert returncode == 0
    assert _log(tmp_path) == ['stop ' + signal.Signals(sig).name[3:],
                              'clean']


@_needs_sigwait
@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGINT])
def test_a_group_stop_while_fastcard_starts(tmp_path, sig):
    """A Ctrl-C or systemd stop that ended fastcard before it set up its
    handler gave 130 or 143 for a capture that stopped when asked."""
    process = _run_capture(tmp_path, mode='slow', new_session=True)
    try:
        _wait_for(lambda: (tmp_path / 'starting').exists())
        os.killpg(process.pid, sig)
    finally:
        returncode = _finish(process, tmp_path)
    assert returncode == 0
    assert _log(tmp_path) == []


@_needs_sigwait
def test_duration_stops_fastcard(tmp_path):
    """--duration used to be ignored with the fastcard binary."""
    process = _run_capture(tmp_path, '--duration', '1')
    assert _finish(process, tmp_path) == 0
    # (A slow start can take the SIGINT before blocking it.)
    assert _log(tmp_path) in (['stop INT', 'clean'], [])


@_needs_sigwait
def test_duration_shorter_than_fastcard_startup(tmp_path):
    """SIGINT reached fastcard before it set up its handler and killed
    it: exit 130 for a capture that stopped when asked."""
    assert _finish(_run_capture(tmp_path, '--duration', '0'), tmp_path) == 0


@_needs_sigwait
@pytest.mark.parametrize('first', ['signal', 'duration'])
def test_a_second_signal_kills_a_stuck_fastcard(tmp_path, first):
    """Once a stop request had been passed on, capture ignored every
    further Ctrl-C while fastcard did not stop."""
    extra = ('--duration', '2') if first == 'duration' else ()
    process = _run_capture(tmp_path, *extra, mode='stuck')
    try:
        _wait_for(lambda: (tmp_path / 'pid').exists())
        if first == 'signal':
            process.send_signal(signal.SIGTERM)
        _wait_for(lambda: _log(tmp_path))       # it was asked to stop
        process.send_signal(signal.SIGINT)
    finally:
        returncode = _finish(process, tmp_path)
    assert returncode == 128 + signal.SIGKILL
    assert len(_log(tmp_path)) == 1


# Runs the fastcard capture with a Popen that takes a SIGTERM just
# before fastcard starts (and returns once it runs).
_SIGNAL_WHILE_STARTING = textwrap.dedent('''\
    import ast
    import os
    import signal
    import subprocess
    import sys
    import time

    from thriftyx import airspy_capture
    from thriftyx.settings import Namespace

    fastcard, output, pid, config = sys.argv[1:]
    real_popen = subprocess.Popen


    def popen(call):
        os.kill(os.getpid(), signal.SIGTERM)
        process = real_popen(call)
        while not os.path.exists(pid):
            time.sleep(0.05)
        return process


    airspy_capture.subprocess.Popen = popen
    airspy_capture._capture_rtlsdr_fastcard(
        Namespace(ast.literal_eval(config)),
        {'fastcard': fastcard, 'output': output})
''')


@_needs_sigwait
def test_a_signal_while_fastcard_starts_stops_it(tmp_path):
    """Before the handlers went in ahead of Popen, a SIGTERM during it
    killed capture and left fastcard running."""
    process = subprocess.Popen(
        [sys.executable, '-c', _SIGNAL_WHILE_STARTING,
         str(_fake_fastcard(tmp_path)), str(tmp_path / 'rx0.card'),
         str(tmp_path / 'pid'), repr(dict(_config()))],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    assert _finish(process, tmp_path) == 0
    assert _log(tmp_path) == ['stop TERM', 'clean']


