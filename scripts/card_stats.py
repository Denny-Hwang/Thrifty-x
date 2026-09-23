#!/usr/bin/env python3
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Sample-level statistics of a .card file against ADC full scale.

Reports, over every block in the card, the peak |I|/|Q| and |I+jQ| (raw
units and as a fraction of ADC full scale), the RMS magnitude, and the
number of samples sitting at the raw container limits.  Use it to check
gain settings, and to confirm the Airspy int16 scale on hardware: with a
strong tone at high gain an Airspy card peaks near 16384 raw
(AIRSPY_INT16_FULL_SCALE, ADC full scale) before libairspy's int16 path
saturates, and headroom figures are then directly comparable with
RTL-SDR cards.

Usage:
    python scripts/card_stats.py rx0.card
"""

import argparse
import sys

import numpy as np

from thriftyx import block_data


def card_stats(stream):
    """Return a dict of statistics for the .card on *stream*."""
    header, stream = block_data.peek_card_header(stream)
    bit_depth = int(header.get('bit_depth', 8))
    full_scale = (block_data.AIRSPY_INT16_FULL_SCALE if bit_depth == 12
                  else 128.0)
    limits = (-32768, 32767) if bit_depth == 12 else (0, 255)

    blocks = 0
    peak_component = peak_magnitude = 0.0
    power_sum = 0.0
    samples = at_limit = 0
    for _, _, block in block_data.card_reader(stream, bit_depth=bit_depth):
        z = np.asarray(block)
        blocks += 1
        samples += len(z)
        peak_component = max(peak_component, float(np.max(np.abs(z.real))),
                             float(np.max(np.abs(z.imag))))
        peak_magnitude = max(peak_magnitude, float(np.max(np.abs(z))))
        power_sum += float(np.sum(np.abs(z) ** 2))
        raw = block_data.complex_to_raw(z, bit_depth=bit_depth)
        at_limit += int(np.sum((raw == limits[0]) | (raw == limits[1])))
    return {
        'header': header,
        'bit_depth': bit_depth,
        'blocks': blocks,
        'full_scale_raw': full_scale,
        'peak_component': peak_component,
        'peak_magnitude': peak_magnitude,
        'rms_magnitude': (np.sqrt(power_sum / samples) if samples else 0.0),
        'samples_at_limit': at_limit,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('card', type=argparse.FileType('r'))
    args = parser.parse_args(argv)
    stats = card_stats(args.card)
    if not stats['blocks']:
        print("no blocks in {}".format(args.card.name), file=sys.stderr)
        return 1
    fs = stats['full_scale_raw']
    header = stats['header']
    print("{}: {} blocks, {}-bit{}".format(
        args.card.name, stats['blocks'], stats['bit_depth'],
        ", {:g} MSPS".format(float(header['sample_rate']) / 1e6)
        if float(header.get('sample_rate', 0)) > 0 else ""))
    for label, key in (("peak |I|,|Q|", 'peak_component'),
                       ("peak |I+jQ|", 'peak_magnitude'),
                       ("RMS |I+jQ|", 'rms_magnitude')):
        value = stats[key]
        print("  {:<13} {:8.0f} raw  = {:6.3f} of ADC full scale"
              " ({:+.1f} dBFS)".format(label, value * fs, value,
                                       20 * np.log10(max(value, 1e-12))))
    print("  samples at the raw limits (clipping/saturation): {}".format(
        stats['samples_at_limit']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
