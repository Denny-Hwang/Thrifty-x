# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/heartbeat.py: service state, disk use and detection freshness.

A fake systemctl on PATH behaves like systemd's `is-active`: it prints
the unit state and exits 0 only for active/reloading, 3 otherwise.
"""

import collections
import datetime
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'rpi' / 'heartbeat.py'

pytestmark = pytest.mark.skipif(sys.platform != 'linux',
                                reason="needs a POSIX shell for the fake")

FAKE_SYSTEMCTL = r'''#!/bin/sh
[ -n "${FAKE_STATE}" ] || exit 1
echo "${FAKE_STATE}"
case "${FAKE_STATE}" in active|reloading) exit 0 ;; *) exit 3 ;; esac
'''


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    systemctl = fakebin / 'systemctl'
    systemctl.write_text(FAKE_SYSTEMCTL)
    systemctl.chmod(0o755)
    monkeypatch.setenv('PATH', str(fakebin))
    spec = importlib.util.spec_from_file_location('heartbeat', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('state', [
    'active', 'reloading', 'activating', 'failed', 'inactive',
    'deactivating'])
def test_service_state_is_what_systemctl_prints(heartbeat, monkeypatch,
                                                state):
    """Regression: every state but active/reloading came out 'unknown'
    (the non-zero exit was treated as an error), so a unit `failed` on a
    bad capture.cfg or crash-looping in `activating` was never reported
    as such."""
    monkeypatch.setenv('FAKE_STATE', state)
    assert heartbeat._service_state('thriftyx-capture@rx0.service') == state


def test_service_state_unknown_without_an_answer(heartbeat, monkeypatch):
    monkeypatch.setenv('FAKE_STATE', '')      # prints nothing, exits 1
    assert heartbeat._service_state('x.service') == 'unknown'
    monkeypatch.setenv('PATH', '/nonexistent')  # no systemctl at all
    assert heartbeat._service_state('x.service') == 'unknown'


def test_payload_carries_the_failed_state(heartbeat, monkeypatch, tmp_path):
    monkeypatch.setenv('FAKE_STATE', 'failed')
    monkeypatch.setenv('THRIFTYX_OUT', str(tmp_path))
    assert heartbeat.build_payload()['service_state'] == 'failed'


_Usage = collections.namedtuple('_Usage', 'total used free')


@pytest.mark.parametrize('total, used, free, pct', [
    (100, 92, 0, 100),     # full for the capture user: 8 % reserved
    (200, 1, 199, 1),      # df rounds up
    (100, 50, 45, 53),
    (100, 0, 95, 0),
])
def test_disk_pct_is_what_df_reports(heartbeat, monkeypatch, total, used,
                                     free, pct):
    """Regression: used / total read 92 on a disk full for non-root
    users; df and the cleanup thresholds use used / (used + available),
    rounded up."""
    monkeypatch.setattr(heartbeat.shutil, 'disk_usage',
                        lambda path: _Usage(total, used, free))
    assert heartbeat._disk_pct('/x') == pct


def test_disk_pct_matches_df_on_a_real_filesystem(heartbeat, tmp_path):
    def df():
        out = subprocess.run(['df', '--output=pcent', str(tmp_path)],
                             capture_output=True, text=True,
                             env={'PATH': '/usr/bin:/bin'}).stdout
        return int(out.split()[-1].rstrip('%'))
    before = df()
    ours = heartbeat._disk_pct(str(tmp_path))
    assert ours in {before, df()}


def _card(path, blocks, age_s=0, tail=b''):
    """A card with a #v2 header and one line per (timestamp, size)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'#v2 bit_depth=12 sample_rate=6000000 endian=little\n')
        for i, (ts, size) in enumerate(blocks):
            f.write(b'%.6f %d %s\n' % (ts, i, b'A' * size))
        f.write(tail)
    then = time.time() - age_s
    os.utime(path, (then, then))
    return path


def _iso(ts):
    return datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone.utc).isoformat(timespec='seconds') \
        .replace('+00:00', 'Z')


def test_last_block_ts_sees_through_the_hourly_header(heartbeat, tmp_path):
    """Regression: the only detection signal was the newest card's
    mtime, which the header written at each hourly rotation keeps
    fresh, so "capture running but detecting nothing" never showed.
    last_block_ts is the newest detected block's own timestamp."""
    cards = tmp_path / 'card'
    detected = 1_760_000_000.25
    _card(cards / 'rx0_a.card', [(detected - 60, 100), (detected, 100)],
          age_s=7200)
    _card(cards / 'rx0_b.card', [], age_s=3600)    # nothing detected
    newest = _card(cards / 'rx0_c.card', [])       # rotated just now
    assert heartbeat._last_detection_ts(cards) == \
        _iso(newest.stat().st_mtime)
    assert heartbeat._last_block_ts(cards) == _iso(detected)


def test_last_block_ts_long_lines_and_line_being_written(heartbeat,
                                                         monkeypatch,
                                                         tmp_path):
    """Data lines far longer than the tail read, and a last line still
    being written (no newline yet): the last complete block counts."""
    monkeypatch.setattr(heartbeat, '_TAIL_CHUNK', 64)
    cards = tmp_path / 'card'
    _card(cards / 'rx0.card', [(1_760_000_000, 5000), (1_760_000_100, 5000)],
          tail=b'1760000200.000000 2 AAAA')
    assert heartbeat._last_block_ts(cards) == _iso(1_760_000_100)


def _count_reads(heartbeat, monkeypatch):
    """Make the heartbeat's open() count the bytes it reads."""
    total = [0]

    class Counting:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def seek(self, *args):
            return self._f.seek(*args)

        def read(self, *args):
            data = self._f.read(*args)
            total[0] += len(data)
            return data

    monkeypatch.setattr(heartbeat, 'open',
                        lambda *a, **k: Counting(open(*a, **k)),
                        raising=False)
    return total


def test_last_block_ts_reads_a_bounded_tail(heartbeat, monkeypatch,
                                            tmp_path):
    """Regression: with no parseable data line in the newest card the
    tail read doubled up to the whole file (a 300 MB card: 868 MB peak,
    8 s, every minute).  A card whose last _TAIL_MAX bytes hold no
    block counts as having none."""
    monkeypatch.setattr(heartbeat, '_TAIL_CHUNK', 1 << 10)
    monkeypatch.setattr(heartbeat, '_TAIL_MAX', 64 << 10)
    cards = tmp_path / 'card'
    detected = 1_760_000_000
    _card(cards / 'rx0_a.card', [(detected, 100)], age_s=3600)
    garbage = cards / 'rx0_b.card'
    garbage.write_bytes((b'x' * 999 + b'\n') * 2000)     # 2 MB, no block
    total = _count_reads(heartbeat, monkeypatch)
    assert heartbeat._last_block_ts(cards) == _iso(detected)
    assert total[0] <= (64 << 10) + (cards / 'rx0_a.card').stat().st_size


def test_last_block_ts_scan_has_a_total_budget(heartbeat, monkeypatch,
                                               tmp_path):
    monkeypatch.setattr(heartbeat, '_TAIL_CHUNK', 1 << 10)
    monkeypatch.setattr(heartbeat, '_TAIL_MAX', 64 << 10)
    monkeypatch.setattr(heartbeat, '_SCAN_MAX', 200 << 10)
    cards = tmp_path / 'card'
    _card(cards / 'rx0_old.card', [(1_760_000_000, 100)], age_s=7200)
    for k in range(10):
        path = cards / 'rx0_{}.card'.format(k)
        path.write_bytes((b'x' * 999 + b'\n') * 100)     # 100 kB each
        os.utime(path, (time.time() - k, time.time() - k))
    total = _count_reads(heartbeat, monkeypatch)
    assert heartbeat._last_block_ts(cards) is None
    assert total[0] <= 200 << 10


def test_last_block_ts_without_blocks(heartbeat, monkeypatch, tmp_path):
    cards = tmp_path / 'card'
    assert heartbeat._last_block_ts(cards) is None       # no directory
    _card(cards / 'rx0.card', [])
    (cards / 'rx0_noise.card').write_text('Using Volk machine: x\n')
    assert heartbeat._last_block_ts(cards) is None
    monkeypatch.setenv('FAKE_STATE', 'active')
    monkeypatch.setenv('THRIFTYX_OUT', str(tmp_path))
    payload = heartbeat.build_payload()
    assert payload['last_block_ts'] is None
    assert payload['last_detection_ts'] is not None
