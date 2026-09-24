# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""rpi/heartbeat.py reports the capture unit's state as systemctl does.

A fake systemctl on PATH behaves like systemd's `is-active`: it prints
the unit state and exits 0 only for active/reloading, 3 otherwise.
"""

import importlib.util
import sys
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
