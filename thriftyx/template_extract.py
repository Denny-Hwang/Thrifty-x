# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""
Extract a template from captured data.

Usage example:

    Capture data:
    $ thriftyx capture rx1.card

    Find which code the transmitter sends (no template needed):
    $ thriftyx gold --identify rx1.card

    Generate a "base template" for that code to be able to extract the
    code signal, e.g. for the 11-bit code 0:
    $ thriftyx template_generate 11 0 -o theoretical-template.npy

    Extract the new template:
    $ thriftyx template_extract rx1.card --template=theoretical-template.npy \\
                                         -o captured-template.npy

"""


import argparse
import contextlib
import errno
import os
import shutil
import sys

import numpy as np

from thriftyx import detect
from thriftyx import settings
from thriftyx.exceptions import DetectionError
from thriftyx.setting_parsers import normalize_freq_range


MAX_OFFSET = 0.2


def _with_neighbours(detections):
    """Yield ``(result, fft, previous, next)`` for each detected block,
    with the results of the detections before and after it (or None).

    One detection is held back at a time, not every candidate's FFT.
    """
    previous = current = None
    for detected, result, fft, _ in detections:
        if not detected:
            continue
        if current is not None:
            yield current + (previous, result)
            previous = current[0]
        current = (result, fft)
    if current is not None:
        yield current + (previous, None)


def _is_partial(result, previous, next_):
    """Whether *result* is the part of a burst that the block before or
    after it holds whole: that block has a stronger detection.

    Such a partial is at a few percent of the burst's energy and on a
    misaligned lag, so a template cut there starts part-way into the
    code.  identify.py drops the same duplicates.  A burst from a weaker
    transmitter elsewhere in the capture is still complete.
    """
    return any(other is not None
               and abs(other.block - result.block) == 1
               and other.corr_info.energy > result.corr_info.energy
               for other in (previous, next_))


def best_detection(detections, max_offset):
    """Get block with largest corr peak and offset less than `max_offset`.

    Only a complete burst qualifies, not the partial detection a burst
    leaves in the block before or after it (see `_is_partial`).
    """
    best_result = None
    best_fft = None
    partial = None  # the strongest candidate that was a partial

    for result, fft, previous, next_ in _with_neighbours(detections):
        if abs(result.corr_info.offset) > max_offset:
            continue
        if _is_partial(result, previous, next_):
            if (partial is None or
                    result.corr_info.energy > partial.corr_info.energy):
                partial = result
        elif (best_result is None or
                result.corr_info.energy > best_result.corr_info.energy):
            best_result = result
            best_fft = fft

    if best_result is None and partial is not None:
        raise DetectionError(
            "no complete burst peaked within {} samples of a whole "
            "sample: the best block with |offset| <= {} (#{}) holds only "
            "part of a burst, which a stronger detection in the block "
            "before or after it holds whole.  Capture for longer (e.g. "
            "--duration 30) to get more bursts".format(
                max_offset, max_offset, partial.block))
    if best_result is None:
        raise DetectionError(
            "no block had a correlation detection with |offset| <= {}; "
            "check the carrier window/thresholds and that the base "
            "template holds the transmitter's code (register length, "
            "index and, for 8 and 10 bits, --family) at the data's sample "
            "rate: `thriftyx gold --identify CAPTURE.card` reports the "
            "code".format(max_offset))
    best_signal = np.fft.ifft(best_fft)
    return best_signal, best_result


def extract_template(signal, result, template_len):
    """Extract template from detection with OOK signal."""
    start = result.corr_info.sample
    cut = np.abs(signal[start:start+template_len])
    cut *= 2 / (np.mean(cut) + np.std(cut))
    cut = cut - np.mean(cut)  # OOK -> bipolar signal
    return cut


def _check_output_dir(path):
    """Fail before the extraction, not after it, if *path*'s directory
    is missing."""
    if not os.path.isdir(os.path.dirname(os.path.realpath(path))):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),
                                path)


def _save_replacing(path, array):
    """Save *array* to *path* (.npy) only once it is complete.

    Written next to *path* and renamed over it, so a failure leaves an
    existing file -- often the working template -- as it was.  A
    symlink is written through, and an existing file keeps its mode.
    """
    target = os.path.realpath(path)
    tmp = '{}.{}.tmp'.format(target, os.getpid())
    try:
        with open(tmp, 'wb') as output:
            np.save(output, array)
        if os.path.exists(target):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        if isinstance(exc, OSError) and exc.filename == tmp:
            exc.filename = path  # the user's name, not the temporary one
        raise


def plot(signal, template, offset):
    """Plot the newly captured template and the base template."""
    import matplotlib.pyplot as plt

    xdata = np.arange(len(template))
    plt.plot(xdata, signal, '.-', label='New')
    plt.plot(xdata - offset, template, '.-', label='Base')
    plt.savefig('extract.pdf', format='pdf')
    plt.legend()
    plt.show()


def _main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('input',
                        type=argparse.FileType('rb'), default='-',
                        help="input data ('-' streams from stdin)")
    # A path, not an open file: argparse would truncate it before the
    # template (possibly the same file) is even read.
    parser.add_argument('-o', '--output', default='capture.npy',
                        help="Output file (.npy; '-' for stdout)")
    parser.add_argument('-p', '--plot', action='store_true',
                        help="Plot base template and extracted template.")

    setting_keys = ['device_type', 'sample_rate', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'corr_threshold', 'template', 'bit_depth',
                    'freq_shift_method', 'soa_interpolation']
    config, args = settings.load_args(parser, setting_keys)
    if args.output != '-':
        _check_output_dir(args.output)
    blocks, config = detect.open_card(args.input, config)

    bin_freq = config.sample_rate / config.block_size
    window = normalize_freq_range(config.carrier_window, bin_freq)
    template = detect.load_template(config.template, config.sample_rate,
                                    config.get('chip_rate'))

    dsettings = detect.DetectorSettings(
        block_len=config.block_size,
        history_len=config.block_history,
        carrier_len=len(template),
        carrier_thresh=config.carrier_threshold,
        carrier_window=window,
        template=template,
        corr_thresh=config.corr_threshold,
        freq_shift_method=config.freq_shift_method,
        soa_interpolation=config.soa_interpolation,
        )
    detections = detect.Detector(dsettings, blocks, yield_data=True)

    full_signal, result = best_detection(detections, MAX_OFFSET)
    signal = extract_template(full_signal, result, len(template))

    info_out = sys.stdout
    if args.output == '-':
        np.save(sys.stdout.buffer, signal)
        info_out = sys.stderr
    else:
        _save_replacing(args.output, signal)
    print("Captured template from block #{} (timestamp: {:.6f}): "
          "offset={:+.3f}; corr_ampl={}".format(result.block,
                                                result.timestamp,
                                                result.corr_info.offset,
                                                result.corr_info.energy),
          file=info_out)
    if args.plot:
        plot(signal, template, result.corr_info.offset)


if __name__ == '__main__':
    _main()
