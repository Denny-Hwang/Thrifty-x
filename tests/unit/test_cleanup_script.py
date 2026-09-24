# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/cleanup_old_captures.sh on a scratch data directory.

A fake df reports the data disk's use as FAKE_DF_BASE percent plus
FAKE_DF_PER_CARD for every .card file under the data directory, so the
purge frees "space" card by card (50 % and none by default).
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'rpi' / 'cleanup_old_captures.sh'

pytestmark = pytest.mark.skipif(sys.platform != 'linux',
                                reason="needs bash and GNU find")

FAKE_DF = r'''#!/bin/bash
n=$(find "${FAKE_DF_ROOT}" -type f -name '*.card' 2>/dev/null | wc -l)
echo "Use%"
echo " $((FAKE_DF_BASE + FAKE_DF_PER_CARD * n))%"
'''


def _file(path, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x\n')
    then = time.time() - age_days * 86400
    os.utime(path, (then, then))
    return path


def _run(tmp_path, root, df=(50, 0), timeout=60, **settings):
    config = tmp_path / 'cleanup.env'
    config.write_text('THRIFTYX_OUT={}\n'.format(root) + ''.join(
        '{}={}\n'.format(k, v) for k, v in settings.items()))
    fakebin = tmp_path / 'bin'
    fakebin.mkdir(exist_ok=True)
    logger = fakebin / 'logger'
    logger.write_text('#!/bin/bash\nshift 2\necho "$*" >> "{}"\n'.format(
        tmp_path / 'syslog'))
    (fakebin / 'df').write_text(FAKE_DF)
    for exe in fakebin.iterdir():
        exe.chmod(0o755)
    env = dict(os.environ, THRIFTYX_CLEANUP_CONFIG=str(config),
               PATH='{}:{}'.format(fakebin, os.environ['PATH']),
               FAKE_DF_ROOT=str(root), FAKE_DF_BASE=str(df[0]),
               FAKE_DF_PER_CARD=str(df[1]))
    result = subprocess.run(['bash', str(SCRIPT)], env=env,
                            capture_output=True, text=True, timeout=timeout)
    log = tmp_path / 'syslog'
    return result, (log.read_text() if log.exists() else '')


def test_expires_by_type_and_reports(tmp_path):
    root = tmp_path / 'data'
    old_card = _file(root / 'card' / 'rx0_old.card', 10)
    new_card = _file(root / 'card' / 'rx0_new.card', 1)
    old_toad = _file(root / 'toad' / 'rx0.toad', 40)
    old_log = _file(root / 'log' / 'capture.log', 40)
    other = _file(root / 'log' / 'notes.txt', 40)  # not a log: kept
    result, log = _run(tmp_path, root)
    assert result.returncode == 0, result.stderr
    assert not old_card.exists() and not old_toad.exists()
    assert not old_log.exists()
    assert new_card.exists() and other.exists()
    assert 'deleted 1 card, 1 toad, 1 log file(s)' in log


@pytest.mark.parametrize('days', [1, 7])
def test_retention_is_days_not_days_plus_one(tmp_path, days):
    """Regression: `find -mtime +N` rounds an age down to whole days, so
    a file was deleted only once N+1 days old (CARD_RETENTION_DAYS=1
    kept two days of cards)."""
    root = tmp_path / 'data'
    kept = _file(root / 'card' / 'rx0_kept.card', days - 1 / 24)
    gone = _file(root / 'card' / 'rx0_gone.card', days + 1 / 24)
    result, log = _run(tmp_path, root, CARD_RETENTION_DAYS=days)
    assert result.returncode == 0, result.stderr
    assert kept.exists() and not gone.exists()
    assert 'deleted 1 card, 0 toad, 0 log file(s)' in log


@pytest.mark.parametrize('setting', ['CARD_RETENTION_DAYS',
                                     'TOAD_RETENTION_DAYS',
                                     'LOG_RETENTION_DAYS'])
@pytest.mark.parametrize('value', ['0', '-1', '1.5', '7d', 'days', '36501',
                                   '9223372036854775807',
                                   '9999999999999999'])
def test_bad_retention_skips_only_that_expiry(tmp_path, setting, value):
    """0, a huge value (N x 1440 minutes overflowed to a negative age) or
    one that is not a whole number of days is a config error, not
    "expire everything" (it would take the file being written).  Only
    that category is left alone; the others still expire."""
    root = tmp_path / 'data'
    files = {'CARD_RETENTION_DAYS': _file(root / 'card' / 'rx0.card', 40),
             'TOAD_RETENTION_DAYS': _file(root / 'toad' / 'rx0.toad', 40),
             'LOG_RETENTION_DAYS': _file(root / 'log' / 'capture.log', 40)}
    fresh = _file(root / 'card' / 'rx0_fresh.card', 0)
    result, log = _run(tmp_path, root, **{setting: value})
    assert result.returncode == 2, result.stderr
    assert "{}='{}'".format(setting, value) in log
    for name, path in files.items():
        assert path.exists() == (name == setting), name
    assert fresh.exists()


def test_bad_retention_still_runs_emergency_purge(tmp_path):
    """Regression: a refused retention value stopped the run before the
    disk-full purge, so an old config with CARD_RETENTION_DAYS=0 turned
    off all cleanup and let the disk fill."""
    root = tmp_path / 'data'
    old = _file(root / 'card' / 'rx0_old.card', 40)
    live = _file(root / 'card' / 'rx0_live.card', 0)
    result, log = _run(tmp_path, root, df=(95, 0), CARD_RETENTION_DAYS=0)
    assert result.returncode == 2, result.stderr
    assert 'CARD_RETENTION_DAYS' in log and 'emergency purge' in log
    assert not old.exists() and live.exists()
    assert 'purged 1 card file(s) for space' in log


@pytest.mark.parametrize('value, expired', [('007', True),
                                            ('36500', False)])
def test_retention_up_to_100_years_leading_zeros_ok(tmp_path, value,
                                                    expired):
    root = tmp_path / 'data'
    card = _file(root / 'card' / 'rx0.card', 8)
    result, log = _run(tmp_path, root, CARD_RETENTION_DAYS=value)
    assert result.returncode == 0, result.stderr
    assert card.exists() != expired


def test_nothing_to_do_is_quiet(tmp_path):
    root = tmp_path / 'data'
    _file(root / 'card' / 'rx0.card', 1)
    result, log = _run(tmp_path, root)
    assert result.returncode == 0, result.stderr
    assert log == ''


def test_purge_stops_below_the_warn_threshold(tmp_path):
    root = tmp_path / 'data'
    cards = [_file(root / 'card' / 'rx0_{}.card'.format(age), age)
             for age in (4, 3, 2, 1)]
    # 50 % + 10 % per card = 90 %: purge the oldest until below 80 %.
    result, log = _run(tmp_path, root, df=(50, 10))
    assert result.returncode == 0, result.stderr
    assert [card.exists() for card in cards] == [False, False, True, True]
    assert 'purged 2 card file(s) for space; disk now 70%' in log


def test_purge_handles_a_card_name_with_a_space(tmp_path):
    """Regression: the purge cut the path at its first space; `rm -f`
    of the cut name succeeded, nothing was deleted and, the disk still
    full, the loop never ended."""
    root = tmp_path / 'data'
    spaced = _file(root / 'card' / 'rx0 test.card', 2)
    live = _file(root / 'card' / 'rx0_live.card', 0)
    result, log = _run(tmp_path, root, df=(95, 0), timeout=20)
    assert result.returncode == 0, result.stderr
    assert not spaced.exists() and live.exists()
    assert 'purged 1 card file(s) for space' in log


def test_purge_skips_a_card_that_stays(tmp_path):
    """A card still there after `rm -f` (which reported success) is
    logged and skipped; the purge moves on and ends."""
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    (fakebin / 'rm').write_text(
        '#!/bin/bash\n'
        'case "$*" in *stuck*) exit 0 ;; esac\n'
        'exec {} "$@"\n'.format(shutil.which('rm')))
    root = tmp_path / 'data'
    stuck = _file(root / 'card' / 'rx0_stuck.card', 3)
    other = _file(root / 'soak' / 'run' / 'capture.card', 2)
    result, log = _run(tmp_path, root, df=(95, 0), timeout=20)
    assert result.returncode == 2, result.stderr
    assert 'cannot delete card/rx0_stuck.card' in log
    assert stuck.exists() and not other.exists()


@pytest.mark.parametrize('settings, bad', [
    ({'DISK_PURGE_PCT': '90%'}, "DISK_PURGE_PCT='90%'"),
    ({'DISK_WARN_PCT': '80%'}, "DISK_WARN_PCT='80%'"),
    ({'DISK_PURGE_PCT': '0'}, "DISK_PURGE_PCT='0'"),
    ({'DISK_PURGE_PCT': '101'}, "DISK_PURGE_PCT='101'"),
    ({'DISK_WARN_PCT': 'eighty'}, "DISK_WARN_PCT='eighty'"),
    ({'DISK_WARN_PCT': '95', 'DISK_PURGE_PCT': '90'},
     'DISK_WARN_PCT=95 is above DISK_PURGE_PCT=90'),
])
def test_bad_disk_threshold_uses_the_defaults(tmp_path, settings, bad):
    """Regression: DISK_PURGE_PCT=90% made the `[` comparison fail
    inside `if` -- no purge, no log, exit 0 -- on a full disk."""
    root = tmp_path / 'data'
    old = _file(root / 'card' / 'rx0_old.card', 2)
    live = _file(root / 'card' / 'rx0_live.card', 0)
    result, log = _run(tmp_path, root, df=(85, 10), **settings)
    assert result.returncode == 2, result.stderr
    assert bad in log
    # Defaults 80/90: 85 % + 10 % per card = 105 % -> purge to < 80 %.
    assert 'emergency purge' in log
    assert not old.exists() and live.exists()


def test_valid_disk_thresholds_are_used(tmp_path):
    root = tmp_path / 'data'
    old = _file(root / 'card' / 'rx0_old.card', 2)
    result, log = _run(tmp_path, root, df=(85, 10), DISK_WARN_PCT='097',
                       DISK_PURGE_PCT='100')
    assert result.returncode == 0, result.stderr
    assert old.exists()
    assert 'disk 95% >= 97%' not in log and 'purge' not in log


def test_soak_runs_expire_and_are_purged_with_the_cards(tmp_path):
    """Regression: soak_test.sh writes under THRIFTYX_OUT/soak, which
    was never expired or purged; on a full disk the purge deleted every
    regular card and left the soak cards."""
    root = tmp_path / 'data'
    run = root / 'soak' / '20260801T120000'
    expired_card = _file(run / 'capture.card', 10)
    expired_log = _file(run / 'stderr.log', 40)
    summary = _file(run / 'summary.txt', 40)     # the record: kept
    result, log = _run(tmp_path, root)
    assert result.returncode == 0, result.stderr
    assert not expired_card.exists() and not expired_log.exists()
    assert summary.exists()
    assert 'deleted 1 card, 0 toad, 1 log file(s)' in log

    # Within retention, but the oldest card on a full disk: purged
    # first, before any capture card.
    soak_card = _file(run / 'capture.card', 5)
    older = _file(root / 'card' / 'rx0_a.card', 4)
    newer = _file(root / 'card' / 'rx0_b.card', 3)
    # 60 % + 10 % per card = 90 %: one deletion reaches 80 % < 85 %.
    result, log = _run(tmp_path, root, df=(60, 10), DISK_WARN_PCT=85)
    assert result.returncode == 0, result.stderr
    assert not soak_card.exists()
    assert older.exists() and newer.exists()


def test_missing_root_is_reported(tmp_path):
    result, log = _run(tmp_path, tmp_path / 'not-mounted')
    assert result.returncode == 2
    assert 'not a directory' in log
