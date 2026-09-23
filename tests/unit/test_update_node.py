# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/update_node.sh against a real git clone and a fake systemd.

The node's clone, its origin and its venv are real directories; the
commands that need a Pi (systemctl, root) are small scripts on PATH that
record their calls.  The fake capture service crash-loops whenever the
checked-out tree contains a file named BROKEN, the way a bad release
would under Restart=always.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'rpi' / 'update_node.sh'

pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or shutil.which('git') is None,
    reason="needs bash, git and GNU coreutils")

FAKE_SYSTEMCTL = r'''#!/bin/bash
# Fake systemctl: one service whose PID changes on every (re)start.
state="${FAKE_STATE}"
echo "$*" >> "${state}/calls"
starts=$(cat "${state}/starts" 2>/dev/null || echo 0)
case "$1" in
  restart)
    echo $((starts + 1)) > "${state}/starts"; echo 0 > "${state}/restarts" ;;
  daemon-reload) ;;
  is-active) echo active ;;
  show)
    restarts=$(cat "${state}/restarts" 2>/dev/null || echo 0)
    if [ -e "${FAKE_CLONE}/BROKEN" ]; then
      # Crash loop: systemd restarted it again since the last look.
      restarts=$((restarts + 1)); echo "${restarts}" > "${state}/restarts"
    fi
    case "$3" in
      MainPID) echo $((1000 + starts + restarts)) ;;
      NRestarts) echo "${restarts}" ;;
    esac ;;
esac
'''

FAKE_ID = r'''#!/bin/bash
if [ "$1" = "-u" ]; then echo "${FAKE_UID}"; else exec /usr/bin/id "$@"; fi
'''


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _commit(clone, message, files):
    for name, text in files.items():
        path = clone / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    _git(clone, 'add', '-A')
    _git(clone, '-c', 'user.name=t', '-c', 'user.email=t@t',
         'commit', '-q', '-m', message)


@pytest.fixture
def node(tmp_path):
    """A node clone at commit A, an origin, fake tools and install dirs."""
    work = tmp_path / 'work'
    work.mkdir()
    _git(work, 'init', '-q', '-b', 'master')
    _commit(work, 'A', {
        'rpi/systemd/thriftyx-capture@.service': 'unit v1\n',
        'rpi/systemd/thriftyx-heartbeat.timer': 'timer v1\n',
        'rpi/update_node.sh': 'script v1\n',
        'rpi/cleanup_old_captures.sh': 'cleanup v1\n',
    })
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'clone', '-q', '--bare', str(work), str(origin))
    clone = tmp_path / 'thrifty-x'
    _git(tmp_path, 'clone', '-q', str(origin), str(clone))

    venv = clone / '.venv' / 'bin'
    venv.mkdir(parents=True)
    (venv / 'pip').write_text(
        '#!/bin/bash\necho "$*" >> "${FAKE_STATE}/pip"\n')
    (venv / 'python').write_text('#!/bin/bash\nexit 1\n')  # no pyfftw

    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    (fakebin / 'systemctl').write_text(FAKE_SYSTEMCTL)
    (fakebin / 'id').write_text(FAKE_ID)
    for exe in (*venv.iterdir(), *fakebin.iterdir()):
        exe.chmod(0o755)

    units = tmp_path / 'etc-systemd'
    units.mkdir()
    (units / 'thriftyx-capture@.service').write_text('unit v1\n')
    bindir = tmp_path / 'usr-local-bin'
    bindir.mkdir()
    (bindir / 'cleanup_old_captures.sh').write_text('cleanup v1\n')
    state = tmp_path / 'state'
    state.mkdir()

    env = dict(os.environ,
               PATH='{}:{}'.format(fakebin, os.environ['PATH']),
               THRIFTYX_HOME=str(clone), HEALTH_WAIT_S='0',
               UNIT_DIR=str(units), BIN_DIR=str(bindir),
               FAKE_STATE=str(state), FAKE_CLONE=str(clone), FAKE_UID='0')
    env.pop('PIP_EXTRAS', None)

    def run(**overrides):
        return subprocess.run(['bash', str(SCRIPT)], env={**env, **overrides},
                              capture_output=True, text=True, timeout=60)

    def publish(message, files):
        _commit(work, message, files)
        _git(work, 'push', '-q', str(origin), 'master')

    head = lambda: subprocess.run(  # noqa: E731
        ['git', 'rev-parse', 'HEAD'], cwd=clone, capture_output=True,
        text=True, check=True).stdout.strip()
    return dict(run=run, publish=publish, head=head, clone=clone,
                units=units, bindir=bindir, state=state)


def test_up_to_date_is_a_no_op(node):
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no-op' in result.stdout
    assert not (node['state'] / 'calls').exists()


def test_update_refreshes_installed_units_and_scripts(node):
    node['publish']('B', {
        'rpi/systemd/thriftyx-capture@.service': 'unit v2\n',
        'rpi/systemd/thriftyx-heartbeat.timer': 'timer v2\n',
        'rpi/cleanup_old_captures.sh': 'cleanup v2\n',
    })
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v2\n'
    assert (node['bindir'] / 'cleanup_old_captures.sh').read_text() \
        == 'cleanup v2\n'
    # Only what the node had installed is refreshed.
    assert not (node['units'] / 'thriftyx-heartbeat.timer').exists()
    assert not (node['bindir'] / 'update_node.sh').exists()
    calls = (node['state'] / 'calls').read_text()
    assert 'daemon-reload' in calls
    assert (node['clone'] / '.last_known_good_sha').read_text().strip() \
        == node['head']()
    # No pyfftw in the venv: the fft extra is not requested.
    assert '.[analysis]' in (node['state'] / 'pip').read_text()


def test_crash_looping_release_is_rolled_back(node):
    """is-active reports a crash-looping service as active most of the
    time; the restart counter and MainPID must catch it."""
    before = node['head']()
    node['publish']('broken', {
        'BROKEN': 'x\n',
        'rpi/systemd/thriftyx-capture@.service': 'unit broken\n'})
    result = node['run']()
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'restarted during the health check' in result.stdout
    assert node['head']() == before
    # The rollback restored the old unit too.
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v1\n'


def test_requires_root(node):
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    before = node['head']()
    result = node['run'](FAKE_UID='1000')
    assert result.returncode == 3
    assert 'run as root' in result.stdout
    assert node['head']() == before
