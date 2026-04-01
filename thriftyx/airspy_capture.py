# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Capture positioning signals from SDR hardware to .card file.

Supports RTL-SDR (8-bit), Airspy Mini (12-bit), and Airspy R2 (12-bit).
For RTL-SDR, uses fastcard binary if available, otherwise falls back to
Python-based carrier detection.  For Airspy devices, uses the Hardware
Abstraction Layer (HAL) with Python carrier detection.

Only blocks where a carrier is detected are written to the .card file,
matching the behaviour of the original Thrifty ``fastcard`` tool.
"""

import argparse
import base64
import logging
import os
import shutil
import signal
import subprocess
import sys
import time

import numpy as np

from thriftyx import settings as settings_module
from thriftyx import setting_parsers
from thriftyx.block_data import (write_card_header, raw_to_complex)
from thriftyx import config_validator
from thriftyx.carrier_detect import detect as carrier_detect_block
from thriftyx.exceptions import (DeviceNotFoundError, DeviceConfigError,
                                  ConfigValidationError)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_output(output_path):
    """Open output file for writing, or return stdout."""
    if output_path is None or output_path == '-':
        return sys.stdout
    return open(output_path, 'w')


def _bit_depth_for_device(device_type):
    """Return the correct bit depth for the device type."""
    if device_type in ('airspy_mini', 'airspy_r2'):
        return 12
    return 8  # RTL-SDR


def _compute_threshold(fft_mag, thresh_coeffs, noise_rms):
    """Compute detection threshold for display.

    Mirrors ``carrier_detect._calculate_threshold`` so we can show the
    threshold alongside peak magnitude in the fastcard-style status line.
    """
    thresh_const, thresh_snr, thresh_stddev = thresh_coeffs
    stddev = np.std(fft_mag) if thresh_stddev else 0
    thresh = (thresh_const + thresh_snr * noise_rms ** 2
              + thresh_stddev * stddev ** 2)
    return np.sqrt(thresh)


def _print_capture_header(config, window):
    """Print fastcard-compatible capture configuration header to stderr."""
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    constant, snr, _stddev = config.carrier_threshold
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    gain = float(config.tuner_gain)

    print("block size: {}; history length: {}".format(block_size, block_history),
          file=sys.stderr)
    print("carrier bin window: min = {}; max = {}".format(window[0], window[1]),
          file=sys.stderr)
    print("threshold: constant = {:g}; snr = {:g}".format(constant, snr),
          file=sys.stderr)
    print(file=sys.stderr)
    print("tuner:", file=sys.stderr)
    print("    center freq = {:.6f} MHz".format(center_freq / 1e6),
          file=sys.stderr)
    print("    sample rate = {:.6f} Msps".format(sample_rate / 1e6),
          file=sys.stderr)
    print("    gain = {:.2f} dB".format(gain), file=sys.stderr)


def _print_detection_line(block_idx, peak_idx, peak_mag, threshold, noise_rms):
    """Print a fastcard-compatible per-block detection line to stderr."""
    print("block #{}: mag[{}] = {:.1f} (thresh = {:.1f}, noise = {:.1f})"
          .format(block_idx, peak_idx, peak_mag, threshold, noise_rms),
          file=sys.stderr)


def _write_card_line(output_file, timestamp, block_idx, raw_array):
    """Write one line in v1 .card format: ``timestamp block_idx base64``."""
    encoded = base64.b64encode(raw_array.tobytes()).decode('ascii')
    output_file.write("{:.6f} {} {}\n".format(timestamp, block_idx, encoded))


# ---------------------------------------------------------------------------
# RTL-SDR capture via fastcard binary (preferred)
# ---------------------------------------------------------------------------

def _capture_rtlsdr_fastcard(config, extra_args):
    """Capture from RTL-SDR by delegating to the ``fastcard`` binary.

    This replicates the original Thrifty ``fastcard_capture.py`` behaviour:
    fastcard performs carrier detection in C and writes only detected blocks
    to the .card file.
    """
    bin_freq = config.sample_rate / config.block_size
    window = setting_parsers.normalize_freq_range(
        config.carrier_window, bin_freq)
    constant, snr, stddev = config.carrier_threshold
    if stddev != 0:
        print("Warning: fastcard does not support 'stddev' in threshold "
              "formula", file=sys.stderr)

    fastcard_path = extra_args.get('fastcard', 'fastcard')
    device_index = extra_args.get('device_index', 0)

    call = [
        fastcard_path,
        '-i', 'rtlsdr',
        '-s', str(int(config.sample_rate)),
        '-f', str(int(config.tuner_freq)),
        '-g', str(float(config.tuner_gain)),
        '-d', str(device_index),
        '-b', str(int(config.block_size)),
        '-h', str(int(config.block_history)),
        '-w', "{}-{}".format(window[0], window[1]),
        '-t', "{}c{}s".format(constant, snr),
        '-k', str(int(config.capture_skip)),
    ]

    output_path = extra_args.get('output')
    if output_path is not None and output_path != '-':
        call.extend(['-o', output_path])

    logging.info("Calling %s", ' '.join(call))

    os.setpgrp()
    process = subprocess.Popen(call)

    def _signal_handler(signal_, _frame):
        try:
            if process.poll() is None:
                process.send_signal(signal_)
                returncode = process.wait()
                sys.exit(returncode)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        returncode = process.wait()
        if returncode != 0:
            sys.exit(returncode)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# RTL-SDR capture with Python carrier detection (fallback)
# ---------------------------------------------------------------------------

def _capture_rtlsdr(config, extra_args, output_file):
    """Capture from RTL-SDR with Python-based carrier detection.

    Reads raw uint8 I/Q data from *stdin* (piped from ``rtl_sdr``) or from a
    file, performs carrier detection on each block, and writes only detected
    blocks in v1 .card format (no header, ``timestamp block_idx base64``).
    """
    sample_rate = int(config.sample_rate)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.capture_skip)
    duration = extra_args.get('duration')
    input_path = extra_args.get('input')
    bit_depth = 8
    thresh_coeffs = config.carrier_threshold

    bin_freq = sample_rate / block_size
    window = setting_parsers.normalize_freq_range(
        config.carrier_window, bin_freq)

    # Print fastcard-compatible header to stderr
    _print_capture_header(config, window)

    # Determine input source
    if input_path and input_path != '-':
        input_stream = open(input_path, 'rb')
    else:
        input_stream = sys.stdin.buffer

    block_idx = 0
    detected_count = 0
    start_time = time.time()
    running = [True]

    def _sigint_handler(_sig, _frame):
        running[0] = False

    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    new_samples = block_size - block_history
    # RTL-SDR: uint8 I/Q interleaved, 1 byte per component
    bytes_per_block = new_samples * 2

    if capture_skip > 0:
        skip_bytes = capture_skip * bytes_per_block
        print("\nSkipping {} block(s)...".format(capture_skip),
              end="", file=sys.stderr)
        sys.stderr.flush()
        skipped = 0
        while skipped < skip_bytes and running[0]:
            chunk = input_stream.read(min(skip_bytes - skipped, 65536))
            if not chunk:
                break
            skipped += len(chunk)
        print(" done\n", file=sys.stderr)

    # History buffer for block overlap
    history_raw = np.zeros(block_history * 2, dtype=np.uint8)

    while running[0]:
        if duration is not None and (time.time() - start_time) >= duration:
            break

        raw_bytes = input_stream.read(bytes_per_block)
        if len(raw_bytes) < bytes_per_block:
            break

        new_raw = np.frombuffer(raw_bytes, dtype=np.uint8)

        # Build full block with history
        block_raw = np.concatenate([history_raw, new_raw])
        block_complex = raw_to_complex(block_raw, bit_depth=bit_depth)

        # Carrier detection via FFT
        fft_mag = np.abs(np.fft.fft(block_complex))
        detected, peak_idx, peak_mag, noise_rms = carrier_detect_block(
            fft_mag, thresh_coeffs, window=window)

        if detected:
            threshold = _compute_threshold(fft_mag, thresh_coeffs, noise_rms)
            _print_detection_line(block_idx, peak_idx, peak_mag,
                                  threshold, noise_rms)
            # Write v1 format line (raw uint8 bytes, no conversion loss)
            _write_card_line(output_file, time.time(), block_idx, block_raw)
            output_file.flush()
            detected_count += 1

        history_raw = new_raw[-block_history * 2:]
        block_idx += 1

    if input_stream not in (sys.stdin, sys.stdin.buffer):
        input_stream.close()

    print("\nRead {} blocks.".format(block_idx), file=sys.stderr)
    logger.info("Detected %d blocks out of %d", detected_count, block_idx)
    return block_idx


# ---------------------------------------------------------------------------
# Airspy capture with Python carrier detection
# ---------------------------------------------------------------------------

def _capture_airspy(config, extra_args, output_file):
    """Capture from Airspy Mini or Airspy R2 via HAL with carrier detection.

    Detected blocks are written in v2 .card format (``#v2`` header +
    ``timestamp block_idx base64`` lines with int16 I/Q data).
    """
    from thriftyx.hal.device_factory import create_device

    device_type = config.get('device_type', 'airspy_mini')
    bit_depth = 12  # Airspy always 12-bit
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.capture_skip)
    duration = extra_args.get('duration')
    thresh_coeffs = config.carrier_threshold

    bin_freq = sample_rate / block_size
    window = setting_parsers.normalize_freq_range(
        config.carrier_window, bin_freq)

    try:
        logger.info("Opening %s device", device_type)
        device = create_device(device_type)
        device.open()
    except DeviceNotFoundError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        print("Is the Airspy device connected? Is libairspy installed?",
              file=sys.stderr)
        sys.exit(1)

    block_idx = 0
    detected_count = 0

    try:
        device.set_sample_rate(sample_rate)
        device.set_center_freq(center_freq)
        device.set_gain('lna', int(config.get('lna_gain', 0)))
        device.set_gain('mixer', int(config.get('mixer_gain', 0)))
        device.set_gain('vga', int(config.get('vga_gain', 0)))
        device.set_bias_tee(bool(config.get('bias_tee', False)))

        # Write v2 .card header
        write_card_header(output_file, bit_depth=bit_depth,
                          sample_rate=sample_rate)

        # Print fastcard-compatible configuration header
        _print_capture_header(config, window)

        start_time = time.time()
        running = [True]
        new_samples = block_size - block_history
        history_raw = np.zeros(block_history * 2, dtype=np.int16)

        def _sigint_handler(_sig, _frame):
            running[0] = False

        signal.signal(signal.SIGINT, _sigint_handler)
        signal.signal(signal.SIGTERM, _sigint_handler)

        if capture_skip > 0:
            print("\nSkipping {} block(s)...".format(capture_skip),
                  end="", file=sys.stderr)
            sys.stderr.flush()
            blocks_skipped = 0
            while blocks_skipped < capture_skip and running[0]:
                raw = device.read_sync(new_samples)
                if len(raw) < new_samples * 2:
                    break
                history_raw = raw[-block_history * 2:]
                blocks_skipped += 1
            print(" done\n", file=sys.stderr)

        while running[0]:
            if duration is not None and (time.time() - start_time) >= duration:
                break

            raw = device.read_sync(new_samples)
            if len(raw) < new_samples * 2:
                break

            block_raw = np.concatenate([history_raw, raw])
            block_complex = raw_to_complex(block_raw, bit_depth=bit_depth)

            # Carrier detection via FFT
            fft_mag = np.abs(np.fft.fft(block_complex))
            detected, peak_idx, peak_mag, noise_rms = carrier_detect_block(
                fft_mag, thresh_coeffs, window=window)

            if detected:
                threshold = _compute_threshold(
                    fft_mag, thresh_coeffs, noise_rms)
                _print_detection_line(block_idx, peak_idx, peak_mag,
                                      threshold, noise_rms)
                # Write v2 format line (raw int16 bytes)
                _write_card_line(output_file, time.time(), block_idx,
                                 block_raw)
                output_file.flush()
                detected_count += 1

            history_raw = raw[-block_history * 2:]
            block_idx += 1

    except DeviceConfigError as e:
        print("ERROR configuring device: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        device.close()
        print("\nRead {} blocks.".format(block_idx), file=sys.stderr)
        logger.info("Detected %d blocks out of %d", detected_count, block_idx)

    return block_idx


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def capture_cli(args=None):
    """Main capture CLI function.

    Supports all device types:
      - RTL-SDR:     thriftyx capture output.card --device-type rtlsdr
                     rtl_sdr -f 162M -s 2.4M - | thriftyx capture output.card
      - Airspy Mini: thriftyx capture output.card --device-type airspy_mini
      - Airspy R2:   thriftyx capture output.card --device-type airspy_r2

    For RTL-SDR, the ``fastcard`` binary is used when available.  If it is
    not installed, a Python-based carrier detection fallback is used.
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
                        help="Capture duration in seconds (default: until "
                             "Ctrl+C)")
    parser.add_argument('--fastcard', dest='fastcard', default='fastcard',
                        help="Path to fastcard binary")
    parser.add_argument('-d', '--device-index', dest='device_index',
                        type=int, default=0, help="RTL-SDR device index")

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
        print("ERROR: Invalid configuration: {}".format(e), file=sys.stderr)
        sys.exit(1)

    device_type = config.get('device_type', 'rtlsdr')

    try:
        if device_type == 'rtlsdr':
            # Prefer the fastcard binary for RTL-SDR (matches original Thrifty)
            fastcard_path = extra_args.get('fastcard', 'fastcard')
            if shutil.which(fastcard_path):
                logger.info("Using fastcard binary: %s", fastcard_path)
                _capture_rtlsdr_fastcard(config, extra_args)
            else:
                # Python fallback: carrier detection + v1 .card format
                logger.info("fastcard not found; using Python carrier "
                            "detection")
                output_path = extra_args.get('output')
                output_file = _open_output(output_path)
                try:
                    _capture_rtlsdr(config, extra_args, output_file)
                finally:
                    if output_file not in (sys.stdout, sys.stderr):
                        output_file.close()
        elif device_type in ('airspy_mini', 'airspy_r2'):
            output_path = extra_args.get('output')
            output_file = _open_output(output_path)
            try:
                _capture_airspy(config, extra_args, output_file)
            finally:
                if output_file not in (sys.stdout, sys.stderr):
                    output_file.close()
        else:
            print("ERROR: Unknown device_type: {}".format(device_type),
                  file=sys.stderr)
            sys.exit(1)
    except BrokenPipeError:
        # Downstream consumer closed the pipe (e.g. ``head``)
        pass


# Legacy alias
_main = capture_cli
