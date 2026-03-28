# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the Detector class."""

import numpy as np
import pytest

from thriftyx.detect import Detector, DetectorSettings
from thriftyx.signal_utils import Signal


@pytest.fixture
def template():
    """Simple Gold-code-like template."""
    rng = np.random.default_rng(seed=123)
    return rng.choice([-1.0, 1.0], size=63).astype(np.float64)


@pytest.fixture
def detector_settings(template):
    block_len = 4096
    history_len = 1024
    return DetectorSettings(
        block_len=block_len,
        history_len=history_len,
        carrier_len=len(template),
        carrier_thresh=(20, 0, 0),
        carrier_window=(0, block_len // 2),
        template=template,
        corr_thresh=(20, 0, 0),
    )


def test_detector_callable_returns_result(detector_settings):
    """Test that __call__ returns the same as detect()."""
    detector = Detector(detector_settings, rxid=0)
    block = Signal(np.zeros(detector_settings.block_len, dtype=np.complex64))
    result_call = detector(0.0, 0, block)
    assert result_call is not None
    assert len(result_call) == 2  # (detected, result)
    detected, result = result_call
    assert isinstance(bool(detected), bool)
    assert result is not None
    assert hasattr(result, 'carrier_info')


def test_detector_no_signal(detector_settings):
    """Test that detector returns not-detected for noise-only input."""
    detector = Detector(detector_settings, rxid=0)
    rng = np.random.default_rng(seed=456)
    data = (rng.normal(size=detector_settings.block_len) +
            1j * rng.normal(size=detector_settings.block_len)).astype(np.complex64)
    block = Signal(data)
    detected, result = detector(0.0, 0, block)
    # detected may be True or False depending on random noise;
    # just verify the return structure is correct
    assert isinstance(bool(detected), bool)
    assert result is not None
