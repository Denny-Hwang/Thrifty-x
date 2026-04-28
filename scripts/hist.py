#!/usr/bin/env python

"""
Calculate the histogram over multiple blocks of data.

Examples:
    # RTL-SDR (8-bit, default):
    rtl_sdr -f 433.83M -s 2.4M -g 55 data.bin
    hist.py data.bin

    # Airspy (12-bit) raw I/Q file:
    hist.py --bit-depth 12 raw_int16.bin
"""

import argparse

import numpy as np

from thriftyx import settings
from thriftyx.block_data import block_reader, complex_to_raw


def _main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', nargs='?',
                        type=argparse.FileType('rb'), default='-',
                        help="input data ('-' streams from stdin)")
    parser.add_argument('-i', '--integrate', type=int, default=100,
                        help="Number of blocks to integrate over")
    setting_keys = ['block_size', 'block_history', 'bit_depth']
    config, args = settings.load_args(parser, setting_keys)

    bit_depth = int(config.get('bit_depth', 8))
    blocks = block_reader(args.input, config.block_size, config.block_history,
                          bit_depth=bit_depth)

    if bit_depth == 12:
        hist_nbins = 256
        hist_range = (-32768, 32768)
    else:
        hist_nbins = 256
        hist_range = (0, 256)
    hist_sum = np.zeros(hist_nbins)

    cnt = 0

    for _, _, block in blocks:
        samples = complex_to_raw(block, bit_depth=bit_depth)
        counts, _ = np.histogram(samples, bins=hist_nbins, range=hist_range)
        hist_sum += counts

        cnt += 1
        if cnt == args.integrate:
            print(hist_sum)
            hist_sum = np.zeros(hist_nbins)
            cnt = 0


if __name__ == '__main__':
    _main()
