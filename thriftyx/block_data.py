# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Module for splitting raw data into fixed-sized blocks.

Supports both 8-bit unsigned (RTL-SDR legacy, v1 .card format) and
12-bit signed (Airspy, v2 .card format) I/Q samples.
"""

import base64
import time

import numpy as np

from thriftyx.signal_utils import Signal

_V2_HEADER_PREFIX = '#v2 '


def _raw_reader(stream, chunk_size):
    """Read raw chunks of data."""
    while True:
        buf = stream.read(chunk_size)
        if len(buf) == 0:
            break
        yield buf


def _raw_block_reader(stream, block_size, bit_depth=12):
    """Read fixed-sized blocks of samples.

    Parameters
    ----------
    bit_depth : int
        12 for Airspy (int16), 8 for RTL-SDR (uint8).
    """
    bytes_per_sample = 2 if bit_depth == 12 else 1
    chunk_bytes = block_size * bytes_per_sample * 2  # *2 for I+Q
    dtype = np.int16 if bit_depth == 12 else np.uint8
    chunk = b""
    for raw in _raw_reader(stream, chunk_bytes - len(chunk)):
        chunk += raw
        if len(chunk) < chunk_bytes:
            continue
        data = np.frombuffer(chunk[:chunk_bytes], dtype=dtype)
        yield data
        chunk = chunk[chunk_bytes:]


def raw_to_complex(data, bit_depth=8):
    """Convert raw I/Q interleaved samples to array of complex values.

    Parameters
    ----------
    data : :class:`numpy.ndarray`
        Raw sample data. int16 for 12-bit Airspy, uint8 for 8-bit RTL-SDR.
    bit_depth : int
        ADC bit depth. 8 for RTL-SDR legacy (default), 12 for Airspy.

    Returns
    -------
    :class:`numpy.ndarray` of `numpy.complex64`
    """
    if bit_depth == 12:
        # Airspy: 12-bit signed int16, range -2048 to +2047, no DC offset
        values = data.astype(np.float32).view(np.complex64)
        values = values / 2048.0
    elif bit_depth == 8:
        # RTL-SDR legacy: 8-bit unsigned uint8, DC offset at 127.4
        values = data.astype(np.float32).view(np.complex64)
        values -= (127.4 + 127.4j)
        values /= 128.0
    else:
        raise ValueError(f"Unsupported bit depth: {bit_depth}. Use 8 or 12.")
    return values


def complex_to_raw(array, bit_depth=8):
    """Convert complex array back to I/Q interleaved samples.

    Parameters
    ----------
    array : :class:`numpy.ndarray` of `numpy.complex64`
    bit_depth : int
        ADC bit depth. 8 for RTL-SDR legacy (default), 12 for Airspy.

    Returns
    -------
    :class:`numpy.ndarray` of `numpy.int16` or `numpy.uint8`
    """
    if bit_depth == 12:
        scaled = (array.astype(np.complex64) * 2048.0).view(np.float32)
        return np.clip(scaled, -2048, 2047).astype(np.int16)
    elif bit_depth == 8:
        scaled = array.astype(np.complex64).view(np.float32) * 128 + 127.4
        return np.clip(scaled, 0, 255).astype(np.uint8)
    else:
        raise ValueError(f"Unsupported bit depth: {bit_depth}. Use 8 or 12.")


def block_reader(stream, size, history, bit_depth=8):
    """Read fixed-sized blocks from a stream of raw SDR samples.

    Parameters
    ----------
    stream : file-like object
        Raw I/Q interleaved data.
    size : int
        Size of the blocks that should be generated.
    history : int
        Number of samples from the end of the previous block that should be
        included at the start of a new block.
    bit_depth : int
        ADC bit depth. 8 for RTL-SDR legacy (default), 12 for Airspy.

    Yields
    ------
    timestamp : float
    block_idx : int
    data : :class:`Signal`
    """
    new = size - history
    data = np.zeros(size)
    for block_idx, block in enumerate(_raw_block_reader(stream, new,
                                                          bit_depth=bit_depth)):
        new_data = raw_to_complex(block, bit_depth=bit_depth)
        data = np.concatenate([data[-history:], new_data])
        yield time.time(), block_idx, Signal(data)


def card_reader(stream, bit_depth=None):
    """Read blocks from .card file.

    Supports both v1 (uint8, RTL-SDR) and v2 (int16, Airspy) formats.
    Auto-detects format from header.

    v2 format header: '#v2 bit_depth=12 sample_rate=6000000'
    v1 format: no header line (legacy RTL-SDR).

    Parameters
    ----------
    stream : file-like object
    bit_depth : int or None
        If None, auto-detect from file header.

    Yields
    ------
    timestamp : float
    block_idx : int
    data : :class:`Signal`
    """
    detected_bit_depth = bit_depth
    metadata = {}

    while True:
        line = stream.readline()
        if len(line) == 0:
            break
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith(_V2_HEADER_PREFIX):
            # Parse v2 metadata
            for kv in line[len(_V2_HEADER_PREFIX):].strip().split():
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    metadata[k] = v
            if detected_bit_depth is None and 'bit_depth' in metadata:
                detected_bit_depth = int(metadata['bit_depth'])
            continue
        if line[0] == '#' or line[0] == '\n':
            continue
        if line.startswith('Using Volk machine:') or line.startswith('linux;'):
            continue
        timestamp, idx, encoded = line.rstrip('\n').split(' ')
        raw_bytes = base64.b64decode(encoded)

        # Default: if not set from header, use bit_depth arg or fall back to 8
        bd = detected_bit_depth if detected_bit_depth is not None else 8
        dtype = np.int16 if bd == 12 else np.uint8
        raw = np.frombuffer(raw_bytes, dtype=dtype)
        data = raw_to_complex(raw, bit_depth=bd)
        yield float(timestamp), int(idx), Signal(data)


def card_writer(stream, timestamp, block_idx, block, bit_depth=12,
                sample_rate=6_000_000):
    """Write a single block to .card format.

    Parameters
    ----------
    stream : file-like object (text mode)
    timestamp : float
    block_idx : int
    block : :class:`numpy.ndarray` of complex64
    bit_depth : int
        12 for Airspy (default), 8 for RTL-SDR.
    sample_rate : int
    """
    raw = complex_to_raw(block, bit_depth=bit_depth)
    encoded = base64.b64encode(raw.tobytes()).decode('ascii')
    stream.write(f"{timestamp:.6f} {block_idx} {encoded}\n")


def write_card_header(stream, bit_depth=12, sample_rate=6_000_000):
    """Write v2 .card file header.

    Parameters
    ----------
    stream : file-like object (text mode)
    """
    stream.write(f"{_V2_HEADER_PREFIX}bit_depth={bit_depth} "
                 f"sample_rate={sample_rate}\n")
