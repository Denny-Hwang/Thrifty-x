# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Live time-domain and frequency-domain plot using matplotlib.

Replaces the original GNU Radio / osmo-sdr-based scope with a
matplotlib FuncAnimation implementation.
"""
# pylint: skip-file

import argparse
import logging
import sys

import numpy as np

from thriftyx import settings as settings_module

logger = logging.getLogger(__name__)


def scope_cli(args=None):
    """Live signal scope using Airspy SDR and matplotlib."""
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

    device_type = config.get('device_type', 'airspy_mini')
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    bit_depth = int(config.get('bit_depth', 12))

    from thriftyx.hal.device_factory import create_device
    from thriftyx.block_data import raw_to_complex
    from thriftyx.exceptions import DeviceNotFoundError

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
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    fig, (ax_time, ax_freq) = plt.subplots(2, 1, figsize=(10, 6))
    fig.suptitle(f"Thrifty-X Scope — {center_freq / 1e6:.3f} MHz "
                 f"@ {sample_rate / 1e6:.1f} MSPS")

    # Time domain plot
    t_line, = ax_time.plot([], [], 'b-', linewidth=0.5)
    ax_time.set_xlim(0, block_size // 2)
    ax_time.set_ylim(-1.2, 1.2)
    ax_time.set_xlabel('Sample')
    ax_time.set_ylabel('Magnitude')
    ax_time.set_title('Time domain (magnitude)')
    ax_time.grid(True, alpha=0.3)

    # Frequency domain plot
    freqs = np.fft.fftshift(np.fft.fftfreq(block_size, 1.0 / sample_rate))
    f_line, = ax_freq.plot(freqs / 1e6, np.zeros(block_size), 'r-',
                           linewidth=0.5)
    ax_freq.set_xlim(freqs[0] / 1e6, freqs[-1] / 1e6)
    ax_freq.set_ylim(-60, 10)
    ax_freq.set_xlabel('Frequency offset (MHz)')
    ax_freq.set_ylabel('Power (dB)')
    ax_freq.set_title('Frequency spectrum')
    ax_freq.grid(True, alpha=0.3)

    _data = [np.zeros(block_size, dtype=np.complex64)]

    def _update(frame):
        raw = device.read_sync(block_size)
        if len(raw) >= block_size * 2:
            _data[0] = raw_to_complex(raw[:block_size * 2],
                                       bit_depth=bit_depth)

        block = _data[0]
        mag = np.abs(block[:block_size // 2])
        t_line.set_data(np.arange(len(mag)), mag)

        fft_mag = np.fft.fftshift(np.abs(np.fft.fft(block)))
        fft_db = 20 * np.log10(fft_mag + 1e-12) - 20 * np.log10(block_size)
        f_line.set_ydata(fft_db)
        ax_freq.set_ylim(np.max(fft_db) - 70, np.max(fft_db) + 5)

        return t_line, f_line

    ani = FuncAnimation(fig, _update, interval=100, blit=True, cache_frame_data=False)

    plt.tight_layout()
    try:
        plt.show()
    finally:
        device.close()


# Entry point alias
_main = scope_cli
