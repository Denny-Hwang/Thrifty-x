# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Live time-domain, frequency-domain, and histogram plot using matplotlib.

Replaces the original GNU Radio / osmo-sdr-based scope with a
matplotlib FuncAnimation implementation.  Supports RTL-SDR (via stdin
pipe from ``rtl_sdr``) and Airspy devices (via HAL).

Original 3-panel layout: time domain, frequency spectrum, sample histogram.
"""

import argparse
import logging
import sys

import numpy as np

from thriftyx import settings as settings_module

logger = logging.getLogger(__name__)


def scope_cli(args=None):
    """Live signal scope for RTL-SDR and Airspy hardware."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation
    except ImportError:
        print("ERROR: matplotlib is required for scope. "
              "Install with: pip install matplotlib", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    setting_keys = ['tuner_freq', 'sample_rate', 'block_size',
                    'device_type', 'lna_gain', 'mixer_gain', 'vga_gain',
                    'bias_tee', 'bit_depth']
    config, _ = settings_module.load_args(parser, setting_keys, argv=args)

    device_type = config.get('device_type', 'rtlsdr')
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)

    if device_type in ('airspy_mini', 'airspy_r2'):
        bit_depth = 12
    else:
        bit_depth = 8

    from thriftyx.block_data import raw_to_complex
    from thriftyx.exceptions import DeviceNotFoundError

    # --------------- data source setup ---------------
    device = None
    stdin_source = False

    if device_type == 'rtlsdr':
        # RTL-SDR: read raw uint8 I/Q from stdin (piped from rtl_sdr)
        if sys.stdin.isatty():
            print("RTL-SDR scope reads raw I/Q from stdin.\n"
                  "Usage: rtl_sdr -f {freq} -s {rate} - | thriftyx scope"
                  .format(freq=center_freq, rate=sample_rate),
                  file=sys.stderr)
            sys.exit(1)
        stdin_source = True
        logger.info("Reading RTL-SDR samples from stdin")
    else:
        from thriftyx.hal.device_factory import create_device
        try:
            device = create_device(device_type)
            device.open()
            device.set_sample_rate(sample_rate)
            device.set_center_freq(center_freq)
            device.set_gain('lna', int(config.get('lna_gain', 0)))
            device.set_gain('mixer', int(config.get('mixer_gain', 0)))
            device.set_gain('vga', int(config.get('vga_gain', 0)))
            device.set_bias_tee(bool(config.get('bias_tee', False)))
        except DeviceNotFoundError as e:
            print("ERROR: {}".format(e), file=sys.stderr)
            sys.exit(1)

    # --------------- 3-panel figure ---------------
    fig, (ax_time, ax_freq, ax_hist) = plt.subplots(
        3, 1, figsize=(10, 8))
    fig.suptitle("Thrifty-X Scope \u2014 {:.3f} MHz @ {:.1f} MSPS"
                 .format(center_freq / 1e6, sample_rate / 1e6))

    # Panel 1: time domain (magnitude)
    t_line, = ax_time.plot([], [], 'b-', linewidth=0.5)
    ax_time.set_xlim(0, block_size // 2)
    ax_time.set_ylim(-1.2, 1.2)
    ax_time.set_xlabel('Sample')
    ax_time.set_ylabel('Magnitude')
    ax_time.set_title('Time domain (magnitude)')
    ax_time.grid(True, alpha=0.3)

    # Panel 2: frequency spectrum
    freqs = np.fft.fftshift(np.fft.fftfreq(block_size, 1.0 / sample_rate))
    f_line, = ax_freq.plot(freqs / 1e6, np.zeros(block_size), 'r-',
                           linewidth=0.5)
    ax_freq.set_xlim(freqs[0] / 1e6, freqs[-1] / 1e6)
    ax_freq.set_ylim(-60, 10)
    ax_freq.set_xlabel('Frequency offset (MHz)')
    ax_freq.set_ylabel('Power (dB)')
    ax_freq.set_title('Frequency spectrum')
    ax_freq.grid(True, alpha=0.3)

    # Panel 3: sample value histogram
    if bit_depth == 8:
        hist_bins = 256
        hist_range = (0, 255)
        h_x = np.arange(hist_bins)
    else:
        hist_bins = 256
        hist_range = (-2048, 2047)
        h_x = np.linspace(hist_range[0], hist_range[1], hist_bins)

    h_bars = ax_hist.bar(h_x, np.zeros(hist_bins), width=h_x[1] - h_x[0],
                         color='green', alpha=0.7)
    ax_hist.set_xlabel('Sample value')
    ax_hist.set_ylabel('Count')
    ax_hist.set_title('Sample value histogram')
    ax_hist.grid(True, alpha=0.3)

    _data = [np.zeros(block_size, dtype=np.complex64)]
    _raw_data = [np.zeros(block_size * 2,
                          dtype=np.uint8 if bit_depth == 8 else np.int16)]

    # Number of bytes per read for stdin
    bytes_per_sample = 1 if bit_depth == 8 else 2
    read_bytes = block_size * 2 * bytes_per_sample

    def _read_block():
        """Read one block of complex samples from the configured source."""
        if stdin_source:
            raw_bytes = sys.stdin.buffer.read(read_bytes)
            if len(raw_bytes) < read_bytes:
                return False
            dtype = np.uint8 if bit_depth == 8 else np.int16
            raw = np.frombuffer(raw_bytes, dtype=dtype)
            _raw_data[0] = raw
            _data[0] = raw_to_complex(raw, bit_depth=bit_depth)
        else:
            raw = device.read_sync(block_size)
            if len(raw) < block_size * 2:
                return False
            _raw_data[0] = raw
            _data[0] = raw_to_complex(raw[:block_size * 2],
                                       bit_depth=bit_depth)
        return True

    def _update(_frame):
        if not _read_block():
            return t_line, f_line

        block = _data[0]

        # Time domain
        mag = np.abs(block[:block_size // 2])
        t_line.set_data(np.arange(len(mag)), mag)

        # Frequency domain
        fft_mag = np.fft.fftshift(np.abs(np.fft.fft(block)))
        fft_db = (20 * np.log10(fft_mag + 1e-12)
                  - 20 * np.log10(block_size))
        f_line.set_ydata(fft_db)
        ax_freq.set_ylim(np.max(fft_db) - 70, np.max(fft_db) + 5)

        # Histogram
        raw = _raw_data[0]
        if bit_depth == 8:
            counts = np.bincount(raw.astype(np.int32), minlength=hist_bins)
        else:
            counts, _ = np.histogram(raw, bins=hist_bins, range=hist_range)
        for bar, count in zip(h_bars, counts):
            bar.set_height(count)
        ax_hist.set_ylim(0, max(np.max(counts), 1) * 1.1)

        return (t_line, f_line) + tuple(h_bars)

    _ani = FuncAnimation(fig, _update, interval=100,
                         blit=False, cache_frame_data=False)

    plt.tight_layout()
    try:
        plt.show()
    finally:
        if device is not None:
            device.close()


# Entry point alias
_main = scope_cli
