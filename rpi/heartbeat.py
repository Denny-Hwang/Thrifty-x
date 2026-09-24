#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Minimal heartbeat for Thrifty-X RX nodes (Raspberry Pi 5).

Emits a single JSON line per invocation. Intended to run on a systemd
timer (default 60 s). Always logs to stdout (journald-friendly); if
``THRIFTYX_HEARTBEAT_URL`` is set, also POSTs the payload there.

Schema is the one defined in docs/rpi5_runbook.md §4.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib import request as _urlrequest


def _read_uptime() -> float:
    try:
        with open('/proc/uptime', 'r') as f:
            return float(f.read().split()[0])
    except OSError:
        return 0.0


def _read_cpu_temp() -> float | None:
    # Try vcgencmd first (Pi-specific, gives float in °C), then sysfs.
    if shutil.which('vcgencmd'):
        try:
            out = subprocess.check_output(
                ['vcgencmd', 'measure_temp'], text=True, timeout=2)
            # "temp=58.3'C\n"
            return float(out.split('=')[1].split("'")[0])
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
    for p in ('/sys/class/thermal/thermal_zone0/temp',):
        try:
            with open(p, 'r') as f:
                return int(f.read().strip()) / 1000.0
        except OSError:
            continue
    return None


def _read_throttled() -> str | None:
    if not shutil.which('vcgencmd'):
        return None
    try:
        out = subprocess.check_output(
            ['vcgencmd', 'get_throttled'], text=True, timeout=2)
        return out.strip().split('=', 1)[-1]
    except subprocess.SubprocessError:
        return None


def _disk_pct(path: str) -> int | None:
    """Use% as df and cleanup_old_captures.sh compute it.

    used / (used + available to non-root users), rounded up: the blocks
    reserved for root count as unavailable, so a disk the capture user
    can no longer write to reads 100, not ~95 (used / total).
    """
    try:
        usage = shutil.disk_usage(path)   # .free is f_bavail
    except OSError:
        return None
    size = usage.used + usage.free
    if size <= 0:
        return None
    return -(-usage.used * 100 // size)


def _service_state(unit: str) -> str:
    # `is-active` prints the state whatever it is but exits 3 for all
    # but active/reloading, so the exit status is not an error here:
    # checking it would report `failed` and `activating` as 'unknown'.
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', unit],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return result.stdout.strip() or 'unknown'


def _iso(ts: float) -> str | None:
    try:
        return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc) \
            .isoformat(timespec='seconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError, OSError):
        return None


def _cards_newest_first(card_dir: Path) -> list[tuple[float, Path]]:
    """(mtime, path) of the .card files, newest first.  A file deleted
    meanwhile (the cleanup job) is skipped."""
    cards = []
    try:
        for path in card_dir.glob('*.card'):
            try:
                cards.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    return sorted(cards, reverse=True)


def _last_detection_ts(card_dir: Path) -> str | None:
    """mtime of the newest .card file, as an ISO-8601 UTC string.

    The time of the last *write* to a card file: a detected block or,
    after an hourly rotation, the new file's header.  So it shows that
    capture is writing (stale by more than a rotation period: capture
    hung or stopped), not that it detects anything -- see
    :func:`_last_block_ts`.  The key name is kept for existing
    consumers.
    """
    cards = _cards_newest_first(card_dir)
    return _iso(cards[0][0]) if cards else None


# The tail is read backwards in chunks, doubling from _TAIL_CHUNK: one
# block of 32768 int16 I/Q samples is a ~175 kB line of base64, so a
# card holding blocks has one in its first read.  A card whose last
# _TAIL_MAX bytes hold no complete block line (a corrupt file, one huge
# line) counts as having none, and one heartbeat reads at most
# _SCAN_MAX bytes of cards in all: a timer that runs every minute on
# the capture node must not read hundreds of MB.
_TAIL_CHUNK = 1 << 18
_TAIL_MAX = 8 << 20
_SCAN_MAX = 32 << 20


def _last_block_time(path: Path, limit: int) -> tuple[float | None, int]:
    """Timestamp field of the last complete data line of a card file,
    looking at most `limit` bytes back from its end, and the number of
    bytes read."""
    nread = 0
    try:
        with open(path, 'rb') as f:
            size = f.seek(0, os.SEEK_END)
            floor = max(0, size - limit)
            start, chunk, tail = size, _TAIL_CHUNK, b''
            while start > floor:
                begin = max(floor, start - chunk)
                f.seek(begin)
                data = f.read(start - begin)
                nread += len(data)
                tail = data + tail
                start = begin
                lines = tail.split(b'\n')
                # The last piece is empty or a line still being
                # written; the first is cut unless it starts the file.
                lines = lines[:-1] if start == 0 else lines[1:-1]
                for line in reversed(lines):
                    if line[:1] in (b'', b'#'):
                        continue
                    try:
                        ts = float(line.split(None, 1)[0])
                    except ValueError:
                        continue      # tool noise, not a block
                    if math.isfinite(ts):
                        return ts, nread
                chunk *= 2
    except OSError:
        pass
    return None, nread


def _last_block_ts(card_dir: Path) -> str | None:
    """Capture time of the newest detected block, ISO-8601 UTC.

    A card holds a ``#v2`` header, then one ``timestamp block_idx
    samples`` line per detected block; this is the timestamp of the
    last such line in the newest card that has one.  Unlike the file
    mtime it does not move when capture starts a new, header-only file
    every hour, so it goes stale when the receiver stops detecting
    (antenna, gain, frequency, transmitters off).  None when no card
    holds a block (within the read limits above).
    """
    budget = _SCAN_MAX
    for _mtime, path in _cards_newest_first(card_dir):
        if budget <= 0:
            break
        ts, nread = _last_block_time(path, min(_TAIL_MAX, budget))
        if ts is not None:
            return _iso(ts)
        budget -= nread
    return None


def _version() -> str:
    try:
        from thriftyx import __version__
        return str(__version__)
    except Exception:
        return 'unknown'


def build_payload() -> dict:
    rxid = int(os.environ.get('THRIFTYX_RXID', '0'))
    out_root = os.environ.get('THRIFTYX_OUT', '/var/lib/thriftyx')
    unit = os.environ.get(
        'THRIFTYX_UNIT', f'thriftyx-capture@rx{rxid}.service')
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    return {
        'rxid': rxid,
        'host': socket.gethostname(),
        'ts': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'uptime_s': int(_read_uptime()),
        'disk_pct': _disk_pct(out_root),
        'cpu_temp_c': _read_cpu_temp(),
        'throttled': _read_throttled(),
        'service_state': _service_state(unit),
        'last_detection_ts': _last_detection_ts(Path(out_root) / 'card'),
        'last_block_ts': _last_block_ts(Path(out_root) / 'card'),
        'version': _version(),
    }


def _post(url: str, payload: dict, timeout: float = 5.0) -> None:
    body = json.dumps(payload).encode('utf-8')
    req = _urlrequest.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as exc:  # network failures must not crash the timer
        print(f'heartbeat: POST failed: {exc}', file=sys.stderr)


def main() -> int:
    payload = build_payload()
    print(json.dumps(payload))
    url = os.environ.get('THRIFTYX_HEARTBEAT_URL')
    if url:
        _post(url, payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
