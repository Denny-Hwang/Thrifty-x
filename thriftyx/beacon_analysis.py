#!/usr/bin/env python
# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only


"""
Analyze the difference in SOA of a beacon between two receivers.
"""


import numpy as np
import matplotlib.pyplot as plt
import scipy.ndimage

from thriftyx import matchmaker
from thriftyx import tdoa_est
from thriftyx import toads_data
from thriftyx.exceptions import EstimationError
from thriftyx.setting_parsers import metric_float


SPEED_OF_LIGHT = 2.997e8


def plot(soa0, residuals, discontinuities, sample_rate, avg_snr=None):
    """Plot and print the residuals (in samples of *sample_rate*) in m."""
    s2m = SPEED_OF_LIGHT / sample_rate

    if avg_snr is None:
        avg_snr_db = float('nan')
    else:
        avg_snr_db = 10 * np.log10(avg_snr)

    print("residuals: std dev = {:.01f} m; max = {:.01f} m; "
          "avg corr snr = {:.01f}"
          .format(np.std(residuals) * s2m,
                  np.max(np.abs(residuals)) * s2m,
                  avg_snr_db))

    plt.figure(figsize=(11, 6))
    plt.subplot(1, 2, 1)
    plt.plot(soa0, residuals * s2m, '.-')
    plt.title("Residuals")
    plt.xlabel("RX sample")
    plt.ylabel("Residual (m)")
    plt.grid()
    # plt.ylim([-0.5, 0.5])

    for discontinuity in discontinuities:
        plt.axvline(discontinuity, color='k')

    plt.subplot(1, 2, 2)
    plt.hist(residuals * s2m, 20)
    plt.title("Histogram: residuals")
    plt.xlabel("Residual (m)")
    plt.grid()

    plt.suptitle("Clock sync (stddev = {:.01f} m; max = {:.01f} m; "
                 "avg corr SNR: {:.01f} dB)"
                 .format(np.std(residuals) * s2m,
                         np.max(np.abs(residuals)) * s2m,
                         avg_snr_db))

    plt.tight_layout()
    plt.subplots_adjust(top=0.90)


def find_discontinuities(soa):
    """Indices i where the SDOA jumps between beacon match i and i+1.

    Between two beacon transmissions the SDOA changes by the time between
    them times the receivers' relative clock rate, which has either sign
    (whichever clock is faster), drifts slowly and is about zero for
    coherent clocks; a beacon missed by either receiver doubles the
    change.  A step that departs from the local rate by more than the
    noise (10 MADs, and at least one sample) is a jump in one receiver's
    sample count.

    Parameters
    ----------
    soa : (N, 2) array
        SoAs of the beacon at the two receivers, in time order.
    """
    soa = np.asarray(soa, dtype=float)
    dsdoa = np.diff(soa[:, 1] - soa[:, 0])
    dsoa0 = np.diff(soa[:, 0])
    if len(dsdoa) == 0:
        return np.array([], dtype=int)
    with np.errstate(divide='ignore', invalid='ignore'):
        rate = dsdoa / dsoa0
    local_rate = scipy.ndimage.median_filter(rate, size=9, mode='mirror')
    deviation = np.abs(dsdoa - local_rate * dsoa0)
    noise = 1.4826 * np.median(deviation)
    return np.where(deviation > max(10 * noise, 1.0))[0]


def analyze(detections, matches, sample_rate, deg=2):
    """
    Parameters
    ---------
    detections: detection array
    matches:    matches of beacon transmissions of the two receivers
    sample_rate: the receivers' nominal sample rate, for figures in m
    """

    print("Number of detection groups:", len(matches))
    if len(matches) < 2:
        raise EstimationError("fewer than two beacon transmissions matched "
                              "between the two receivers")

    soa = detections['soa'][matches]
    discontinuities = find_discontinuities(soa)

    print("Number of discontinuities:", np.size(discontinuities))
    print("Discontinuities (index):", " ".join(map(str, discontinuities)))
    discont_ids = detections[matches]['idx'][discontinuities]
    print("Discontinuities (toad id):", " ".join(map(str, discont_ids)))

    all_soas = []
    all_residuals = []
    all_avg_snr = []
    all_coefs = []
    discontinuity_soas = []

    edges = np.concatenate([[0], discontinuities, [len(matches)]])
    for i in range(len(edges) - 1):
        left, right = edges[i] + 1, edges[i + 1]
        if left + 8 >= right:
            # range too small
            continue

        cut = detections[matches][left:right]
        coef, residuals = fit_poly_model(cut['soa'], deg)
        # outliers = stat_tools.is_outlier(residuals)
        outliers = np.zeros(len(residuals), dtype=bool)
        filtered_cut = cut[~outliers]

        energy = filtered_cut['energy']
        noise = filtered_cut['noise']
        avg_snr = np.mean(energy**2 / noise**2)

        all_soas.append(filtered_cut['soa'])
        all_residuals.append(residuals[~outliers])
        all_avg_snr.append(avg_snr)
        all_coefs.append(coef)

        if right != len(matches):
            discontinuity_soas.append(soa[right, 0])
            # print(soa[right, 0], '\t', soa[right, 1], '\t', dsdoa[right])

        print('Cut #{}: {} outliers'.format(i+1, np.sum(outliers)))

    if not all_soas:
        raise EstimationError("no run of more than 8 matched beacon "
                              "transmissions between discontinuities; "
                              "nothing to fit")
    soas = np.concatenate(all_soas)
    residuals = np.concatenate(all_residuals)
    avg_snr = np.mean(all_avg_snr)
    plot(soas[:, 0], residuals, discontinuity_soas, sample_rate, avg_snr)

    return all_coefs


def fit_poly_model(soa, deg=2):
    soa0, soa1 = soa[:, 0], soa[:, 1]
    coef = np.polyfit(soa0, soa1, deg)
    fit = np.poly1d(coef)
    residuals = soa1 - fit(soa0)

    return coef, residuals


def parse_range(string):
    if string is None:
        return None
    a, b = string.split('-')
    a, b = int(a), int(b)
    return a, b


def _main():
    import argparse

    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=formatter)

    parser.add_argument('toads', nargs='?',
                        type=argparse.FileType('r'), default='data.toads',
                        help="toads data (\"-\" streams from stdin)")
    parser.add_argument('matches', nargs='?',
                        type=argparse.FileType('r'), default='data.match',
                        help="match data (\"-\" streams from stdin)")
    parser.add_argument('--beacon', type=int, default=0,
                        help="transmitter ID of beacon")
    parser.add_argument('--rx0', type=int, default=0,
                        help="receiver ID of first receiver")
    parser.add_argument('--rx1', type=int, default=1,
                        help="receiver ID of second receiver")
    parser.add_argument('--range', type=parse_range, default=None,
                        help="limit to a range of detection IDs")
    parser.add_argument('--deg', type=int, default=2,
                        help="degree of polynomial to fit through the SOAs")
    parser.add_argument('--export', type=str, nargs='?',
                        const=True,
                        help="export plot to a .PDF file")
    parser.add_argument('-s', '--sample-rate', dest='sample_rate',
                        type=metric_float, default=None,
                        help="nominal sample rate of the receivers in Hz "
                             "(e.g. 2.4M, 6M, 10M), for the residuals in "
                             "metres. If omitted, reads from detector.cfg "
                             "(sample_rate or device_type).")
    parser.add_argument('-c', '--config', dest='config', default=None,
                        help="settings config file to read sample_rate from "
                             "[default: detector.cfg]")

    args = parser.parse_args()
    sample_rate = tdoa_est._resolve_sample_rate(args.sample_rate, args.config)

    toads = toads_data.load_toads(args.toads)
    detections = toads_data.toads_array(toads, with_ids=True)

    all_matches = matchmaker.load_matches(args.matches)

    matches = matchmaker.extract_match_matrix(toads, all_matches,
                                              [args.rx0, args.rx1],
                                              [args.beacon])
    if args.range is not None:
        start, stop = args.range
        matches = [m for m in matches
                   if (m[0] >= start and m[0] <= stop and
                       m[1] >= start and m[1] <= stop)]
    matches = np.array(matches)

    analyze(detections, matches, sample_rate, deg=args.deg)

    if args.export:
        if args.export is True:
            filename = 'beacon_analysis_beacon{}_rx{}_rx{}_deg{}'.format(
                       args.beacon, args.rx0, args.rx1, args.deg)
            if args.range is not None:
                filename += '_{}-{}'.format(args.range[0], args.range[1])
            filename += '.pdf'
        else:
            filename = args.export + '.pdf'

        plt.savefig(filename, format='pdf')

    plt.show()

if __name__ == '__main__':
    _main()
