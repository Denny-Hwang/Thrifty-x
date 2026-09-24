# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/cleanup_old_captures.sh on a scratch data directory."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'rpi' / 'cleanup_old_captures.sh'

pytestmark = pytest.mark.skipif(sys.platform != 'linux',
                                reason="needs bash and GNU find")


def _file(path, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x\n')
    then = time.time() - age_days * 86400
    os.utime(path, (then, then))
    return path


def _run(tmp_path, root, **settings):
    config = tmp_path / 'cleanup.env'
    config.write_text('THRIFTYX_OUT={}\n'.format(root) + ''.join(
        '{}={}\n'.format(k, v) for k, v in settings.items()))
    fakebin = tmp_path / 'bin'
    fakebin.mkdir(exist_ok=True)
    logger = fakebin / 'logger'
    logger.write_text('#!/bin/bash\nshift 2\necho "$*" >> "{}"\n'.format(
        tmp_path / 'syslog'))
    logger.chmod(0o755)
    env = dict(os.environ, THRIFTYX_CLEANUP_CONFIG=str(config),
               PATH='{}:{}'.format(fakebin, os.environ['PATH']))
    result = subprocess.run(['bash', str(SCRIPT)], env=env,
                            capture_output=True, text=True, timeout=60)
    log = tmp_path / 'syslog'
    return result, (log.read_text() if log.exists() else '')


def test_expires_by_type_and_reports(tmp_path):
    root = tmp_path / 'data'
    old_card = _file(root / 'card' / 'rx0_old.card', 10)
    new_card = _file(root / 'card' / 'rx0_new.card', 1)
    old_toad = _file(root / 'toad' / 'rx0.toad', 40)
    old_log = _file(root / 'log' / 'capture.log', 40)
    other = _file(root / 'log' / 'notes.txt', 40)  # not a log: kept
    result, log = _run(tmp_path, root, DISK_WARN_PCT=100,
                       DISK_PURGE_PCT=101)
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
    result, log = _run(tmp_path, root, CARD_RETENTION_DAYS=days,
                       DISK_WARN_PCT=100, DISK_PURGE_PCT=101)
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
    result, log = _run(tmp_path, root, DISK_WARN_PCT=100,
                       DISK_PURGE_PCT=101, **{setting: value})
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
    result, log = _run(tmp_path, root, CARD_RETENTION_DAYS=0,
                       DISK_WARN_PCT=0, DISK_PURGE_PCT=0)
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
    result, log = _run(tmp_path, root, CARD_RETENTION_DAYS=value,
                       DISK_WARN_PCT=100, DISK_PURGE_PCT=101)
    assert result.returncode == 0, result.stderr
    assert card.exists() != expired


def test_nothing_to_do_is_quiet(tmp_path):
    root = tmp_path / 'data'
    _file(root / 'card' / 'rx0.card', 1)
    result, log = _run(tmp_path, root, DISK_WARN_PCT=100,
                       DISK_PURGE_PCT=101)
    assert result.returncode == 0, result.stderr
    assert log == ''


def test_missing_root_is_reported(tmp_path):
    result, log = _run(tmp_path, tmp_path / 'not-mounted')
    assert result.returncode == 2
    assert 'not a directory' in log
