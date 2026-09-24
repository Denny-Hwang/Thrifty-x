#!/usr/bin/env python
# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only


"""
Merge RX detections, identify transmitter IDs, filter detections.

Merge multiple .toad files, identify transmitter IDs based on carrier
frequency, remove duplicate detections, and output .toads file.
"""


import argparse
from collections import defaultdict, namedtuple
import errno
import glob
import itertools
import logging

import numpy as np

from thriftyx import toads_data, util
from thriftyx.exceptions import ConfigError
from thriftyx.setting_parsers import freq_range
from thriftyx.settings import parse_kvconfig

UNIDENTIFIED_TX = -1

# --map contents: {txid: (start_bin, stop_bin)} and {rxid: bin offset}.
FreqMap = namedtuple('FreqMap', ['tx_ranges', 'rx_offset'])


def detect_transmitter_windows(freqs, verbose=False):
    """Detect transmitter frequency windows automatically.

    Parameters
    ----------
    freqs : :class:`numpy.ndarray`
        Carrier bins of detections.

    Returns
    -------
    edges : :class:`numpy.ndarray`
    """

    first_bin = np.min(freqs)
    cnts = np.bincount(freqs - first_bin)
    last_bin = first_bin + len(cnts)
    low_thresh = np.std(cnts) * 0.4
    high_thresh = np.std(cnts) * 1.25

    peaks = []
    below_thresh = True
    above_thresh_start = None
    for i, cnt in enumerate(cnts):
        if not below_thresh and cnt < low_thresh:
            peaks.append((above_thresh_start, i))
            above_thresh_start = None
            below_thresh = True
        if below_thresh and cnt > high_thresh:
            above_thresh_start = i
            below_thresh = False
    if not below_thresh:
        peaks.append((above_thresh_start, len(cnts) - 1))

    edges = [(peaks[i][1] + peaks[i+1][0]) // 2 for i in range(len(peaks)-1)]
    edges = np.concatenate([[first_bin],
                            np.array(edges) + first_bin,
                            [last_bin]])

    if verbose:
        print("Window threshold: low = {}; high = {}:"
              .format(low_thresh, high_thresh))
        print("Freq bin counts: {} ++ {}".format(first_bin, cnts))
        print("Detected {} transmitter(s):".format(len(edges) - 1))

        for i in range(len(edges) - 1):
            start, stop = edges[i], edges[i+1] - 1
            bins = cnts[start-first_bin:stop-first_bin+1]
            print(" {}: bins {} - {}".format(i, start, stop))
            print("     {}".format(bins))

    return edges


def auto_classify_transmitters(detections):
    """Identify transmitter IDs based on carrier frequency."""
    # Split by receiver
    detections_by_rx = defaultdict(list)
    for detection in detections:
        detections_by_rx[detection.rxid].append(detection)

    edges = {}
    max_tx = 0
    for rxid, rx_detections in detections_by_rx.items():
        freqs = np.array([d.carrier_info.bin for d in rx_detections])
        rx_edges = detect_transmitter_windows(freqs)

        n_tx = len(rx_edges) - 1
        max_tx = max(max_tx, n_tx)
        summary = ("Detected {} transmitter(s) at RX {}:".format(n_tx, rxid))
        for i in range(len(rx_edges) - 1):
            summary += " {}-{}".format(rx_edges[i], rx_edges[i+1] - 1)
        print(summary)

        edges[rxid] = rx_edges[:-1]

    # Soft nudge: the histogram auto-classifier is a convenience for ad-hoc
    # runs. For production captures with a known transmitter count, a manual
    # frequency map (--map) is more robust (e.g. against very uneven per-TX
    # detection populations). See docs/user_guide.md sec 9.2.1.
    if max_tx >= 2:
        logging.warning(
            "auto-classified %d transmitters from the carrier-bin histogram; "
            "for production runs with a known TX count, prefer --map "
            "(see user_guide sec 9.2.1) for more robust classification.",
            max_tx)

    txids = [np.digitize(d.carrier_info.bin, edges[d.rxid]) - 1
             for d in detections]

    return txids


def classify_transmitters(detections, freqmap):
    """Identify transmitter IDs based on the closest nominal frequency."""
    txids = []
    for detection in detections:
        freq = detection.carrier_info.bin + detection.carrier_info.offset
        # A receiver without an '@rxid' line has no LO offset.
        offset = freqmap.rx_offset.get(detection.rxid, 0.0)
        this_txid = UNIDENTIFIED_TX
        for txid, (start, stop) in freqmap.tx_ranges.items():
            if start + offset <= freq <= stop + offset:
                this_txid = txid
        if this_txid == UNIDENTIFIED_TX:
            logging.warning("Failed to classify transmitter for detection "
                            "at freq=%.2f on rxid=%s", freq, detection.rxid)
        txids.append(this_txid)
    return txids


def identify_transmitters(detections, freqmap):
    """
    Identify transmitters and add TX info to detections.
    The DetectionResult object (detections) is changed in-place.
    """

    if freqmap is None:
        txids = auto_classify_transmitters(detections)
    else:
        txids = classify_transmitters(detections, freqmap)

    for i, detection in enumerate(detections):
        detection.txid = txids[i]


def identify_duplicates(detections):
    """
    Returns a mask for filtering duplicate detections for the same transmitter.

    The block prior to or after the full detection may contain a portion of
    positioning signal and also trigger a detection. It is thus necessary
    to remove those "duplicate" detections.
    It is assumed that all detections were captured by the same receiver.

    The mask will exclude unidentified detections.
    """
    array = toads_data.toads_array(detections, with_ids=True)

    # Sort by receiver ID, then transmitter ID, then block ID, then timestamp
    idx = np.argsort(array[['rxid', 'txid', 'block', 'timestamp']])

    cur = array[idx]
    prev = np.roll(cur, 1)
    next_ = np.roll(cur, -1)

    # TODO: only filter if SOA is within code_len
    mask_unidentified = (cur['txid'] == -1)  # FIXME: magic number
    mask_prev = ((cur['block'] == prev['block'] + 1) &
                 (cur['energy'] < prev['energy']))
    mask_next = ((cur['block'] == next_['block'] - 1) &
                 (cur['energy'] < next_['energy']))
    mask = ~(mask_prev | mask_next | mask_unidentified)

    reverse_idx = np.argsort(idx)

    return mask[reverse_idx]


def filter_duplicates(detections):
    """Return detections with duplicates and unidentified detections removed,
    sorted by timestamp."""
    mask = identify_duplicates(detections)
    filtered = list(itertools.compress(detections, mask))
    filtered.sort(key=lambda x: x.timestamp)
    return filtered


def load_toad_files(toad_globs):
    """Load the detections of every file matching one of *toad_globs*.

    A pattern that matches no file is an error: a mistyped receiver file
    would otherwise just drop that receiver from the run.
    """
    filenames = []
    for toad_glob in toad_globs:
        matched = sorted(glob.glob(toad_glob))
        if not matched:
            raise FileNotFoundError(errno.ENOENT, "no file matches",
                                    toad_glob)
        filenames.extend(matched)
    filenames = list(dict.fromkeys(filenames))   # each file once

    detections = []
    spans = defaultdict(list)   # rxid -> [(first, last timestamp, file)]
    for filename in filenames:
        with open(filename, 'r') as file_:
            file_detections = toads_data.load_toad(file_)
        detections.extend(file_detections)
        timestamps = defaultdict(list)
        for detection in file_detections:
            timestamps[detection.rxid].append(detection.timestamp)
        for rxid, times in timestamps.items():
            spans[rxid].append((min(times), max(times), filename))
    _check_rxids(spans)

    return detections, filenames


def _check_rxids(spans):
    """Warn when two files hold detections of one rxid over the same time.

    One receiver's files (hourly --rotate captures, say) follow each
    other; files that overlap come from different receivers left at the
    same rxid, and `match` would then find nothing to pair.
    """
    for rxid, rx_spans in spans.items():
        rx_spans.sort()
        last_stop, last_file = rx_spans[0][1], rx_spans[0][2]
        for first, stop, filename in rx_spans[1:]:
            if first < last_stop:
                logging.warning(
                    "%s and %s both hold rxid %d detections from the same "
                    "period: give every receiver its own rxid (detect "
                    "--rxid N, or rxid: in its detector.cfg), the id of "
                    "its line in pos-rx.cfg", last_file, filename, rxid)
                break
            if stop > last_stop:
                last_stop, last_file = stop, filename


def load_freqmap(file_):
    """Load an ``identify --map`` file.

    ``txid: start - stop`` lines give each transmitter's carrier range in
    FFT bins (the ``carrier_bin`` column of the .toad files); optional
    ``@rxid: offset`` lines shift the ranges for one receiver, whose
    offset is 0 without one.
    """
    if file_ is None:
        return None
    strings = parse_kvconfig(file_)
    name = getattr(file_, 'name', 'frequency map')

    tx_ranges = {}
    rx_offset = {}

    for key, value in strings.items():
        try:
            if key.startswith('@'):
                rx_offset[int(key[1:])] = float(value)
                continue
            txid = int(key)
            start, stop, unit_hz = freq_range(value)
        except ValueError:
            raise ConfigError(
                "{}: invalid line '{}: {}' (expected 'txid: start - stop' "
                "in FFT bins, or '@rxid: offset')".format(
                    name, key, value)) from None
        if unit_hz:
            # identify knows neither the sample rate nor the block size
            # that would convert Hz to bins.
            raise ConfigError(
                "{}: '{}: {}': give the range in FFT bins, not Hz "
                "(bin = Hz * block_size / sample_rate)".format(
                    name, key, value))
        tx_ranges[txid] = (start, stop)
        # TODO: ensure that ranges do not overlap

    return FreqMap(tx_ranges, rx_offset)


def integrate(detections, freqmap=None):
    """Identify and filter."""
    identify_transmitters(detections, freqmap)
    filtered = filter_duplicates(detections)
    return filtered


def generate_toads(output, toad_globs, freqmap):
    """Identify and filter the detections of *toad_globs* and write them
    to the file *output* (``'-'`` for stdout)."""
    with util.info_to_stderr(output):
        detections, filenames = load_toad_files(toad_globs)
        filtered = integrate(detections, freqmap)

        print("Removed {} duplicates / unidentified transmissions "
              "from {} detections.".format(len(detections)-len(filtered),
                                           len(detections)))

    # Opened only now, so a failed run leaves an earlier output intact.
    with util.open_output(output) as out:
        out.write("# source_files: [%s]\n" % (' '.join(filenames)))
        for detection in filtered:
            out.write(detection.serialize() + '\n')


def _main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('toad_file', type=str, nargs='*', default=['*.toad'],
                        help="toad file(s) from receivers [default: *.toad]")
    parser.add_argument('-o', '--output', default='data.toads',
                        help="output file ('-' for stdout) "
                             "[default: data.toads]")
    parser.add_argument('-m', '--map', type=argparse.FileType('r'),
                        help="schema for mapping DFT index to transmitter ID "
                             "[default: auto-detect]")
    args = parser.parse_args()

    freqmap = load_freqmap(args.map)
    generate_toads(args.output, args.toad_file, freqmap)


if __name__ == "__main__":
    _main()
