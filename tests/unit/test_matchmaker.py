# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for matchmaker module."""

import io
import numpy as np
import pytest

from thriftyx.matchmaker import load_matches, save_matches


def test_save_and_load_matches():
    """Test round-trip save/load of match data."""
    matches = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    buf = io.StringIO()
    save_matches(matches, buf)

    buf.seek(0)
    loaded = load_matches(buf)
    assert len(loaded) == 3
    for orig, loaded_match in zip(matches, loaded):
        assert list(loaded_match) == orig


def test_load_matches_returns_lists():
    """Verify load_matches returns list of lists, not iterators."""
    buf = io.StringIO("1 2 3\n4 5 6\n")
    loaded = load_matches(buf)
    # Iterate twice — should work since they are lists, not iterators
    first = [list(m) for m in loaded]
    second = [list(m) for m in loaded]
    assert first == second
