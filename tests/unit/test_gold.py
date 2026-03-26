# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for Gold code generation."""

import numpy as np
from thriftyx.gold import gold, TAPS


def test_gold_length():
    """Gold code length should be 2^n - 1."""
    for nbits in [5, 6, 7]:
        if nbits in TAPS:
            seq = gold(nbits, 0)
            assert len(seq) == 2**nbits - 1


def test_gold_values_binary():
    """Gold code should consist of 0s and 1s."""
    seq = gold(5, 0)
    assert set(seq).issubset({0, 1})


def test_gold_different_codes():
    """Different code indices should produce different sequences."""
    seq0 = gold(5, 0)
    seq1 = gold(5, 1)
    assert not np.array_equal(seq0, seq1)
