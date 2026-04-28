# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Smoke tests for diagnostic scripts at 8-bit and 12-bit."""

import io

import numpy as np

from thriftyx.block_data import block_reader


def test_block_reader_consumes_8bit_stream():
    block_size = 8
    history = 2
    new_samples = block_size - history
    raw = np.zeros(new_samples * 2 * 3, dtype=np.uint8)
    stream = io.BytesIO(raw.tobytes())
    blocks = list(block_reader(stream, block_size, history, bit_depth=8))
    assert len(blocks) >= 2
    for _, _, data in blocks:
        assert len(data) == block_size


def test_block_reader_consumes_12bit_stream():
    block_size = 8
    history = 2
    new_samples = block_size - history
    raw = np.zeros(new_samples * 2 * 3, dtype=np.int16)
    stream = io.BytesIO(raw.tobytes())
    blocks = list(block_reader(stream, block_size, history, bit_depth=12))
    assert len(blocks) >= 2
    for _, _, data in blocks:
        assert len(data) == block_size
