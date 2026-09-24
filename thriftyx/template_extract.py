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

import numpy as np

from thriftyx import detect
from thriftyx import settings
from thriftyx.exceptions import DetectionError
from thriftyx.setting_parsers import normalize_freq_range


MAX_OFFSET = 0.2


def best_detection(detections, max_offset):
    """Get block with largest corr peak and offset less than `max_offset`."""
    best_result = None
    best_fft = None

    for detected, result, fft, _ in detections:
        if detected and abs(result.corr_info.offset) <= max_offset:
            if (best_result is None or
                    result.corr_info.energy > best_result.corr_info.energy):
                best_result = result
                best_fft = fft

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
    parser.add_argument('-o', '--output', type=argparse.FileType('wb'),
                        default='capture.npy', help="Output file (.npy)")
    parser.add_argument('-p', '--plot', action='store_true',
                        help="Plot base template and extracted template.")

    setting_keys = ['device_type', 'sample_rate', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'corr_threshold', 'template', 'bit_depth',
                    'freq_shift_method', 'soa_interpolation']
    config, args = settings.load_args(parser, setting_keys)
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

    np.save(args.output, signal)
    print("Captured template from block #{} (timestamp: {:.6f}): "
          "offset={:+.3f}; corr_ampl={}".format(result.block,
                                                result.timestamp,
                                                result.corr_info.offset,
                                                result.corr_info.energy))
    if args.plot:
        plot(signal, template, result.corr_info.offset)


if __name__ == '__main__':
    _main()
