#!/usr/bin/env python
# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only


"""
Estimate position from TDOA values.
"""


import scipy.optimize
import numpy as np

from thriftyx import exceptions
from thriftyx import tdoa_est
from thriftyx import util

SPEED_OF_LIGHT = tdoa_est.SPEED_OF_LIGHT

POSITION_INFO_DTYPE = {
    'names': ('group_id', 'timestamp', 'tx', 'dop', 'snr', 'x', 'y', 'z'),
    'formats': ('i4', 'f8', 'i4', 'f8', 'f8', 'f8', 'f8', 'f8')
}
# TODO: split DOP into multiple values (one per dimension)

MAX_DIST = 10e3

# solve_numerically tries the starts beyond the receivers only for a
# first fit within NEAR_RX of a receiver (a fraction of the array's
# radius: in the layouts tried, fits stuck in a corner receiver's cusp
# were within 0.075 of it) or outside the receivers' bounding box grown
# by BOX_MARGIN of its size.  A fit from such a start replaces the first
# only if its cost (half the sum of squared residuals, in m^2) is below
# OUTER_START_COST_RATIO * cost - OUTER_START_COST_MARGIN.
NEAR_RX = 0.1
BOX_MARGIN = 0.1
OUTER_START_COST_RATIO = 0.25
OUTER_START_COST_MARGIN = 1.0


class EstimationError(exceptions.EstimationError):
    pass


def solve_1d(tdoa_array, rx_pos):
    """Simple 1D position estimator for 2xRX."""
    if len(rx_pos) != 2:
        raise EstimationError(
            f"solve_1d requires exactly 2 receivers, got {len(rx_pos)}")
    if len(tdoa_array) != 1:
        raise EstimationError(
            f"solve_1d requires exactly 1 TDOA, got {len(tdoa_array)}")
    # The TDOA is d(rx0) - d(rx1) for the receivers its row names, not
    # for pos-rx.cfg's first and second lines.
    rx0, rx1 = int(tdoa_array['rx0'][0]), int(tdoa_array['rx1'][0])
    if rx0 not in rx_pos or rx1 not in rx_pos:
        raise EstimationError(
            f"no coordinates for receiver pair ({rx0}, {rx1})")
    if len(rx_pos[rx0]) != 1:
        raise EstimationError("solve_1d requires 1D receiver positions")

    tdoa_pos = tdoa_array['tdoa'][0] * SPEED_OF_LIGHT
    # Receiver positions are 1-element sequences; use scalars so the
    # result is a plain coordinate (an array here cannot be packed into
    # the structured result array under NumPy 2).
    pos0, pos1 = float(rx_pos[rx0][0]), float(rx_pos[rx1][0])
    rx_dist = pos0 + pos1
    if pos0 > pos1:
        position = (rx_dist - tdoa_pos) / 2
    else:
        position = (rx_dist + tdoa_pos) / 2

    return (position,), tdoa_array['snr'][0]


def solve_numerically(tdoa_array, rx_pos):
    """Solve position using the Levenberg-Marquardt minimization algorithm."""
    # TODO: use analytic solution or previous position as initial value
    # TODO: experiment with different algorithms
    # TODO: use SNR or some confidence value as weight

    first_rx = next(iter(rx_pos))
    dims = len(rx_pos[first_rx])
    uniq_rx = np.unique(np.concatenate([tdoa_array['rx0'], tdoa_array['rx1']]))
    if len(uniq_rx) < dims + 1:
        # With z in pos-rx.cfg the tag's height is unknown too.
        raise EstimationError(
            "Underdetermined: a {}-D position needs TDOAs from at least {} "
            "receivers, got {}".format(dims, dims + 1, len(uniq_rx)))

    rx_coords = np.array(list(rx_pos.values()))
    min_bounds = np.amin(rx_coords, axis=0) - MAX_DIST
    max_bounds = np.amax(rx_coords, axis=0) + MAX_DIST

    unknown = set(uniq_rx.tolist()) - set(rx_pos)
    if unknown:
        raise EstimationError("no coordinates for receiver(s) {}".format(
            ', '.join(map(str, sorted(unknown)))))
    rx0 = np.array([rx_pos[rxid] for rxid in tdoa_array['rx0']])
    rx1 = np.array([rx_pos[rxid] for rxid in tdoa_array['rx1']])

    def model(pos):
        # position relative to {rx0, rx1}
        pos_rx0, pos_rx1 = rx0 - pos, rx1 - pos
        # distance to {rx0, rx1}
        dist0 = np.linalg.norm(pos_rx0, axis=1)
        dist1 = np.linalg.norm(pos_rx1, axis=1)
        # predicted TDOA (in m)
        predicted_tdoa = dist0 - dist1

        residuals = tdoa_array['tdoa'] * SPEED_OF_LIGHT - predicted_tdoa
        return residuals

    def jac(pos):
        return _unit_vectors(rx0 - pos) - _unit_vectors(rx1 - pos)

    def solve_from(x0):
        return scipy.optimize.least_squares(
            model, x0, jac=jac, bounds=(min_bounds, max_bounds))

    def in_bounds(x0):
        return np.all(x0 >= min_bounds) and np.all(x0 <= max_bounds)

    # The solver stops in the local minimum nearest its start, so solve
    # from two starts and keep the better fit: near the origin and at the
    # receivers' centroid, which is inside the bounds even for receivers
    # more than MAX_DIST from the origin (e.g. UTM coordinates).  The 0.1
    # offsets keep a start off a receiver.
    centroid = np.mean(rx_coords, axis=0)
    res = min((solve_from(x0) for x0 in (np.full(dims, 0.1), centroid + 0.1)
               if in_bounds(x0)), key=lambda fit: fit.cost)

    # From inside the array, the path to a tag behind a corner receiver
    # ends in that receiver's cusp, up to hundreds of metres off, so a fit
    # near a receiver or outside the array is tried again from just beyond
    # each receiver.  Such a start only wins by fitting clearly better:
    # for a tag inside a 3-D array, or a 3-receiver one, it can find the
    # mirror solution, which fits (about) as well as the true position.
    if _near_receiver_or_outside(res.x, rx_coords, centroid):
        for rx in rx_coords:
            x0 = rx + 0.5 * (rx - centroid) + 0.1
            if not in_bounds(x0):
                continue
            candidate = solve_from(x0)
            if candidate.cost < (OUTER_START_COST_RATIO * res.cost
                                 - OUTER_START_COST_MARGIN):
                res = candidate

    # TODO: also return residual or a measure of the quality or confidence of
    #       the estimate

    snr_mean = np.mean(tdoa_array['snr'])

    return res.x, snr_mean


def _unit_vectors(vectors):
    """Rows of *vectors* scaled to unit length; a zero row stays zero.

    These are the gradients of the distances to the receivers.  At a
    receiver the gradient is undefined, and 0/0 made the Jacobian NaN:
    in 1-D, where a Gauss-Newton step often lands exactly on a receiver,
    scipy then raised ValueError and aborted `pos`.  Zero (a subgradient)
    lets the solver move on.
    """
    norms = np.linalg.norm(vectors, axis=1)[:, None]
    return vectors / np.where(norms > 0, norms, 1.0)


def _near_receiver_or_outside(pos, rx_coords, centroid):
    """Whether *pos* is within NEAR_RX of a receiver (as a fraction of the
    array's radius) or outside the receivers' bounding box grown by
    BOX_MARGIN of its size in every dimension."""
    radius = np.max(np.linalg.norm(rx_coords - centroid, axis=1))
    if np.min(np.linalg.norm(rx_coords - pos, axis=1)) < NEAR_RX * radius:
        return True
    low, high = np.amin(rx_coords, axis=0), np.amax(rx_coords, axis=0)
    margin = BOX_MARGIN * (high - low)
    return bool(np.any(pos < low - margin) or np.any(pos > high + margin))


def dop_matrix(pos, rx_pos, rx_pairs):
    pos = np.array(pos)
    rx0 = np.array([np.array(rx_pos[rxid]) for rxid, _ in rx_pairs])
    rx1 = np.array([np.array(rx_pos[rxid]) for _, rxid in rx_pairs])
    G = _unit_vectors(rx0 - pos) - _unit_vectors(rx1 - pos)
    H_inv = G.T.dot(G)
    try:
        H = np.linalg.inv(H_inv)
    except np.linalg.LinAlgError:
        H = None
    return H


def dop(pos, rx_pos, rx_pairs):
    rx_pairs = list(rx_pairs)
    matrix = dop_matrix(pos, rx_pos, rx_pairs)
    if matrix is None:
        return -1
    return np.sqrt(np.trace(matrix))


def solve(tdoa_groups, rx_pos):
    # TODO: estimate confidence with SNR and DOP and return with pos
    num_rx = len(rx_pos)
    first_rx = next(iter(rx_pos))
    dimensions = len(rx_pos[first_rx])

    results = []
    for group_id, timestamp, tx, tdoas in tdoa_groups:
        try:
            if num_rx == 2 and dimensions == 1:
                coords, snr = solve_1d(tdoas, rx_pos)
            else:
                coords, snr = solve_numerically(tdoas, rx_pos)
            rx_pairs = zip(tdoas['rx0'], tdoas['rx1'], strict=True)
            dop_est = dop(coords, rx_pos, rx_pairs)
            results.append((group_id, timestamp, tx, dop_est, snr) +
                           tuple(coords))
            # print(tx, dop_est, snr, coords)
        except EstimationError as e:
            print("Failed to estimate group #{}: {}".format(group_id, e))

    # TODO: apply Kalmin filter or something to average out the position
    #       estimates (move to separate module)

    dtype = {
        'names': POSITION_INFO_DTYPE['names'][:5 + dimensions],
        'formats': POSITION_INFO_DTYPE['formats'][:5 + dimensions]
    }
    results = np.array(results, dtype=dtype)
    return results


def save_positions(output, results):
    for position in results:
        fields = list(position)
        fields[1] = "{:.6f}".format(fields[1])  # format timestamp
        print(*fields, file=output)


def load_positions(fname):
    num_fields = len(np.genfromtxt(fname, max_rows=1))  # FIXME
    dtype = {
        'names': POSITION_INFO_DTYPE['names'][:num_fields],
        'formats': POSITION_INFO_DTYPE['formats'][:num_fields]
    }
    data = np.genfromtxt(fname, dtype=dtype)
    return np.atleast_1d(data)   # a one-row file gives a 0-d array


def _main():
    import argparse

    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=formatter)

    parser.add_argument('tdoa', nargs='?',
                        type=argparse.FileType('r'), default='data.tdoa',
                        help="tdoa data (\"-\" streams from stdin)")
    parser.add_argument('-o', '--output', dest='output', default='data.pos',
                        help="output file (\'-\' for stdout)")
    parser.add_argument('-r', '--rx-coordinates', dest='rx_pos',
                        type=argparse.FileType('r'), default='pos-rx.cfg',
                        help="path to config file that contains the "
                             "coordinates of the receivers")
    args = parser.parse_args()

    with util.info_to_stderr(args.output):
        tdoa_groups = tdoa_est.load_tdoa_groups(args.tdoa)
        rx_pos = tdoa_est.load_pos_config(args.rx_pos)
        results = solve(tdoa_groups, rx_pos)

    # Opened only now, so a failed run leaves an earlier output intact.
    with util.open_output(args.output) as output:
        save_positions(output, results)


if __name__ == '__main__':
    _main()
