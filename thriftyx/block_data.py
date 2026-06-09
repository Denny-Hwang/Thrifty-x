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
import logging
import time

import numpy as np

from thriftyx.signal_utils import Signal

logger = logging.getLogger(__name__)

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

    Input must be a 1D array of interleaved samples ``[I0, Q0, I1, Q1, ...]``.
    Output is a contiguous 1D ``complex64`` array ``[I0+jQ0, I1+jQ1, ...]``.

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
    if bit_depth not in (8, 12):
        raise ValueError(f"Unsupported bit depth: {bit_depth}. Use 8 or 12.")
    if data.ndim != 1:
        raise ValueError(
            f"raw_to_complex expects a 1D array, got shape {data.shape}")
    if data.size % 2 != 0:
        raise ValueError(
            f"RTL-SDR raw I/Q data must have even length, got {data.size}")

    floats = data.astype(np.float32, copy=False)
    if bit_depth == 12:
        # Airspy INT16_IQ: signed int16 in NATIVE 12-bit range (-2048..+2047).
        # libairspy does NOT left-shift; the int16 container is used because the
        # internal FIR filter output can briefly exceed the raw 12-bit ADC range.
        # Normalize to [-1, +1] by dividing by 2048 (12-bit signed full scale).
        # Empirical verification: low-4-bit nibble of recorded samples shows all
        # 16 distinct values (would be a single value if left-shifted x16).
        floats = floats / 2048.0
    else:
        # RTL-SDR legacy: 8-bit unsigned, DC offset at 127.4.
        floats = (floats - 127.4) / 128.0

    pairs = floats.reshape(-1, 2)
    values = np.empty(pairs.shape[0], dtype=np.complex64)
    values.real = pairs[:, 0]
    values.imag = pairs[:, 1]
    return values


def complex_to_raw(array, bit_depth=8):
    """Convert complex array back to I/Q interleaved samples.

    Inverse of :func:`raw_to_complex`. Output is a 1D interleaved
    ``[I0, Q0, I1, Q1, ...]`` array.

    Parameters
    ----------
    array : :class:`numpy.ndarray` of `numpy.complex64`
    bit_depth : int
        ADC bit depth. 8 for RTL-SDR legacy (default), 12 for Airspy.

    Returns
    -------
    :class:`numpy.ndarray` of `numpy.int16` or `numpy.uint8`
    """
    if bit_depth not in (8, 12):
        raise ValueError(f"Unsupported bit depth: {bit_depth}. Use 8 or 12.")

    array = np.asarray(array, dtype=np.complex64)
    if array.ndim != 1:
        raise ValueError(
            f"complex_to_raw expects a 1D array, got shape {array.shape}")

    interleaved = np.empty(array.size * 2, dtype=np.float32)
    interleaved[0::2] = array.real
    interleaved[1::2] = array.imag

    if bit_depth == 12:
        # Inverse of raw_to_complex 12-bit path: multiply by 2048 to map [-1, +1]
        # back to 12-bit signed range. Clip to int16 storage limits (the wider
        # int16 envelope is preserved so FIR-overshoot test signals round-trip).
        scaled = interleaved * 2048.0
        return np.clip(scaled, -32768, 32767).astype(np.int16)
    scaled = interleaved * 128.0 + 127.4
    return np.clip(scaled, 0, 255).astype(np.uint8)


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
    data = np.zeros(size, dtype=np.complex64)
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
        Fallback bit depth for files without a ``#v2`` header (v1 files).
        When the file carries a ``#v2 bit_depth=…`` header, the header
        always wins; a conflicting explicit value is ignored with a
        warning.  ``None`` means "headerless files are 8-bit (v1)".

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
            if 'bit_depth' in metadata:
                header_bit_depth = int(metadata['bit_depth'])
                if (detected_bit_depth is not None
                        and detected_bit_depth != header_bit_depth):
                    logger.warning(
                        "card bit_depth=%d from #v2 header overrides "
                        "configured bit_depth=%d",
                        header_bit_depth, detected_bit_depth)
                detected_bit_depth = header_bit_depth
            continue
        if not line or line[0] == '#' or line[0] == '\n':
            continue
        if line.startswith('Using Volk machine:') or line.startswith('linux;'):
            continue
        try:
            timestamp, idx, encoded = line.rstrip('\n').split(' ')
        except ValueError:
            raise ValueError(
                "Malformed .card line: expected 'timestamp index data', "
                "got: {!r}".format(line.rstrip('\n'))
            )
        raw_bytes = base64.b64decode(encoded)

        # Default: if not set from header, use bit_depth arg or fall back to 8
        bd = detected_bit_depth if detected_bit_depth is not None else 8
        dtype = np.int16 if bd == 12 else np.uint8
        raw = np.frombuffer(raw_bytes, dtype=dtype)
        data = raw_to_complex(raw, bit_depth=bd)
        yield float(timestamp), int(idx), Signal(data)


def card_writer(stream, timestamp, block_idx, block, bit_depth=8,
                sample_rate=2_400_000):
    """Write a single block to .card format.

    Parameters
    ----------
    stream : file-like object (text mode)
    timestamp : float
    block_idx : int
    block : :class:`numpy.ndarray` of complex64
    bit_depth : int
        8 for RTL-SDR (default), 12 for Airspy.
    sample_rate : int
    """
    raw = complex_to_raw(block, bit_depth=bit_depth)
    encoded = base64.b64encode(raw.tobytes()).decode('ascii')
    stream.write(f"{timestamp:.6f} {block_idx} {encoded}\n")


def write_card_header(stream, bit_depth=8, sample_rate=2_400_000):
    """Write v2 .card file header.

    Parameters
    ----------
    stream : file-like object (text mode)
    bit_depth : int
        8 for RTL-SDR (default), 12 for Airspy.
    sample_rate : int
    """
    stream.write(f"{_V2_HEADER_PREFIX}bit_depth={bit_depth} "
                 f"sample_rate={sample_rate}\n")
