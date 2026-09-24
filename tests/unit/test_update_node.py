# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/update_node.sh against a real git clone and a fake systemd.

The node's clone, its origin and its venv are real directories; the
commands that need a Pi (systemctl, root, the journal) are small scripts
on PATH that record their calls.  The fake capture service crash-loops
whenever the checked-out tree contains a file named BROKEN, the way a
bad release would under Restart=always, and an instance without its
/etc/default/thriftyx-capture@<rxid> file fails to start, as it does
under systemd.  The fake pip fails when the tree contains BADDEPS, or
always under FAKE_PIP_FAIL (package index unreachable); its call number
FAKE_PIP_HOLD waits for the test (which interrupts the run meanwhile).
The node is set up as receiver rx1, verified at the clone's first
commit (.last_known_good_sha).
"""

import os
import shutil
import signal
import subprocess
import sys
import time
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
    instance="${2#thriftyx-capture@}"; instance="${instance%.service}"
    if [ ! -e "${ENV_DIR}/thriftyx-capture@${instance}" ]; then
      # EnvironmentFile= without '-': systemd refuses to start it.
      echo "Job for $2 failed because of unavailable resources." >&2
      exit 1
    fi
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

FAKE_PIP = r'''#!/bin/bash
echo "$*" >> "${FAKE_STATE}/pip"
if [ "$(wc -l < "${FAKE_STATE}/pip")" = "${FAKE_PIP_HOLD:-}" ]; then
  : > "${FAKE_STATE}/pip-held"
  for _ in $(seq 300); do
    [ -e "${FAKE_STATE}/pip-go" ] && break; sleep 0.1
  done
fi
[ ! -e BADDEPS ] && [ -z "${FAKE_PIP_FAIL:-}" ]
'''

# logger -t TAG with the message on stdin: the journal is a file.
FAKE_LOGGER = r'''#!/bin/bash
cat >> "${FAKE_STATE}/journal"
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
    (venv / 'pip').write_text(FAKE_PIP)
    (venv / 'python').write_text('#!/bin/bash\nexit 1\n')  # no pyfftw
    # Installed and health-checked at A, as after an earlier update.
    (clone / '.last_known_good_sha').write_text(subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=clone, capture_output=True,
        text=True, check=True).stdout)

    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    (fakebin / 'systemctl').write_text(FAKE_SYSTEMCTL)
    (fakebin / 'id').write_text(FAKE_ID)
    (fakebin / 'logger').write_text(FAKE_LOGGER)
    for exe in (*venv.iterdir(), *fakebin.iterdir()):
        exe.chmod(0o755)

    units = tmp_path / 'etc-systemd'
    units.mkdir()
    (units / 'thriftyx-capture@.service').write_text('unit v1\n')
    bindir = tmp_path / 'usr-local-bin'
    bindir.mkdir()
    (bindir / 'cleanup_old_captures.sh').write_text('cleanup v1\n')
    defaults = tmp_path / 'etc-default'
    defaults.mkdir()
    (defaults / 'thriftyx-capture@rx1').write_text('THRIFTYX_OUT=/x\n')
    state = tmp_path / 'state'
    state.mkdir()

    env = dict(os.environ,
               PATH='{}:{}'.format(fakebin, os.environ['PATH']),
               THRIFTYX_HOME=str(clone), HEALTH_WAIT_S='0',
               UNIT_DIR=str(units), BIN_DIR=str(bindir),
               ENV_DIR=str(defaults),
               FAKE_STATE=str(state), FAKE_CLONE=str(clone), FAKE_UID='0')
    for key in ('PIP_EXTRAS', 'THRIFTYX_RXID', 'THRIFTYX_SERVICE',
                'JOURNAL_STREAM', 'FAKE_PIP_HOLD', 'FAKE_PIP_FAIL'):
        env.pop(key, None)

    def run(**overrides):
        return subprocess.run(['bash', str(SCRIPT)], env={**env, **overrides},
                              capture_output=True, text=True, timeout=60)

    def start(**overrides):
        """Run in the background, in its own session and process group
        (like a command under sshd), output on a pipe."""
        return subprocess.Popen(['bash', str(SCRIPT)],
                                env={**env, **overrides},
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)

    def publish(message, files):
        _commit(work, message, files)
        _git(work, 'push', '-q', str(origin), 'master')

    def rewind(sha):
        """Force-push origin's master back to SHA."""
        _git(work, 'push', '-q', '-f', str(origin),
             '{}:refs/heads/master'.format(sha))

    head = lambda: subprocess.run(  # noqa: E731
        ['git', 'rev-parse', 'HEAD'], cwd=clone, capture_output=True,
        text=True, check=True).stdout.strip()
    return dict(run=run, start=start, publish=publish, rewind=rewind,
                head=head, clone=clone, units=units, bindir=bindir,
                defaults=defaults, state=state)


def _calls(node):
    calls = node['state'] / 'calls'
    return calls.read_text() if calls.exists() else ''


def _lkg(node):
    return (node['clone'] / '.last_known_good_sha').read_text().strip()


def _pending(node):
    return (node['clone'] / '.update_pending').exists()


def _hold_pip(node, process):
    """Wait until the run is inside the held pip call."""
    held = node['state'] / 'pip-held'
    deadline = time.monotonic() + 30
    while not held.exists():
        assert process.poll() is None, process.stdout.read()
        assert time.monotonic() < deadline, 'pip was never called'
        time.sleep(0.05)


def _release_pip(node):
    (node['state'] / 'pip-go').touch()


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


def test_restarts_this_nodes_capture_instance(node):
    """Regression: the script always restarted thriftyx-capture@rx0.  On
    a node set up as rx1 that instance has no env file, so the restart
    failed, the rollback failed the same way, and every update of a node
    other than rx0 ended in exit 2 (page) with its real unit untouched."""
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(node)
    assert 'restart thriftyx-capture@rx1.service' in calls
    assert 'rx0' not in calls


@pytest.mark.parametrize('instances', [[], ['rx0', 'rx1']])
def test_unknown_capture_instance_is_a_setup_error(node, instances):
    """No env file, or several: stop before pulling anything."""
    for path in node['defaults'].iterdir():
        path.unlink()
    for instance in instances:
        (node['defaults'] / 'thriftyx-capture@{}'.format(instance)) \
            .write_text('')
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    before = node['head']()
    result = node['run']()
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'THRIFTYX_SERVICE' in result.stdout
    assert node['head']() == before
    assert _calls(node) == ''


@pytest.mark.parametrize('override', [
    {'THRIFTYX_RXID': '2'},
    {'THRIFTYX_SERVICE': 'thriftyx-capture@rx2.service'},
])
def test_explicit_instance_wins(node, override):
    (node['defaults'] / 'thriftyx-capture@rx2').write_text('')
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    result = node['run'](**override)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(node)
    assert 'restart thriftyx-capture@rx2.service' in calls
    assert 'rx1' not in calls


def test_explicit_instance_without_env_file_is_a_setup_error(node):
    """A wrong THRIFTYX_RXID is caught before the pull, not by a failed
    restart and a rollback that pages someone."""
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    before = node['head']()
    result = node['run'](THRIFTYX_RXID='0')
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'thriftyx-capture@rx0' in result.stdout
    assert node['head']() == before
    assert _calls(node) == ''


def test_install_failure_leaves_the_service_alone(node):
    """The new release does not install: back to the old tree and its
    package, with no restart (the service never left the old code)."""
    before = node['head']()
    node['publish']('bad deps', {'BADDEPS': 'x\n'})
    result = node['run']()
    assert result.returncode == 1, result.stdout + result.stderr
    assert node['head']() == before
    assert _calls(node) == ''
    assert len((node['state'] / 'pip').read_text().splitlines()) == 2
    assert 'WARNING' not in result.stdout


def test_unreachable_package_index_is_not_a_page(node):
    """Regression: git fetch works but PyPI does not, so reinstalling the
    old release fails too.  The service was never touched and the reset
    already restored the code its editable install loads, so this is
    exit 1 (running the old version), not 2 (page)."""
    before = node['head']()
    node['publish']('B', {'rpi/cleanup_old_captures.sh': 'cleanup v2\n'})
    result = node['run'](FAKE_PIP_FAIL='1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert node['head']() == before
    assert _calls(node) == ''
    assert 'WARNING' in result.stdout
    assert (node['bindir'] / 'cleanup_old_captures.sh').read_text() \
        == 'cleanup v1\n'


@pytest.mark.parametrize('has_record', [True, False])
def test_package_index_down_for_several_runs_is_never_a_page(node,
                                                             has_record):
    """Regression: the first run with PyPI unreachable kept the pending
    marker; the next one took it for an update that had touched the
    service, rolled back in full, failed to reinstall and exited 2
    (page) -- without a single systemctl call.  Every such run is exit
    1 until pip works; then the update goes through."""
    before = node['head']()
    if has_record:
        node['publish']('B', {'rpi/cleanup_old_captures.sh':
                              'cleanup v2\n'})
    else:
        # Never verified: the run installs and checks the tree it has.
        (node['clone'] / '.last_known_good_sha').unlink()
    for _ in range(3):
        result = node['run'](FAKE_PIP_FAIL='1')
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'pip install of' in result.stdout
        assert 'service untouched' in result.stdout
        assert 'failed on new sha' not in result.stdout
        assert 'WARNING' in result.stdout
        assert node['head']() == before
    assert _calls(node) == ''
    assert (node['bindir'] / 'cleanup_old_captures.sh').read_text() \
        == 'cleanup v1\n'
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'restart thriftyx-capture@rx1.service' in _calls(node)
    assert _lkg(node) == node['head']()
    assert not _pending(node)


def test_killed_install_then_package_index_down(node):
    """A run killed inside pip left HEAD on the new release; the next
    run cannot install it either.  Units and service were never
    touched: back to the last known good tree, exit 1, no restart."""
    before = node['head']()
    node['publish']('B', {'rpi/systemd/thriftyx-capture@.service':
                          'unit v2\n'})
    process = node['start'](FAKE_PIP_HOLD='1')
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()
    assert node['head']() != before
    result = node['run'](FAKE_PIP_FAIL='1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'back to {}'.format(before) in result.stdout
    assert node['head']() == before
    assert _calls(node) == ''
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v1\n'
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v2\n'


def test_failed_rollback_says_which_step_failed(node):
    """Once units and service were touched (here: a rollback cut short
    after its reset), an install failure needs the full rollback; when
    that cannot install either, exit 2 names the failed step."""
    before = node['head']()
    node['publish']('broken', {
        'BROKEN': 'x\n',
        'rpi/systemd/thriftyx-capture@.service': 'unit broken\n'})
    process = node['start'](FAKE_PIP_HOLD='2')    # the rollback's pip
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()
    result = node['run'](FAKE_PIP_FAIL='1')
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'already touched the units or the service' in result.stdout
    assert 'rollback: pip install of {} failed'.format(before) \
        in result.stdout
    assert 'rollback to {} failed'.format(before) in result.stdout
    assert _pending(node)


@pytest.mark.parametrize('record', [None, 'not a sha\n'])
def test_node_without_a_record_is_verified_once(node, record):
    """No (usable) .last_known_good_sha: nothing shows the checked-out
    release was installed and health-checked, so the first run does it
    and records it; after that the node is up to date."""
    lkg = node['clone'] / '.last_known_good_sha'
    if record is None:
        lkg.unlink()
    else:
        lkg.write_text(record)
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'restart thriftyx-capture@rx1.service' in _calls(node)
    assert _lkg(node) == node['head']()
    assert not _pending(node)
    calls = _calls(node)
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no-op' in result.stdout
    assert _calls(node) == calls


def test_killed_update_is_finished_by_the_next_run(node):
    """Regression: a run killed after `git merge` (here inside pip) left
    new code on disk, half installed, never restarted; the next run
    compared only HEAD with origin and said "no-op", exit 0."""
    before = node['head']()
    node['publish']('B', {'rpi/systemd/thriftyx-capture@.service':
                          'unit v2\n'})
    process = node['start'](FAKE_PIP_HOLD='1')
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()
    assert node['head']() != before          # the merge had happened
    assert _calls(node) == ''                # nothing restarted

    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'did not finish' in result.stdout
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v2\n'
    calls = _calls(node)
    assert 'daemon-reload' in calls
    assert 'restart thriftyx-capture@rx1.service' in calls
    assert _lkg(node) == node['head']()
    assert not _pending(node)
    result = node['run']()
    assert 'no-op' in result.stdout, result.stdout


def test_unfinished_update_that_fails_goes_back_to_the_last_good(node):
    """Finishing an interrupted update of a bad release rolls back to
    the last known good SHA, not to the half-installed HEAD."""
    before = node['head']()
    node['publish']('broken', {
        'BROKEN': 'x\n',
        'rpi/systemd/thriftyx-capture@.service': 'unit broken\n'})
    process = node['start'](FAKE_PIP_HOLD='1')
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()

    result = node['run']()
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'rolling back to {}'.format(before) in result.stdout
    assert node['head']() == before
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v1\n'
    assert _lkg(node) == before
    assert not _pending(node)


def test_interrupted_rollback_is_finished(node):
    """A rollback killed after its `git reset` leaves HEAD on the last
    known good SHA with the bad release's unit installed.  Even when
    origin is then rewound to that SHA, the pending marker makes the
    next run restore the unit and restart, not report "no-op"."""
    before = node['head']()
    node['publish']('broken', {
        'BROKEN': 'x\n',
        'rpi/systemd/thriftyx-capture@.service': 'unit broken\n'})
    # pip call 1 installs the bad release, call 2 is the rollback's.
    process = node['start'](FAKE_PIP_HOLD='2')
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()
    assert node['head']() == before
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit broken\n'

    node['rewind'](before)
    restarts = _calls(node).count('restart ')
    result = node['run']()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'did not finish' in result.stdout
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v1\n'
    assert _calls(node).count('restart ') == restarts + 1
    assert _lkg(node) == before
    assert not _pending(node)


def test_ssh_hangup_does_not_stop_the_update(node):
    """Regression: the hangup of an `ssh -t` session (SIGHUP to the
    foreground process group) killed the run inside pip."""
    node['publish']('B', {'rpi/systemd/thriftyx-capture@.service':
                          'unit v2\n'})
    process = node['start'](FAKE_PIP_HOLD='1')
    _hold_pip(node, process)
    os.killpg(process.pid, signal.SIGHUP)
    _release_pip(node)
    out, _ = process.communicate(timeout=60)
    assert process.returncode == 0, out
    assert 'restart thriftyx-capture@rx1.service' in _calls(node)
    assert _lkg(node) == node['head']()


def test_lost_connection_does_not_stop_the_update(node):
    """Regression: with plain `ssh` the output is a pipe to sshd; once
    the connection was gone the next log line died of SIGPIPE, after
    the units were replaced but before daemon-reload and restart.  The
    run now completes and its log reaches the journal."""
    node['publish']('B', {'rpi/systemd/thriftyx-capture@.service':
                          'unit v2\n'})
    process = node['start'](FAKE_PIP_HOLD='1')
    _hold_pip(node, process)
    process.stdout.close()
    _release_pip(node)
    assert process.wait(timeout=60) == 0
    calls = _calls(node)
    assert 'daemon-reload' in calls
    assert 'restart thriftyx-capture@rx1.service' in calls
    assert _lkg(node) == node['head']()
    journal = (node['state'] / 'journal').read_text()
    assert 'updated {}'.format(node['units'] / 'thriftyx-capture@.service') \
        in journal
    assert 'OK: now running {}'.format(node['head']()) in journal


def test_local_commit_is_refused(node):
    """Regression: a clone ahead of origin (a local commit) was
    "updated" -- merge --ff-only said "Already up to date" -- capture
    restarted on every run and origin's SHA was recorded as good."""
    before = node['head']()
    _commit(node['clone'], 'local hotfix', {'HOTFIX': 'x\n'})
    local = node['head']()
    for _ in range(2):
        result = node['run']()
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'not an ancestor of origin/master' in result.stdout
    assert _calls(node) == ''
    assert node['head']() == local
    assert _lkg(node) == before


def test_origin_rewound_by_force_push_is_refused(node):
    """Origin force-pushed back to an older commit: the node does not
    follow it (nor pretend to): exit 1, service and record untouched."""
    before = node['head']()
    node['publish']('B', {'rpi/systemd/thriftyx-capture@.service':
                          'unit v2\n'})
    assert node['run']().returncode == 0
    updated = node['head']()
    calls = _calls(node)
    node['rewind'](before)
    result = node['run']()
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'not an ancestor of origin/master' in result.stdout
    assert 'force' in result.stdout
    assert _calls(node) == calls
    assert node['head']() == updated
    assert _lkg(node) == updated
    assert (node['units'] / 'thriftyx-capture@.service').read_text() \
        == 'unit v2\n'
