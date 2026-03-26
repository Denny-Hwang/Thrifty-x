# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Capture carrier detections from Airspy SDR to .card file.

Replaces the original fastcard_capture.py (RTL-SDR based).
Uses the Hardware Abstraction Layer (HAL) for device access.
"""

import argparse
import logging
import signal
import sys
import time

import numpy as np

from thriftyx import settings as settings_module
from thriftyx import setting_parsers
from thriftyx.block_data import card_writer, write_card_header, raw_to_complex
from thriftyx.hal.device_factory import create_device
from thriftyx.exceptions import DeviceNotFoundError, DeviceConfigError

logger = logging.getLogger(__name__)


def _open_output(output_path):
    if output_path is None or output_path == '-':
        return sys.stdout
    return open(output_path, 'w')


def capture_cli(args=None):
    """Main capture CLI function."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('output', nargs='?', default=None,
                        help="Output .card file path ('-' for stdout, "
                             "default: stdout)")
    parser.add_argument('--device-type', dest='device_type',
                        default=None,
                        help="SDR device type (airspy_mini or airspy_r2)")
    parser.add_argument('--duration', dest='duration',
                        type=float, default=None,
                        help="Capture duration in seconds (default: until Ctrl+C)")

    setting_keys = ['sample_rate', 'tuner_freq', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'device_type', 'bit_depth', 'bias_tee',
                    'lna_gain', 'mixer_gain', 'vga_gain']
    config, extra_args = settings_module.load_args(parser, setting_keys,
                                                    argv=args)

    device_type = config.get('device_type', 'airspy_mini')
    bit_depth = int(config.get('bit_depth', 12))
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    duration = extra_args.get('duration')
    output_path = extra_args.get('output')

    output_file = _open_output(output_path)

    try:
        logger.info("Opening %s device", device_type)
        device = create_device(device_type)
        device.open()
    except DeviceNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Is the Airspy device connected? Is libairspy installed?",
              file=sys.stderr)
        sys.exit(1)

    try:
        device.set_sample_rate(sample_rate)
        device.set_center_freq(center_freq)
        device.set_gain('lna', int(config.get('lna_gain', 0)))
        device.set_gain('mixer', int(config.get('mixer_gain', 0)))
        device.set_gain('vga', int(config.get('vga_gain', 0)))
        device.set_bias_tee(bool(config.get('bias_tee', False)))
    except DeviceConfigError as e:
        print(f"ERROR configuring device: {e}", file=sys.stderr)
        device.close()
        sys.exit(1)

    # Write v2 header
    write_card_header(output_file, bit_depth=bit_depth, sample_rate=sample_rate)

    block_idx = 0
    start_time = time.time()
    running = [True]
    history_buf = np.zeros(block_history * 2, dtype=np.int16)

    def _sigint_handler(sig, frame):
        running[0] = False
        logger.info("Stopping capture...")

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    new_samples = block_size - block_history

    logger.info("Starting capture: %s Hz @ %.1f MHz, block_size=%d",
                sample_rate, center_freq / 1e6, block_size)

    try:
        while running[0]:
            if duration is not None and (time.time() - start_time) >= duration:
                break

            raw = device.read_sync(new_samples)
            if len(raw) < new_samples * 2:
                break

            # Build full block with history
            block_raw = np.concatenate([history_buf, raw])
            block_complex = raw_to_complex(block_raw, bit_depth=bit_depth)

            card_writer(output_file, time.time(), block_idx,
                        block_complex, bit_depth=bit_depth,
                        sample_rate=sample_rate)

            history_buf = raw[-block_history * 2:]
            block_idx += 1

    except KeyboardInterrupt:
        pass
    finally:
        device.close()
        if output_file not in (sys.stdout, sys.stderr):
            output_file.close()
        logger.info("Captured %d blocks", block_idx)


# Legacy alias
_main = capture_cli
