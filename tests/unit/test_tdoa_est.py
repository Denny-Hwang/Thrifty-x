# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Unit tests for thriftyx.tdoa_est — physical-constant + dtype contracts.

The full TDOA estimation pipeline needs synchronised multi-receiver
detections that are awkward to fabricate.  These tests pin the
small but high-blast-radius contracts: the speed-of-light constant, the
saturating MAX_TDOA cap, and the named tuples + structured dtypes that
downstream code (`tdoa_analysis`, `pos_est`) reads back.
"""

import numpy as np

from thriftyx import tdoa_est


def test_speed_of_light_constant_within_tolerance():
    """Constant matches CODATA c to ~0.1% — enough for TDOA ranging."""
    c_codata = 299_792_458.0
    assert abs(tdoa_est.SPEED_OF_LIGHT - c_codata) / c_codata < 1e-3


def test_max_tdoa_corresponds_to_30km_baseline():
    """MAX_TDOA caps at the light-travel time of a 30 km baseline."""
    # MAX_TDOA = 30 km / c
    expected = 30_000.0 / tdoa_est.SPEED_OF_LIGHT
    assert abs(tdoa_est.MAX_TDOA - expected) < 1e-12
    # Sanity: somewhere around 100 microseconds.
    assert 9e-5 < tdoa_est.MAX_TDOA < 1.1e-4


def test_tdoa_info_named_tuple_fields():
    """TdoaInfo field names are frozen — downstream tooling unpacks by name."""
    expected = ('rx0', 'rx1', 'tdoa', 'snr', 'model_quality',
                'det0_idx', 'det1_idx')
    assert tdoa_est.TdoaInfo._fields == expected


def test_tdoa_group_named_tuple_fields():
    """TdoaGroup field names are frozen."""
    assert tdoa_est.TdoaGroup._fields == (
        'group_id', 'timestamp', 'tx', 'tdoas')


def test_tdoa_dtype_round_trips_through_numpy():
    """A row using TDOA_DTYPE survives a NumPy structured-array round-trip."""
    row = np.array([(1, 2, 1.5e-5, 30.0, 0.99, 7, 11)],
                   dtype=list(zip(tdoa_est.TDOA_DTYPE['names'],
                                  tdoa_est.TDOA_DTYPE['formats'])))
    assert int(row['rx0'][0]) == 1
    assert int(row['rx1'][0]) == 2
    assert abs(float(row['tdoa'][0]) - 1.5e-5) < 1e-12
    assert abs(float(row['snr'][0]) - 30.0) < 1e-6


def test_matrix_dtype_extends_tdoa_dtype_with_group_keys():
    """MATRIX_DTYPE prepends (group_id, timestamp, tx) to TDOA_DTYPE."""
    assert tdoa_est.MATRIX_DTYPE['names'][:3] == ('group_id', 'timestamp', 'tx')
    assert tdoa_est.MATRIX_DTYPE['names'][3:] == tdoa_est.TDOA_DTYPE['names']
