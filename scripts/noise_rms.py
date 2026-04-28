#!/usr/bin/env python

"""
Calculate the root mean square value for blocks of data.

Examples:
    # RTL-SDR (8-bit, default):
    rtl_sdr -f 433.83M -s 2.4M -g 55 - | noise_rms.py

    # Airspy (12-bit) raw I/Q file:
    noise_rms.py --bit-depth 12 raw_int16.bin
"""

import argparse

import numpy as np

from thriftyx import settings
from thriftyx.block_data import block_reader


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
    rmss = []
    for _, _, block in blocks:
        rms = np.sqrt(np.sum(block * block.conj)).real
        rmss.append(rms)
        if len(rmss) == args.integrate:
            print(np.mean(rmss))
            rmss = []


if __name__ == '__main__':
    _main()
