# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Unit tests for thriftyx.identify — transmitter-window detection.

The math here is small but production-critical: an off-by-one in the
window-edge computation silently mis-labels transmitters and breaks the
downstream `match → tdoa → pos` pipeline.
"""

import numpy as np

from thriftyx import identify


def test_single_cluster_yields_one_transmitter():
    """A single tightly-clustered carrier bin shows up as one tx."""
    freqs = np.array([100, 100, 100, 101, 99, 100, 100, 100], dtype=int)
    edges = identify.detect_transmitter_windows(freqs)
    # edges is [first_bin, ..., last_bin]; #txs == len(edges) - 1
    assert len(edges) - 1 == 1


def test_two_well_separated_clusters_yield_two_transmitters():
    """Two clusters with a noise gap between them are split."""
    cluster_a = np.full(40, 100, dtype=int)
    cluster_b = np.full(40, 500, dtype=int)
    freqs = np.concatenate([cluster_a, cluster_b])
    edges = identify.detect_transmitter_windows(freqs)
    assert len(edges) - 1 == 2
    # The split edge must land in the empty gap (101..499).
    inner_edge = int(edges[1])
    assert 101 < inner_edge < 500


def test_edges_bracket_the_full_bin_range():
    """First / last edge equal the min / max+1 of the input."""
    freqs = np.array([10, 10, 10, 50, 50, 50], dtype=int)
    edges = identify.detect_transmitter_windows(freqs)
    assert int(edges[0]) == 10
    # last_bin = first_bin + len(bincount) = 10 + 41 = 51
    assert int(edges[-1]) == 51


def test_unidentified_tx_sentinel_value():
    """Sentinel value is stable; downstream code relies on it being -1."""
    assert identify.UNIDENTIFIED_TX == -1
