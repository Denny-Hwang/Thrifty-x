# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""example/Makefile gives every receiver's cards its own rxid.

It used to detect every card with the same detector.cfg (rxid 0), so
identify saw one receiver and match paired nothing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[2] / 'example' / 'Makefile'

pytestmark = pytest.mark.skipif(shutil.which('make') is None,
                                reason="needs make")


def test_rxid_comes_from_the_card_name(tmp_path):
    cards = tmp_path / 'cards'
    cards.mkdir()
    for name in ('rx0.card', 'rx1.card', 'rx12_20260101T000000.card'):
        (cards / name).write_text('')
    (tmp_path / 'detector.cfg').write_text('rxid: 0\n')
    result = subprocess.run(['make', '-n', '-f', str(MAKEFILE), 'detect'],
                            cwd=tmp_path, capture_output=True, text=True,
                            check=True)
    detects = sorted(line for line in result.stdout.splitlines()
                     if line.startswith('thriftyx detect'))
    assert detects == [
        'thriftyx detect cards/rx0.card -o toad/rx0.toad --rxid 0',
        'thriftyx detect cards/rx1.card -o toad/rx1.toad --rxid 1',
        'thriftyx detect cards/rx12_20260101T000000.card '
        '-o toad/rx12_20260101T000000.toad --rxid 12',
    ]
