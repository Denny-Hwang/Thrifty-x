"""Robustness tests for ``thriftyx.identify.detect_transmitter_windows``.

This is the auto-classifier that ``thrifty identify`` falls back to when
no ``--map`` (freq_map) is supplied. The prompt that motivated these
tests assumed the implementation was an `auto_classify(detections,
threshold_std=2.0)` that thresholds gaps in the carrier-bin histogram
with a (mean + 2*std) rule. In fact the implementation is the
histogram-peak detector ``detect_transmitter_windows``
(``thriftyx/identify.py:33-83`` / ``thrifty/identify.py:26-76``):

  1. ``cnts = np.bincount(freqs - min(freqs))``
  2. ``low_thresh, high_thresh = 0.4*std(cnts), 1.25*std(cnts)``
  3. Scan ``cnts`` with hysteresis: a "peak" is a contiguous region
     where ``cnt > high_thresh``; it ends when ``cnt < low_thresh``.
  4. Each peak becomes one transmitter; split-edges are placed at the
     midpoint of the gap between consecutive peaks.

The two algorithms behave differently on the prompt's test corpus:
the std-of-gaps version FAILS on the [101, 102, 128] case, but the
histogram-peak version PASSES (the histogram has 27 zero-count bins
between the two non-zero clusters, which is well below the low
threshold).

These tests record the actual behaviour of the current implementation.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np
import pytest

from thriftyx import identify as ti
from thrifty import identify as upstream_identify


def _n_transmitters(freqs, module):
    edges = module.detect_transmitter_windows(np.asarray(freqs, dtype=int))
    return len(edges) - 1


# ----------------------------------------------------------------------
# The six cases from the prompt. All pass against the current impl.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("module", [ti, upstream_identify])
@pytest.mark.parametrize("name,bins,expected_n", [
    ("a_single_tx_single_bin",             [101],                            1),
    ("b_two_well_separated_single_bin",    [101, 200],                       2),
    ("c_two_tx_with_within_spread",        [101, 102, 128, 129],             2),
    ("d_observed_failure_case",            [101, 102, 128],                  2),
    ("e_three_tx_with_realistic_spread",   [100, 101, 128, 129, 156, 157],   3),
    ("f_all_bins_consecutive_single_tx",   [101, 102, 103, 104],             1),
])
def test_prompt_cases(module, name, bins, expected_n):
    assert _n_transmitters(bins, module) == expected_n, (
        "Case {}: expected {} TX(s) from bins {} (module {})".format(
            name, expected_n, bins, module.__name__))


# ----------------------------------------------------------------------
# Realistic-scale BatRF cases (each cluster ~1000 detections).
# ----------------------------------------------------------------------

@pytest.mark.parametrize("module", [ti, upstream_identify])
def test_batrf_tx1_split_tx2_single_bin(module):
    """TX1 energy on bins 101+102 split 50/50; TX2 clean on bin 128.

    Mirrors the field-observed pattern: dual-bin TX1 should still
    classify as one transmitter, not two.
    """
    bins = np.concatenate([
        np.full(500, 101), np.full(500, 102),  # TX1
        np.full(1000, 128),                      # TX2
    ])
    assert _n_transmitters(bins, module) == 2


@pytest.mark.parametrize("module", [ti, upstream_identify])
def test_three_tx_each_with_three_bin_spread(module):
    bins = np.concatenate([
        np.full(300, 101), np.full(400, 102), np.full(300, 103),  # TX1
        np.full(300, 127), np.full(400, 128), np.full(300, 129),  # TX2
        np.full(300, 155), np.full(400, 156), np.full(300, 157),  # TX3
    ])
    assert _n_transmitters(bins, module) == 3


# ----------------------------------------------------------------------
# Known failure mode: extremely uneven cluster populations.
#
# When one TX has ~40x more detections than another, the dominant
# cluster's bincount entry inflates ``std(cnts)``, raising the
# high threshold above the weaker cluster's count. The weaker TX is
# then absorbed into "background" and lost.
#
# Workaround: use ``--map`` (freqmap) for production captures with
# known transmitters, as documented in user_guide.md. The
# auto-classifier is best treated as a development convenience for
# quick checks, not a calibration-grade tool.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("module", [ti, upstream_identify])
def test_extremely_uneven_populations_misses_weak_tx(module):
    """40:1 population imbalance causes the auto-classifier to miss the weak TX.

    Recorded as known limitation, not assertion of correctness. If the
    implementation grows a population-aware threshold, this test
    becomes the regression test for the fix - flip the assertion to
    `== 2` once auto-classifier handles uneven populations.
    """
    bins = np.concatenate([
        np.full(2000, 50),    # strong TX1
        np.full(50, 150),     # 40x weaker TX2
    ])
    n_detected = _n_transmitters(bins, module)
    # Currently observed: only the strong TX is recovered.
    assert n_detected == 1, (
        "Auto-classifier already handles uneven populations - "
        "update this test to assert ==2 and link to the fix PR.")


# ----------------------------------------------------------------------
# Adjacency boundary: when does the auto-classifier split?
# ----------------------------------------------------------------------

@pytest.mark.parametrize("module", [ti, upstream_identify])
@pytest.mark.parametrize("gap,expected_n", [
    (1, 1),   # adjacent bins -> single TX with width-2 cluster
    (2, 2),   # gap of one empty bin -> split
    (3, 2),
    (5, 2),
    (10, 2),
    (50, 2),
])
def test_adjacency_threshold(module, gap, expected_n):
    """At equal cluster populations, the splitter has gap-sensitivity of 1 bin."""
    bins = np.concatenate([
        np.full(500, 50),
        np.full(500, 50 + gap),
    ])
    assert _n_transmitters(bins, module) == expected_n
