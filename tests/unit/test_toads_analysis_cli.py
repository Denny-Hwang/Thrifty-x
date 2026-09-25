# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""analyze_toads runs on a headless node.

The module used to select TkAgg at import time, so `--export` crashed
("no display name") on a node without a display the moment the first
figure was created.
"""

import os
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from thriftyx.toads_data import (  # noqa: E402
    CarrierSyncInfo, CorrDetectionInfo, DetectionResult)


def _toads(path, interval=0.05):
    rng = np.random.default_rng(3)
    lines = []
    for i in range(40):
        for rxid in (0, 1):
            txid = i % 2
            det = DetectionResult(
                1000.0 + interval * i + 1e-4 * rxid, i, 20490.0 * i + 100.5,
                CarrierSyncInfo(120 + 30 * txid, rng.uniform(-0.5, 0.5),
                                500.0, 10.0),
                CorrDetectionInfo(100, rng.uniform(-0.5, 0.5), 400.0, 5.0),
                rxid=rxid, txid=txid)
            lines.append(det.serialize())
    path.write_text("\n".join(lines) + "\n")


def _headless_env():
    env = dict(os.environ)
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "MPLBACKEND"):
        env.pop(key, None)
    return env


@pytest.mark.parametrize('interval', [0.05, 10.0])
def test_export_works_without_a_display(tmp_path, interval):
    """interval 10.0: regression, a .toads spanning more than 150 s asked
    hist2d for a fractional number of time bins (TypeError), which failed
    example/Makefile's default target."""
    _toads(tmp_path / 'data.toads', interval)
    result = subprocess.run(
        [sys.executable, '-m', 'thriftyx.cli', 'analyze_toads',
         '-i', 'data.toads', '--export', 'out'],
        cwd=tmp_path, env=_headless_env(), capture_output=True, text=True,
        timeout=300)
    assert result.returncode == 0, result.stderr
    pngs = sorted(p.name for p in tmp_path.glob('out_*.png'))
    assert pngs, result.stdout


def test_interactive_without_a_display_points_to_export(tmp_path):
    _toads(tmp_path / 'data.toads')
    result = subprocess.run(
        [sys.executable, '-m', 'thriftyx.cli', 'analyze_toads',
         '-i', 'data.toads'],
        cwd=tmp_path, env=_headless_env(), capture_output=True, text=True,
        timeout=300)
    assert result.returncode == 1
    assert '--export' in result.stderr
    assert 'Traceback' not in result.stderr
