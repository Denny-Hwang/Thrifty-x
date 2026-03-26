# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Combine all the modules."""


from collections import namedtuple

import logging

import numpy as np

from thriftyx.block_data import card_reader
from thriftyx.detect import Detector
from thriftyx import identify
from thriftyx import matchmaker
from thriftyx import tdoa_est
from thriftyx import pos_est


DEFAULT_DETECTOR = Detector  # pylint: disable=invalid-name
DEFAULT_INTEGRATOR = identify.integrate
DEFAULT_MATCHER = matchmaker.match_toads
DEFAULT_TDOA_EST = tdoa_est.estimate_tdoas
DEFAULT_POS_EST = pos_est.solve


PostdetectSettings = namedtuple('PostdetectSettings', [
    'tx_freqs', 'match_window', 'tdoa_est_window',
    'rx_pos', 'beacon_pos', 'sample_rate'])

PostdetectResult = namedtuple('PostdetectResult', [
    'toads', 'matches', 'tdoas', 'pos'])


def patch_module(module, **override):
    """Override a module's parameters."""
    def _patched_module(*args, **kwargs):
        kwargs.update(override)
        return module(*args, **kwargs)
    return _patched_module


def detect_all(cards, settings, detector=DEFAULT_DETECTOR):
    """Detect positioning signal and estimate SOA."""
    toad = []
    for rxid, card in cards.items():
        logging.info(" * Detect: RX #%d (%s)", rxid, card)
        blocks = card_reader(open(card, 'r'))
        det = detector(settings, blocks, rxid=rxid)
        toad.extend([result for detected, result in det if detected])
    return toad


def postdetect(toad, settings,
               integrator=DEFAULT_INTEGRATOR,
               matcher=DEFAULT_MATCHER,
               tdoa_estimator=DEFAULT_TDOA_EST,
               pos_estimator=DEFAULT_POS_EST):
    """Identify, match, estimate TDOA, estimate position."""

    # Identify transmitters and remove duplicates
    logging.info(" * Integrate")
    toads = integrator(toad, freqmap=settings.tx_freqs)

    # Match detections
    logging.info(" * Match")
    matches, _, _ = matcher(toads, settings.match_window)

    # Estimate TDOAs
    logging.info(" * TDOA estimate")
    beacon_pos = {k: np.array(v) for k, v in settings.beacon_pos.items()}
    rx_pos = {k: np.array(v) for k, v in settings.rx_pos.items()}
    tdoas, _ = tdoa_estimator(detections=toads,
                              matches=matches,
                              window_size=settings.tdoa_est_window,
                              beacon_pos=beacon_pos,
                              rx_pos=rx_pos,
                              sample_rate=settings.sample_rate)

    # Estimate positions
    logging.info(" * Positions estimate")
    pos = pos_estimator(tdoas, rx_pos)

    return PostdetectResult(toads=toads,
                            matches=matches,
                            tdoas=tdoas,
                            pos=pos)
