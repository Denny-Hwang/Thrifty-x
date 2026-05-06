#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Minimal heartbeat for Thrifty-X RX nodes (Raspberry Pi 5).

Emits a single JSON line per invocation. Intended to run on a systemd
timer (default 60 s). Always logs to stdout (journald-friendly); if
``THRIFTYX_HEARTBEAT_URL`` is set, also POSTs the payload there.

Schema is the one defined in docs/rpi5_runbook_ko.md §4.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
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
    try:
        usage = shutil.disk_usage(path)
        return int(round(usage.used * 100 / usage.total))
    except OSError:
        return None


def _service_state(unit: str) -> str:
    try:
        return subprocess.check_output(
            ['systemctl', 'is-active', unit],
            text=True, timeout=2).strip()
    except subprocess.SubprocessError:
        return 'unknown'
    except FileNotFoundError:
        return 'unknown'


def _last_detection_ts(card_dir: Path) -> str | None:
    try:
        files = list(card_dir.glob('*.card'))
    except OSError:
        return None
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return _dt.datetime.fromtimestamp(
        newest.stat().st_mtime, tz=_dt.timezone.utc
    ).isoformat(timespec='seconds').replace('+00:00', 'Z')


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
