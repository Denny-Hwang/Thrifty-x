# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/soak_test.sh with a fake `thriftyx capture` and vcgencmd.

The fake capture spends FAKE_STARTUP_S with the signal dispositions it
inherited (as the real one does while importing and opening the
device), writes a #v2 header and FAKE_BLOCKS data lines, then runs
until its --duration (or FAKE_EXIT_AFTER seconds) passes or it is
stopped by SIGINT/SIGTERM, and exits 0 either way -- as the real one
does, which is why the exit status alone cannot show a cut-short soak.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'rpi' / 'soak_test.sh'

pytestmark = pytest.mark.skipif(sys.platform != 'linux',
                                reason="needs bash and /proc")

FAKE_CAPTURE = '''#!{python}
import os, signal, sys, time
# Imports and device open: no signal handler installed yet.
time.sleep(float(os.environ.get('FAKE_STARTUP_S', '0')))
card = sys.argv[2]
duration = float(sys.argv[sys.argv.index('--duration') + 1])
run_for = min(duration, float(os.environ.get('FAKE_EXIT_AFTER', duration)))
stop = []
signal.signal(signal.SIGINT, lambda *a: stop.append(1))
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
with open(card, 'w') as f:
    f.write('#v2 bit_depth=12 sample_rate=6000000 endian=little\\n')
    for i in range(int(os.environ.get('FAKE_BLOCKS', '3'))):
        f.write('{{:.6f}} {{}} AAAA\\n'.format(time.time(), i))
    f.flush()
    end = time.monotonic() + run_for
    while not stop and time.monotonic() < end:
        time.sleep(0.05)
'''

FAKE_VCGENCMD = r'''#!/bin/sh
case "$1" in
  measure_temp) echo "temp=45.0'C" ;;
  get_throttled) echo "throttled=0x0" ;;
esac
'''


@pytest.fixture
def soak(tmp_path):
    home = tmp_path / 'home'
    venv = home / '.venv' / 'bin'
    venv.mkdir(parents=True)
    (venv / 'python').write_text('#!/bin/sh\nexit 0\n')
    (venv / 'thriftyx').write_text(
        FAKE_CAPTURE.format(python=sys.executable))
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    (fakebin / 'vcgencmd').write_text(FAKE_VCGENCMD)
    for exe in (*venv.iterdir(), *fakebin.iterdir()):
        exe.chmod(0o755)
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'capture.cfg').write_text('')
    env = dict(os.environ, THRIFTYX_HOME=str(home), THRIFTYX_OUT=str(out),
               PATH='{}:{}'.format(fakebin, os.environ['PATH']),
               SAMPLE_INTERVAL_S='1', MIN_DISK_FREE_PCT='0')
    for key in ('THRIFTYX_CONFIG', 'SOAK_DURATION_S', 'SOAK_TOLERANCE_S',
                'MIN_CARD_BLOCKS', 'FAKE_EXIT_AFTER', 'FAKE_BLOCKS',
                'FAKE_STARTUP_S'):
        env.pop(key, None)

    def start(**overrides):
        return subprocess.Popen(['bash', str(SCRIPT)],
                                env={**env, **overrides},
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)

    def summary():
        (path,) = out.glob('soak/*/summary.txt')
        return path.read_text()

    return dict(start=start, summary=summary, out=out)


def _finish(process):
    out, _ = process.communicate(timeout=120)
    return process.returncode, out


def _wait_for(soak, process, pattern):
    deadline = time.monotonic() + 30
    while not list(soak['out'].glob(pattern)):
        assert time.monotonic() < deadline and process.poll() is None
        time.sleep(0.05)


def test_full_soak_passes(soak):
    code, out = _finish(soak['start'](SOAK_DURATION_S='2',
                                      SOAK_TOLERANCE_S='0'))
    summary = soak['summary']()
    assert code == 0, out
    assert 'RESULT: PASS' in summary
    assert int(summary.split('elapsed_s=')[1].split()[0]) >= 2
    # Two samples cannot show a memory trend: said, not a silent 0.
    assert 'memory growth was NOT judged' in summary
    assert 'rss_growth_pct=n/a' in summary


@pytest.mark.parametrize('sig, name', [(signal.SIGTERM, 'SIGTERM'),
                                       (signal.SIGINT, 'SIGINT')])
def test_interrupted_soak_fails(soak, sig, name):
    """Regression: a 24 h soak stopped after a few seconds (the scope
    stopped, Ctrl-C) was judged "RESULT: PASS": capture exits 0 on
    SIGINT/SIGTERM and nothing compared the run time with the soak's."""
    process = soak['start']()
    _wait_for(soak, process, 'soak/*/samples.csv')
    time.sleep(1.5)     # a couple of samples into the 24 h
    os.killpg(process.pid, sig)
    code, out = _finish(process)
    summary = soak['summary']()
    assert code == 1, out
    assert 'RESULT: FAIL' in summary
    assert 'soak interrupted by {}'.format(name) in summary
    assert 'of the 86400s soak' in summary


def test_capture_ending_early_fails(soak):
    """Capture exiting 0 long before --duration (killed on its own,
    say) is a cut-short soak, not a pass."""
    code, out = _finish(soak['start'](SOAK_DURATION_S='3600',
                                      FAKE_EXIT_AFTER='1'))
    summary = soak['summary']()
    assert code == 1, out
    assert 'capture_exit_code=0' in summary
    assert 'of the 3600s soak' in summary
    assert 'interrupted' not in summary


@pytest.mark.parametrize('min_blocks, passes', [(None, False), ('0', True)])
def test_card_without_detections(soak, min_blocks, passes):
    """A card holding only its header detected nothing all soak: FAIL,
    unless MIN_CARD_BLOCKS=0 (no transmitters on air)."""
    overrides = dict(SOAK_DURATION_S='1', SOAK_TOLERANCE_S='0',
                     FAKE_BLOCKS='0')
    if min_blocks is not None:
        overrides['MIN_CARD_BLOCKS'] = min_blocks
    code, out = _finish(soak['start'](**overrides))
    summary = soak['summary']()
    assert (code == 0) == passes, out
    assert ('card holds 0 detected block(s)' in summary) != passes


def test_signal_during_capture_startup_stops_capture(soak):
    """Regression: the script stopped capture with SIGINT, which a
    background job of a non-interactive shell starts out ignoring; a
    signal before capture had installed its handler left it running
    the whole --duration.  SIGTERM stops it at any point."""
    started = time.monotonic()
    process = soak['start'](SOAK_DURATION_S='20', FAKE_STARTUP_S='3')
    _wait_for(soak, process, 'soak/*/pid')
    os.kill(process.pid, signal.SIGTERM)       # the script only
    code, out = _finish(process)
    assert time.monotonic() - started < 10, out
    assert code == 1, out
    assert 'soak interrupted by SIGTERM' in soak['summary']()


def test_lost_output_still_writes_the_summary(soak):
    """Regression: with stdout a pipe whose reader was gone (`ssh`
    without -t losing the connection) the next echo killed the script
    with SIGPIPE before summary.txt was written."""
    process = soak['start'](SOAK_DURATION_S='3', SOAK_TOLERANCE_S='0')
    _wait_for(soak, process, 'soak/*/samples.csv')
    process.stdout.close()
    assert process.wait(timeout=60) == 0
    assert 'RESULT: PASS' in soak['summary']()
