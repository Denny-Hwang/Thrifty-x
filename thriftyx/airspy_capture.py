# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Capture positioning signals from SDR hardware to .card file.

Supports RTL-SDR (8-bit), Airspy Mini (12-bit), and Airspy R2 (12-bit).
For RTL-SDR, raw binary data can be piped from rtl_sdr via stdin.
For Airspy devices, uses the Hardware Abstraction Layer (HAL).
"""

import argparse
import logging
import signal
import sys
import time

import numpy as np

from thriftyx import settings as settings_module
from thriftyx import setting_parsers
from thriftyx.block_data import (card_writer, write_card_header,
                                  raw_to_complex, block_reader)
from thriftyx import config_validator
from thriftyx.exceptions import (DeviceNotFoundError, DeviceConfigError,
                                  ConfigValidationError)

logger = logging.getLogger(__name__)


def _open_output(output_path):
    if output_path is None or output_path == '-':
        return sys.stdout
    return open(output_path, 'w')


def _bit_depth_for_device(device_type):
    """Return the correct bit depth for the device type."""
    if device_type in ('airspy_mini', 'airspy_r2'):
        return 12
    return 8  # RTL-SDR


def _capture_rtlsdr(config, extra_args, output_file):
    """Capture from RTL-SDR via raw stdin pipe or rtl_sdr subprocess.

    Usage: rtl_sdr -f 162M -s 2.4M - | thriftyx capture -
    Or:    thriftyx capture output.card  (with RTL-SDR connected)
    """
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.get('capture_skip', 1))
    duration = extra_args.get('duration')
    input_path = extra_args.get('input')
    bit_depth = 8

    # Write .card header
    write_card_header(output_file, bit_depth=bit_depth,
                      sample_rate=sample_rate)

    # Determine input source
    if input_path and input_path != '-':
        input_stream = open(input_path, 'rb')
    else:
        input_stream = sys.stdin.buffer

    logger.info("block size: %d; history length: %d", block_size, block_history)
    logger.info("carrier bin window: min = %d; max = %d",
                int(config.carrier_window[0]), int(config.carrier_window[1]))
    threshold = config.carrier_threshold
    logger.info("threshold: constant = %g; snr = %g", threshold[0], threshold[1])
    logger.info("")
    logger.info("tuner:")
    logger.info("    center freq = %.6f MHz", center_freq / 1e6)
    logger.info("    sample rate = %.6f Msps", sample_rate / 1e6)
    logger.info("    gain = %.2f dB", float(config.get('tuner_gain', 0)))

    block_idx = 0
    start_time = time.time()
    running = [True]

    def _sigint_handler(sig, frame):
        running[0] = False
        logger.info("Stopping capture...")

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    new_samples = block_size - block_history
    # RTL-SDR: 8-bit unsigned, I/Q interleaved, 1 byte per component
    bytes_per_block = new_samples * 2  # *2 for I and Q

    if capture_skip > 0:
        skip_bytes = capture_skip * bytes_per_block
        logger.info("Skipping %d block(s)...", capture_skip)
        skipped = 0
        while skipped < skip_bytes and running[0]:
            chunk = input_stream.read(min(skip_bytes - skipped, 65536))
            if not chunk:
                break
            skipped += len(chunk)
        logger.info("done")

    # History buffer for block overlap
    history = np.zeros(block_history * 2, dtype=np.uint8)

    while running[0]:
        if duration is not None and (time.time() - start_time) >= duration:
            break

        raw_bytes = input_stream.read(bytes_per_block)
        if len(raw_bytes) < bytes_per_block:
            break

        raw = np.frombuffer(raw_bytes, dtype=np.uint8)

        # Build full block with history
        block_raw = np.concatenate([history, raw])
        block_complex = raw_to_complex(block_raw, bit_depth=bit_depth)

        card_writer(output_file, time.time(), block_idx,
                    block_complex, bit_depth=bit_depth,
                    sample_rate=sample_rate)

        history = raw[-block_history * 2:]
        block_idx += 1

    if input_stream not in (sys.stdin, sys.stdin.buffer):
        input_stream.close()

    logger.info("Read %d blocks.", block_idx)
    return block_idx


def _capture_airspy(config, extra_args, output_file):
    """Capture from Airspy Mini or Airspy R2 via HAL."""
    from thriftyx.hal.device_factory import create_device

    device_type = config.get('device_type', 'airspy_mini')
    bit_depth = 12  # Airspy always 12-bit
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.get('capture_skip', 1))
    duration = extra_args.get('duration')

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

        write_card_header(output_file, bit_depth=bit_depth,
                          sample_rate=sample_rate)

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

        logger.info("Starting capture: %s Hz @ %.1f MHz, block_size=%d, "
                    "skip=%d", sample_rate, center_freq / 1e6, block_size,
                    capture_skip)

        blocks_skipped = 0

        while running[0]:
            if duration is not None and (time.time() - start_time) >= duration:
                break

            raw = device.read_sync(new_samples)
            if len(raw) < new_samples * 2:
                break

            if blocks_skipped < capture_skip:
                blocks_skipped += 1
                history_buf = raw[-block_history * 2:]
                continue

            block_raw = np.concatenate([history_buf, raw])
            block_complex = raw_to_complex(block_raw, bit_depth=bit_depth)

            card_writer(output_file, time.time(), block_idx,
                        block_complex, bit_depth=bit_depth,
                        sample_rate=sample_rate)

            history_buf = raw[-block_history * 2:]
            block_idx += 1

    except DeviceConfigError as e:
        print(f"ERROR configuring device: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        device.close()
        logger.info("Captured %d blocks", block_idx if 'block_idx' in locals()
                     else 0)

    return block_idx if 'block_idx' in locals() else 0


def capture_cli(args=None):
    """Main capture CLI function.

    Supports all device types:
      - RTL-SDR:     thriftyx capture output.card --device-type rtlsdr
                     rtl_sdr -f 162M -s 2.4M - | thriftyx capture output.card
      - Airspy Mini: thriftyx capture output.card --device-type airspy_mini
      - Airspy R2:   thriftyx capture output.card --device-type airspy_r2
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('output', nargs='?', default=None,
                        help="Output .card file path ('-' for stdout, "
                             "default: stdout)")
    parser.add_argument('--input', dest='input', default=None,
                        help="Input raw binary file for RTL-SDR "
                             "('-' for stdin, default: stdin)")
    parser.add_argument('--duration', dest='duration',
                        type=float, default=None,
                        help="Capture duration in seconds (default: until Ctrl+C)")

    setting_keys = ['device_type', 'sample_rate', 'tuner_freq',
                    'tuner_gain', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'bit_depth', 'bias_tee',
                    'lna_gain', 'mixer_gain', 'vga_gain',
                    'capture_skip']
    config, extra_args = settings_module.load_args(parser, setting_keys,
                                                    argv=args)

    # Validate configuration
    try:
        validation_warnings = config_validator.validate_config(config)
        for w in validation_warnings:
            logger.warning("Config warning: %s", w)
    except ConfigValidationError as e:
        print(f"ERROR: Invalid configuration: {e}", file=sys.stderr)
        sys.exit(1)

    device_type = config.get('device_type', 'rtlsdr')
    output_path = extra_args.get('output')
    output_file = _open_output(output_path)

    try:
        if device_type == 'rtlsdr':
            _capture_rtlsdr(config, extra_args, output_file)
        elif device_type in ('airspy_mini', 'airspy_r2'):
            _capture_airspy(config, extra_args, output_file)
        else:
            print(f"ERROR: Unknown device_type: {device_type}", file=sys.stderr)
            sys.exit(1)
    finally:
        if output_file not in (sys.stdout, sys.stderr):
            output_file.close()


# Legacy alias
_main = capture_cli
