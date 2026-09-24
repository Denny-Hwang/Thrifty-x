# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""estimate_tdoas against exact arrival times.

Two receivers with unsynchronised sample clocks (offset, frequency
error and quadratic drift) record a beacon at a known position and a
mobile transmitter.  The beacon-referenced clock model must turn the
receivers' sample-of-arrival values back into the true time difference
of arrival of the mobile transmitter.
"""

import numpy as np
import pytest

from thriftyx import tdoa_est
from thriftyx.toads_data import CorrDetectionInfo, DetectionResult

C = tdoa_est.SPEED_OF_LIGHT
FS = 6e6
RX_POS = {0: (0.0, 0.0), 1: (1200.0, 0.0)}
BEACON = 7
MOBILE = 3
BEACON_POS = {BEACON: (1000.0, 800.0)}   # 456 m nearer rx1
MOBILE_POS = (350.0, 250.0)


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _sample_index(rxid, t, uptime=0.0):
    """Receiver *rxid*'s sample counter at true time *t*, in a capture
    that has been running for *uptime* seconds at t = 0."""
    if rxid == 0:
        return FS * (uptime + t)
    # Offset, +20 ppm frequency error and a drifting rate.
    return (1_234_567.25 + FS * (1 + 20e-6) * (uptime + t)
            + 0.5 * FS * 3e-6 * t ** 2)


def _detections(uptime=0.0):
    detections, matches = [], []
    emissions = ([(BEACON, BEACON_POS[BEACON], 0.05 * i) for i in range(20)]
                 + [(MOBILE, MOBILE_POS, 0.025 + 0.05 * i)
                    for i in range(19)])
    for txid, pos, t_emit in emissions:
        group = []
        for rxid, rx in RX_POS.items():
            arrival = t_emit + _dist(pos, rx) / C
            info = CorrDetectionInfo(0, 0.0, 100.0, 1.0)
            detections.append(DetectionResult(
                arrival, 0, _sample_index(rxid, arrival, uptime), None, info,
                rxid=rxid, txid=txid))
            group.append(len(detections) - 1)
        matches.append(group)
    return detections, matches


def _true_tdoa():
    return (_dist(MOBILE_POS, RX_POS[0]) - _dist(MOBILE_POS, RX_POS[1])) / C


def test_mobile_tdoa_matches_geometry():
    detections, matches = _detections()
    groups, failures = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, RX_POS, FS)
    assert failures == []
    assert len(groups) == 19
    assert all(g.tx == MOBILE for g in groups)
    got = np.array([g.tdoas['tdoa'][0] for g in groups])
    # 1e-10 s = 3 cm: the quadratic model captures this clock exactly.
    np.testing.assert_allclose(got, _true_tdoa(), atol=1e-10)


@pytest.mark.parametrize('model', [tdoa_est.build_model_poly,
                                   tdoa_est.build_model_weighted_poly])
@pytest.mark.parametrize('days', [7, 30, 100])
def test_precision_does_not_decay_with_uptime(days, model):
    """Regression: the clock model was fitted to the raw SoAs, which
    count samples since the capture started (5e12 after 10 days at
    6 Msps), and the ill-conditioned fit put the TDOAs 0.4 m off after
    7 days and 15 m after 30.  Fitted relative to the window's first
    beacon, the TDOAs are as precise as the SoAs' float64 steps allow."""
    detections, matches = _detections(uptime=days * 86400.0)
    groups, failures = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, RX_POS, FS,
        model_builder=model)
    assert failures == []
    got = np.array([g.tdoas['tdoa'][0] for g in groups])
    soa_step = np.spacing(max(d.soa for d in detections)) / FS
    np.testing.assert_allclose(got, _true_tdoa(), atol=1e-10 + 4 * soa_step)


def test_beacon_geometry_is_applied():
    """Treating the beacon as equidistant (dropping its TDOA) must give
    a visibly wrong answer -- the check an equidistant test beacon could
    never make."""
    detections, matches = _detections()
    groups, _ = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, RX_POS, FS)
    beacon_tdoa = (_dist(BEACON_POS[BEACON], RX_POS[0])
                   - _dist(BEACON_POS[BEACON], RX_POS[1])) / C
    assert abs(beacon_tdoa) * C > 400
    got = groups[0].tdoas['tdoa'][0]
    assert abs(got - _true_tdoa()) * C < 0.1
    assert abs(got - (_true_tdoa() - beacon_tdoa)) * C > 400


def test_too_few_beacons_is_a_failure_not_a_guess():
    detections, matches = _detections()
    # Keep two beacon groups: a quadratic model needs three.
    beacon_groups = [m for m in matches
                     if detections[m[0]].txid == BEACON][:2]
    mobile_groups = [m for m in matches
                     if detections[m[0]].txid == MOBILE]
    groups, failures = tdoa_est.estimate_tdoas(
        detections, beacon_groups + mobile_groups, 0.3, BEACON_POS,
        RX_POS, FS)
    assert groups == []
    assert len(failures) == len(mobile_groups)


def test_receiver_that_never_hears_the_beacon():
    """A receiver out of the beacon's range loses only its own pairs.

    Regression: the beacon extractor indexed a plain dict by receiver
    pair, so the first mobile group with a pair that never heard the
    beacon together raised KeyError and aborted the whole run.
    """
    detections, matches = _detections()
    rx_pos = dict(RX_POS)
    rx_pos[2] = (0.0, 900.0)
    for group in matches:
        det0 = detections[group[0]]
        if det0.txid != MOBILE:
            continue
        t_emit = det0.timestamp - _dist(MOBILE_POS, RX_POS[0]) / C
        arrival = t_emit + _dist(MOBILE_POS, rx_pos[2]) / C
        info = CorrDetectionInfo(0, 0.0, 100.0, 1.0)
        detections.append(DetectionResult(
            arrival, 0, 777.0 + FS * arrival, None, info, rxid=2,
            txid=MOBILE))
        group.append(len(detections) - 1)

    groups, failures = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, rx_pos, FS)
    assert len(groups) == 19
    assert all(g.tdoas[['rx0', 'rx1']].tolist() == [(0, 1)] for g in groups)
    got = np.array([g.tdoas['tdoa'][0] for g in groups])
    np.testing.assert_allclose(got, _true_tdoa(), atol=1e-10)
    # (0, 2) and (1, 2) of every mobile group
    assert len(failures) == 2 * 19


def _shift_soa(detections, matches, txid, nth, samples):
    """Shift rx1's SoA in the *nth* group of *txid* by *samples*."""
    group = [m for m in matches if detections[m[0]].txid == txid][nth]
    det = detections[group[1]]
    detections[group[1]] = DetectionResult(
        det.timestamp, det.block, det.soa + samples, None, det.corr_info,
        rxid=det.rxid, txid=det.txid)
    return group


def test_beacon_outlier_is_left_out_of_the_model():
    """A beacon detection with a bad SoA (a burst paired with the wrong
    one) must not bend the clock model: without the outlier rejection,
    +300 samples at one receiver moved mobile TDOAs by up to 2.8 km."""
    detections, matches = _detections()
    _shift_soa(detections, matches, BEACON, 10, 300)
    groups, failures = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, RX_POS, FS)
    assert failures == []
    got = np.array([g.tdoas['tdoa'][0] for g in groups])
    np.testing.assert_allclose(got, _true_tdoa(), atol=1e-10)


@pytest.mark.parametrize('samples', [2000, -2000])
def test_tdoa_beyond_max_tdoa_is_a_failure(samples):
    """A mobile SoA off by 2000 samples (333 us, 100 km) gives a TDOA no
    receiver pair within MAX_TDOA (30 km) can have: a failure, not a
    group for pos."""
    detections, matches = _detections()
    group = _shift_soa(detections, matches, MOBILE, 5, samples)
    groups, failures = tdoa_est.estimate_tdoas(
        detections, matches, 0.3, BEACON_POS, RX_POS, FS)
    assert failures == [tuple(group)]
    assert len(groups) == 18
    assert all(abs(g.tdoas['tdoa'][0]) < tdoa_est.MAX_TDOA for g in groups)
    got = np.array([g.tdoas['tdoa'][0] for g in groups])
    np.testing.assert_allclose(got, _true_tdoa(), atol=1e-10)
