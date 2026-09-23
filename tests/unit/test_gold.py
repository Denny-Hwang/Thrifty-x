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


# --- known answers ------------------------------------------------------------

import hashlib  # noqa: E402

import pytest  # noqa: E402


def _periodic_corr(a, b):
    """Periodic cross-correlation of two 0/1 sequences as +/-1."""
    x, y = np.where(a, 1, -1), np.where(b, 1, -1)
    return np.array([np.dot(x, np.roll(y, k)) for k in range(len(x))])


@pytest.mark.parametrize('bits', sorted(TAPS))
@pytest.mark.parametrize('idx', [0, 1])
def test_registers_are_m_sequences(bits, idx):
    """Each register is maximal-length: periodic autocorrelation is
    2^n - 1 at lag 0 and -1 at every other lag."""
    corr = _periodic_corr(gold(bits, idx), gold(bits, idx))
    assert corr[0] == 2 ** bits - 1
    assert set(corr[1:]) == {-1}


@pytest.mark.parametrize('bits', [5, 6, 7, 9, 11])
def test_preferred_pairs_give_three_valued_cross_correlation(bits):
    """A preferred pair's cross-correlation takes only the values
    {-1, -t, t - 2}, t = 1 + 2^floor((n + 2) / 2): the Gold property."""
    t = 1 + 2 ** ((bits + 2) // 2)
    corr = _periodic_corr(gold(bits, 0), gold(bits, 1))
    assert set(corr) <= {-1, -t, t - 2}


def test_ten_bit_pair_is_not_preferred():
    """The 10-bit taps inherited from upstream Thrifty are two m-sequences
    but not a preferred pair: cross-correlation reaches +/-97 instead of
    Gold's bound of 65.  Deployed transmitters use these codes, so they
    stay; this pins the known behaviour so a change is deliberate."""
    corr = _periodic_corr(gold(10, 0), gold(10, 1))
    assert max(abs(corr)) == 97


# SHA-256 of np.packbits(gold(10, idx)): templates generated here must
# match the codes in the transmitter firmware bit for bit.
_GOLD10_SHA256_PREFIX = {
    0: '305a8437993f00b6',
    1: 'cd67a2ed65025bfa',
    2: 'f37a7f7889171bd5',
    3: '5078a1d8f7f64873',
}


@pytest.mark.parametrize('idx', sorted(_GOLD10_SHA256_PREFIX))
def test_ten_bit_codes_are_unchanged(idx):
    digest = hashlib.sha256(np.packbits(gold(10, idx)).tobytes()).hexdigest()
    assert digest.startswith(_GOLD10_SHA256_PREFIX[idx])
