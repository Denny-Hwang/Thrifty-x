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
@pytest.mark.parametrize('value', ['0', '-1', '1.5', '7d', 'days'])
def test_bad_retention_is_refused(tmp_path, setting, value):
    """0 or a value that is not a whole number of days is a config error,
    not "expire everything" (it would take the file being written)."""
    root = tmp_path / 'data'
    files = [_file(root / sub / name, 40) for sub, name in
             [('card', 'rx0.card'), ('toad', 'rx0.toad'),
              ('log', 'capture.log')]]
    result, log = _run(tmp_path, root, DISK_WARN_PCT=100,
                       DISK_PURGE_PCT=101, **{setting: value})
    assert result.returncode == 2, result.stderr
    assert setting in log
    assert all(f.exists() for f in files)


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
