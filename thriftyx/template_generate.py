# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""
Generate a code template by sampling the ideal code signal.

The code is the given register length and index of the Gold family
(thriftyx.gold), or of the older non-Gold 8/10-bit codes with
--family legacy.  It must be the code the transmitters send: check a
capture with `thriftyx gold --identify CAPTURE.card`.  An integer
sampler is used. No filter (e.g. antialiasing filter) is applied.
"""


import argparse

import numpy as np

from thriftyx import gold
from thriftyx import settings


def generate(bit_length, code_index, sps, family=None):
    """Generate a code template.

    Parameters
    ----------
    bit_length : int
        Code register length.
    code_index : int
        Index of code within its family (0 ... 2**bit_length).
    sps : float
        Samples per code symbol (bit).
    family : {'gold', 'legacy'} or None
        See :func:`thriftyx.gold.gold` (required for 8 and 10 bits).

    Returns
    -------
    template : nparray
    """
    code = gold.gold(bit_length, code_index, family)
    return resample(code, sps)


def resample(code, sps):
    """Sample `code` at `sps` samples per symbol using an integer sampler."""
    length = int(sps * len(code))
    indices = np.arange(length) * len(code) // length
    symbols = np.where(code, 1, -1)
    samples = np.array(symbols)[indices]
    return samples


def _main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('length', type=int, help="Code register length. "
                                                 "Code length will be 2^n-1.")
    parser.add_argument('index', nargs='?', type=int, default=0,
                        help="Index of code within its family (0 ... 2^n).")
    parser.add_argument('--family', choices=gold.FAMILIES, default=None,
                        help="code family; required for 8 and 10 bits, "
                             "where the Gold codes differ from the legacy "
                             "(non-Gold) codes Thrifty generated before. "
                             "`thriftyx gold --identify CAPTURE.card` tells "
                             "which one a transmitter sends")
    parser.add_argument('-o', '--output', default='template.npy',
                        help="Output file (.npy).")

    setting_keys = ['device_type', 'sample_rate', 'chip_rate']
    # The template is for this sample rate, even a device default.
    config, args = settings.load_args(parser, setting_keys,
                                      sample_rate_final=True)

    sps = config.sample_rate / config.chip_rate
    try:
        # Before opening the output: a bad length or index must not
        # truncate an existing template.
        samples = generate(args.length, args.index, sps, args.family)
    except ValueError as exc:
        parser.error(str(exc))
    with open(args.output, 'wb') as output:
        np.save(output, samples)

    code_len = 2**args.length - 1
    family = "legacy (not Gold)" if args.family == 'legacy' else "Gold"
    print("Generated new template: {}-bit {} code, index {}: {} symbols @ "
          "{:.6f} MHz = {:.3f} ms --> {} samples @ {:.6f} Msps"
          .format(args.length, family, args.index, code_len,
                  config.chip_rate / 1e6,
                  code_len / config.chip_rate * 1e3,
                  len(samples),
                  config.sample_rate / 1e6))


if __name__ == '__main__':
    _main()
