# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for stat_tools module."""

import numpy as np
from thriftyx.stat_tools import is_outlier


def test_is_outlier_no_outliers():
    """Normal data should have no outliers."""
    data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    outliers = is_outlier(data, thresh=3.5)
    assert not np.any(outliers)


def test_is_outlier_with_outlier():
    """A far outlier should be detected."""
    data = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
    outliers = is_outlier(data, thresh=3.5)
    assert bool(outliers[-1]) is True


def test_is_outlier_identical_values():
    """All identical values should produce no outliers (division by zero guard)."""
    data = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
    outliers = is_outlier(data, thresh=3.5)
    assert not np.any(outliers)
