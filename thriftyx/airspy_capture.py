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
import errno
import logging
import os
import shutil
import signal
import subprocess
import sys
import time

import numpy as np

from thriftyx import settings as settings_module
from thriftyx.block_data import (write_card_header, raw_to_complex)
from thriftyx import config_validator
from thriftyx.hal.profiles import get_profile
from thriftyx.carrier_detect import detect as carrier_detect_block
from thriftyx.exceptions import (EXIT_CONFIG, DeviceNotFoundError,
                                  DeviceConfigError, DeviceCaptureError,
                                  ConfigValidationError)
from thriftyx.signal_utils import compute_fft


# Per-detection fsync is microSD-hostile on Pi-class deployments.
# Flush at most once per FLUSH_INTERVAL_S seconds, or every
# FLUSH_BLOCKS detections — whichever comes first.
FLUSH_INTERVAL_S = 1.0
FLUSH_BLOCKS = 32

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stdout_is_tty():
    """Return ``True`` when stdout is an interactive terminal.

    Defensive against a closed or non-standard stream (returns ``False``).
    """
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


class CardSink:
    """Destination for detected blocks: one ``.card`` stream or file set.

    Writes the ``#v2`` header at the top of every file, flushes at most
    every :data:`FLUSH_INTERVAL_S` seconds or :data:`FLUSH_BLOCKS` lines
    (per-line fsync is microSD-hostile), and optionally rotates files.

    Parameters
    ----------
    stream : file-like or None
        An already-open text stream (stdout, a test buffer).  Never
        closed by the sink.
    path : str or None
        Output path, opened by :meth:`start` -- after the device has
        been configured, so a capture that cannot start does not
        truncate an existing file.
    rotate : float or None
        Start a new file every *rotate* seconds, on wall-clock
        boundaries (every receiver switches files at the same moments).
        *path* is then a :func:`time.strftime` pattern naming each file
        by its start time.  Block indices continue across files, so
        sample-of-arrival stays continuous for the whole run.
    """

    def __init__(self, stream=None, path=None, rotate=None,
                 clock=time.time):
        if (stream is None) == (path is None):
            raise ValueError("CardSink needs exactly one of stream/path")
        if rotate is not None and path is None:
            raise ValueError("rotation needs an output path")
        self._stream = stream
        self._path = path
        self._rotate = rotate
        self._clock = clock
        self._file = stream
        self._header = None
        self._next_rotation = None
        self._pending = 0
        self._last_flush = clock()
        self.paths = []  # files opened so far

    @property
    def file(self):
        """The stream currently written to (``None`` before start)."""
        return self._file

    def start(self, **header):
        """Open the first file and write the header (``write_card_header``
        keyword arguments)."""
        self._header = header
        if self._path is None:
            write_card_header(self._file, **header)
        else:
            self._open_next(self._clock())

    def _open_next(self, now):
        if self._file is not None and self._file is not self._stream:
            self._file.close()
        if self._rotate is None:
            path = self._path
            self._file = open(path, 'w')
        else:
            path = time.strftime(self._path, time.localtime(now))
            self._file = _open_new(path)
            path = self._file.name
            self._next_rotation = (now // self._rotate + 1) * self._rotate
            logger.info("writing %s", path)
        self.paths.append(path)
        write_card_header(self._file, **self._header)
        self._file.flush()
        self._pending = 0

    def tick(self, now=None):
        """Rotate if a rotation boundary has passed.  Call once per block."""
        if self._next_rotation is None:
            return
        now = self._clock() if now is None else now
        if now >= self._next_rotation:
            self._open_next(now)

    def write(self, timestamp, block_idx, raw_array):
        """Write one ``timestamp block_idx base64(raw)`` line."""
        _write_card_line(self._file, timestamp, block_idx, raw_array)
        self._pending += 1
        now = self._clock()
        if (self._pending >= FLUSH_BLOCKS
                or now - self._last_flush >= FLUSH_INTERVAL_S):
            self._file.flush()
            self._pending = 0
            self._last_flush = now

    def close(self):
        """Flush, and close the file if the sink opened it."""
        if self._file is None:
            return
        try:
            self._file.flush()
        except (OSError, ValueError):
            pass
        if self._file is not self._stream:
            self._file.close()
            self._file = None


def _open_new(path):
    """Open *path* for writing without clobbering an existing file.

    Two rotations can map to the same name (a strftime pattern coarser
    than the interval, or local time falling back an hour); the later
    file then gets a ``.1``, ``.2``, ... suffix before its extension.
    The directory is created if needed: a pattern such as
    ``%Y%m%d/rx0_%H%M%S.card`` names a new one every day.
    """
    directory = os.path.dirname(path)
    if directory:  # os.makedirs('') raises
        os.makedirs(directory, exist_ok=True)
    base, ext = os.path.splitext(path)
    for n in range(1000):
        candidate = "{}.{}{}".format(base, n, ext) if n else path
        try:
            return open(candidate, 'x')
        except FileExistsError:
            pass
    raise FileExistsError(
        errno.EEXIST, "this name and its .1 to .999 variants are all "
        "taken; move old files away, or give the --rotate pattern finer "
        "time fields", path)


def _pattern_repeats(pattern, rotate, now=None, rotations=1000):
    """Whether the strftime *pattern* names two files alike when
    rotating every *rotate* seconds -- e.g. ``rx0_%Y%m%d.card`` hourly.

    Checked over the next *rotations* boundaries in UTC: local time
    repeats an hour when daylight saving time ends, which
    :func:`_open_new`'s suffix is for.
    """
    now = time.time() if now is None else now
    first = now // rotate
    try:
        names = [time.strftime(pattern, time.gmtime((first + k) * rotate))
                 for k in range(rotations + 1)]
    except (OverflowError, ValueError, OSError):
        return False  # rotations beyond any calendar never happen
    return any(a == b for a, b in zip(names[:-1], names[1:], strict=True))


def _card_sink_for(output_path, rotate=None):
    """Choose where base64 .card lines go (RTL/fastcard pattern).

    Mirrors the RTL reference ``fastcard`` (``fastcapture/fastcard_cli.c``):
    card data is emitted only when a destination is actually requested -- with
    no ``-o`` flag fastcard leaves ``out == NULL`` and writes no card data at
    all, printing diagnostics only.  Adapted to thriftyx's positional
    ``output`` argument:

      - explicit file path        -> that file (opened when capture starts)
      - ``-``                     -> stdout (explicit request, e.g. piping)
      - omitted, stdout is PIPED  -> stdout (so ``thriftyx capture | ...``
                                     keeps working)
      - omitted, stdout is a TTY  -> ``None`` (display-only: write nothing,
                                     create no file)

    Carrier-detection diagnostics are emitted to stderr by the capture
    loop regardless of this value.
    """
    if output_path is not None and output_path != '-':
        return CardSink(path=output_path, rotate=rotate)
    if output_path is None and _stdout_is_tty():
        return None
    return CardSink(stream=sys.stdout)


def _as_sink(output):
    """Accept a CardSink, an open text stream, or None."""
    if output is None or isinstance(output, CardSink):
        return output
    return CardSink(stream=output)


class _StopOnSignal:
    """Turn SIGINT/SIGTERM into a flag for the capture loop.

    The previous handlers are restored on exit, so an in-process caller
    (tests, a notebook) gets its Ctrl-C back.
    """

    def __init__(self):
        self.running = True
        self._previous = {}

    def _handler(self, _sig, _frame):
        self.running = False

    def __enter__(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.signal(sig, self._handler)
        return self

    def __exit__(self, *_exc):
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        return False


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


def _print_capture_header(config, window, device_type='rtlsdr'):
    """Print fastcard-compatible capture configuration header to stderr."""
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    constant, snr, _stddev = config.carrier_threshold
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)

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
    if get_profile(device_type).gain_stages:
        gain_mode = str(config.get('gain_mode', 'manual'))
        if gain_mode == 'manual':
            print("    gain mode: manual; LNA={} Mixer={} VGA={} "
                  "(lna_agc={}, mixer_agc={})".format(
                      int(config.get('lna_gain', 0)),
                      int(config.get('mixer_gain', 0)),
                      int(config.get('vga_gain', 0)),
                      str(config.get('lna_agc', False)).lower(),
                      str(config.get('mixer_agc', False)).lower(),
                  ), file=sys.stderr)
        else:
            print("    gain mode: {}; combined={}".format(
                      gain_mode, int(config.get('combined_gain', 0))),
                  file=sys.stderr)
        print("    bias_tee={}, ppm={:+.2f}, packing={}".format(
                  str(config.get('bias_tee', False)).lower(),
                  float(config.get('ppm', 0.0)),
                  str(config.get('packing', False)).lower(),
              ), file=sys.stderr)
    else:
        print("    gain = {:.2f} dB".format(float(config.tuner_gain)),
              file=sys.stderr)


def _print_detection_line(block_idx, peak_idx, peak_mag, threshold, noise_rms):
    """Print a fastcard-compatible per-block detection line to stderr."""
    print("block #{}: mag[{}] = {:.1f} (thresh = {:.1f}, noise = {:.1f})"
          .format(block_idx, peak_idx, peak_mag, threshold, noise_rms),
          file=sys.stderr)


def _write_card_line(output_file, timestamp, block_idx, raw_array):
    """Write one .card data line: ``timestamp block_idx base64(raw)``."""
    encoded = base64.b64encode(raw_array.tobytes()).decode('ascii')
    output_file.write("{:.6f} {} {}\n".format(timestamp, block_idx, encoded))


# ---------------------------------------------------------------------------
# RTL-SDR capture via fastcard binary (preferred)
# ---------------------------------------------------------------------------

def _capture_rtlsdr_fastcard(config, extra_args):
    """Capture from RTL-SDR by delegating to the ``fastcard`` binary.

    This replicates the original Thrifty fastcard-based capture behaviour:
    fastcard performs carrier detection in C and writes only detected blocks
    to the .card file.

    SIGINT and SIGTERM are passed on to fastcard, which stops cleanly on
    either; ``duration`` in *extra_args* stops it the same way.  Exits
    with fastcard's status when that is not 0.
    """
    window = config_validator.carrier_bins(
        config.carrier_window, config.sample_rate, int(config.block_size))
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

    # Card data goes where _card_sink_for sends it for the Python
    # capture.  Without -o fastcard writes none, and prints its status
    # text on stdout; with '-o -' that text moves to stderr, so a pipe
    # carries only card lines.
    output_path = extra_args.get('output')
    if output_path is not None:
        call.extend(['-o', output_path])
    elif not _stdout_is_tty():
        call.extend(['-o', '-'])

    logging.info("Calling %s", ' '.join(call))

    # A session leader (systemd service, setsid, ssh remote command)
    # already leads its own process group, and setpgid() on it fails
    # with EPERM.
    if os.getsid(0) != os.getpid():
        os.setpgrp()

    # The handler only passes the signal on; the wait() below reaps
    # fastcard.  Waiting in the handler hung for good: it runs inside
    # that wait(), and Popen's wait lock is not reentrant.  A signal
    # that arrives while fastcard starts is passed on once it has.
    process = None
    pending = []

    def _forward(signum, _frame):
        if process is None:
            pending.append(signum)
        elif process.returncode is None:
            try:
                os.kill(process.pid, signum)
            except ProcessLookupError:
                pass

    stop_signals = (signal.SIGINT, signal.SIGTERM)
    previous = {sig: signal.signal(sig, _forward) for sig in stop_signals}
    try:
        process = subprocess.Popen(call)
        for signum in pending:
            _forward(signum, None)
        try:
            returncode = process.wait(timeout=extra_args.get('duration'))
        except subprocess.TimeoutExpired:
            # --duration: fastcard has no such option; stop it as
            # Ctrl-C would.
            _forward(signal.SIGINT, None)
            returncode = process.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if returncode < 0:
        returncode = 128 - returncode  # killed by a signal, as a shell says
    if returncode != 0:
        sys.exit(returncode)


# ---------------------------------------------------------------------------
# RTL-SDR capture with Python carrier detection (fallback)
# ---------------------------------------------------------------------------

def _capture_rtlsdr(config, extra_args, output):
    """Capture from RTL-SDR with Python-based carrier detection.

    Reads raw uint8 I/Q data from *stdin* (piped from ``rtl_sdr``) or from a
    file, performs carrier detection on each block, and writes only detected
    blocks as ``timestamp block_idx base64`` lines of raw uint8 samples,
    preceded by a ``#v2 bit_depth=8`` header that records the capture
    geometry (readers that predate the header skip it as a comment).

    *output* is a :class:`CardSink`, an open text stream, or ``None``
    (display only).  A file sink is opened only once the first block
    has been read, and an input that ends before that (``rtl_sdr``
    finding no dongle, in a pipe) exits with status 1.
    """
    sink = _as_sink(output)
    sample_rate = int(config.sample_rate)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.capture_skip)
    duration = extra_args.get('duration')
    input_path = extra_args.get('input')
    bit_depth = 8
    thresh_coeffs = config.carrier_threshold

    window = config_validator.carrier_bins(config.carrier_window, sample_rate,
                                           block_size)

    # Determine input source
    if input_path and input_path != '-':
        input_stream = open(input_path, 'rb')
    else:
        input_stream = sys.stdin.buffer

    # Print fastcard-compatible header to stderr
    _print_capture_header(config, window)

    block_idx = 0
    detected_count = 0
    start_time = time.time()

    new_samples = block_size - block_history
    # RTL-SDR: uint8 I/Q interleaved, 1 byte per component
    bytes_per_block = new_samples * 2

    # Block 0's history must hold real samples: a raw uint8 0 decodes to
    # about -1-1j, a full-scale DC step the carrier detector fires on
    # (the Airspy's int16 zeros are silence).  It is the tail of the
    # last skipped block or, without a skip, the first samples read.
    history_bytes = block_history * 2
    history = b''
    ended = False  # the input ran out

    try:
        with _StopOnSignal() as stop:
            if capture_skip > 0:
                print("\nSkipping {} block(s)...".format(capture_skip),
                      end="", file=sys.stderr)
                sys.stderr.flush()
                skipped = 0
                while skipped < capture_skip and stop.running:
                    chunk = input_stream.read(bytes_per_block)
                    if len(chunk) < bytes_per_block:
                        ended = True
                        break
                    history = chunk[len(chunk) - history_bytes:]
                    skipped += 1
                print(" done\n", file=sys.stderr)
            else:
                history = input_stream.read(history_bytes)
                ended = len(history) < history_bytes

            # History buffer for block overlap
            history_raw = np.frombuffer(history, dtype=np.uint8)

            while stop.running and not ended:
                if (duration is not None
                        and (time.time() - start_time) >= duration):
                    break

                raw_bytes = input_stream.read(bytes_per_block)
                if len(raw_bytes) < bytes_per_block:
                    ended = True
                    break
                now = time.time()
                if sink is not None:
                    if block_idx == 0:
                        # Only now: an input that delivers nothing (a
                        # failed rtl_sdr) must not replace an existing
                        # file with a header.
                        sink.start(bit_depth=bit_depth,
                                   sample_rate=sample_rate,
                                   block_size=block_size,
                                   block_history=block_history)
                    sink.tick(now)

                new_raw = np.frombuffer(raw_bytes, dtype=np.uint8)

                # Build full block with history
                block_raw = np.concatenate([history_raw, new_raw])
                block_complex = raw_to_complex(block_raw,
                                               bit_depth=bit_depth)

                # Carrier detection via FFT (pyfftw when available)
                fft_mag = np.abs(compute_fft(block_complex))
                detected, peak_idx, peak_mag, noise_rms = \
                    carrier_detect_block(fft_mag, thresh_coeffs,
                                         window=window)

                if detected:
                    threshold = _compute_threshold(fft_mag, thresh_coeffs,
                                                   noise_rms)
                    _print_detection_line(block_idx, peak_idx, peak_mag,
                                          threshold, noise_rms)
                    detected_count += 1
                    # Card data is written only when a destination
                    # exists.  Display-only runs emit the stderr
                    # diagnostic above but no base64.
                    if sink is not None:
                        # Raw uint8 bytes, no conversion loss
                        sink.write(now, block_idx, block_raw)

                # history 0 must carry over nothing ([-0:] slices
                # everything).
                history_raw = (new_raw[-block_history * 2:]
                               if block_history > 0 else new_raw[:0])
                block_idx += 1
    finally:
        if sink is not None:
            sink.close()
        if input_stream not in (sys.stdin, sys.stdin.buffer):
            input_stream.close()

    print("\nRead {} blocks.".format(block_idx), file=sys.stderr)
    logger.info("Detected %d blocks out of %d", detected_count, block_idx)
    if ended and block_idx == 0:
        print("ERROR: the input ended before a whole block of samples "
              "arrived.  Is the dongle connected, and did rtl_sdr start?",
              file=sys.stderr)
        sys.exit(1)
    return block_idx


# ---------------------------------------------------------------------------
# Airspy capture with Python carrier detection
# ---------------------------------------------------------------------------

def _capture_airspy(config, extra_args, output):
    """Capture from a HAL device (Airspy Mini / R2) with carrier detection.

    Uses only the :class:`~thriftyx.hal.base.SDRDevice` contract, so any
    device registered with the HAL factory works.  Detected blocks are
    written in v2 .card format (``#v2`` header + ``timestamp block_idx
    base64`` lines of raw samples).

    *output* is a :class:`CardSink`, an open text stream, or ``None``
    (display only).  A file sink is opened only once the device is
    configured.

    Block *k* starts ``k * (block_size - block_history)`` samples into
    the run: the HAL zero-fills samples lost in transit, so indices
    (and hence sample-of-arrival) stay aligned after a drop.  Each line
    is stamped with the arrival time of the block's last sample when
    the driver reports it (``last_read_time``), not with the time the
    block happened to be processed.
    """
    from thriftyx.hal.device_factory import create_device

    sink = _as_sink(output)
    device_type = config.device_type
    sample_rate = int(config.sample_rate)
    center_freq = int(config.tuner_freq)
    block_size = int(config.block_size)
    block_history = int(config.block_history)
    capture_skip = int(config.capture_skip)
    duration = extra_args.get('duration')
    thresh_coeffs = config.carrier_threshold

    window = config_validator.carrier_bins(config.carrier_window, sample_rate,
                                           block_size)

    # Resolve device selector.  ``airspy_serial`` (hex/decimal) takes
    # precedence; otherwise ``--device-index`` selects by enumeration order.
    airspy_serial = config.get('airspy_serial', None)
    device_index = extra_args.get('device_index', 0)
    ppm = float(config.get('ppm', 0.0))
    create_kwargs = {'ppm': ppm} if ppm else {}
    if airspy_serial:
        create_kwargs['serial'] = airspy_serial
    elif device_index is not None and int(device_index) > 0:
        create_kwargs['device_index'] = int(device_index)

    # Pre-define counters so the ``finally`` block always sees them, even
    # when device configuration fails before the capture loop starts.
    blocks_processed = 0
    detected_count = 0

    try:
        logger.info("Opening %s device (selector=%s)",
                     device_type, create_kwargs or 'default')
        device = create_device(device_type, **create_kwargs)
        device.open()
    except DeviceNotFoundError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        print("Is the Airspy device connected? Is libairspy installed? "
              "Check udev rules / 'plugdev' group membership.",
              file=sys.stderr)
        sys.exit(1)
    except (TypeError, ValueError) as e:
        # create_device received an unsupported kwarg or invalid serial:
        # configuration, which retrying cannot fix.
        print("ERROR: {}".format(e), file=sys.stderr)
        sys.exit(EXIT_CONFIG)
    except DeviceConfigError as e:
        # open() itself can raise DeviceConfigError (e.g. the INT16_IQ
        # sample-type fail-fast); exit cleanly instead of a traceback.
        print("ERROR configuring device: {}".format(e), file=sys.stderr)
        sys.exit(1)

    dropped_total = 0
    try:
        # The device's own sample format, not the bit_depth setting,
        # decides how the raw samples on disk must be decoded.
        bit_depth = device.get_info().bit_depth

        actual_rate = device.set_sample_rate(sample_rate)
        if actual_rate and int(actual_rate) != sample_rate:
            # The header must record the rate the data was taken at.
            logger.info("sample rate %d snapped to %d", sample_rate,
                        int(actual_rate))
            sample_rate = int(actual_rate)
        # Optional 12-bit USB packing (saves USB bandwidth at the highest
        # sample rates).  Must be applied before set_center_freq /
        # set_gain so the device is fully reconfigured before streaming.
        if bool(config.get('packing', False)):
            device.set_packing(True)
        device.set_center_freq(center_freq)

        gain_mode = str(config.get('gain_mode', 'manual'))
        if gain_mode == 'manual':
            device.apply_gain_mode(
                'manual',
                lna=int(config.get('lna_gain', 0)),
                mixer=int(config.get('mixer_gain', 0)),
                vga=int(config.get('vga_gain', 0)),
                lna_agc=bool(config.get('lna_agc', False)),
                mixer_agc=bool(config.get('mixer_agc', False)),
            )
        else:
            device.apply_gain_mode(
                gain_mode,
                combined=int(config.get('combined_gain', 0)),
            )
        device.set_bias_tee(bool(config.get('bias_tee', False)))

        # Open the destination and write the v2 header -- only when card
        # data has a destination.  When display-only (no output file +
        # interactive TTY) nothing is written to disk or screen,
        # matching fastcard's ``out == NULL`` behaviour.
        if sink is not None:
            sink.start(bit_depth=bit_depth, sample_rate=sample_rate,
                       block_size=block_size, block_history=block_history)

        # Print fastcard-compatible configuration header (always, to stderr)
        _print_capture_header(config, window, device_type=device_type)

        start_time = time.time()
        new_samples = block_size - block_history

        # Persistent history buffer: always exactly block_history * 2 int16
        # values.  Initialised to zeros for the first block (no prior data).
        history_raw = np.zeros(block_history * 2, dtype=np.int16)

        with _StopOnSignal() as stop:
            if capture_skip > 0:
                print("\nSkipping {} block(s)...".format(capture_skip),
                      end="", file=sys.stderr)
                sys.stderr.flush()
                blocks_skipped = 0
                while blocks_skipped < capture_skip and stop.running:
                    raw = device.read_sync(new_samples)
                    if len(raw) < new_samples * 2:
                        break
                    # With correct block parameters new_samples >=
                    # block_history, so this slice always yields exactly
                    # block_history * 2 values.  (history 0 must carry
                    # over nothing: [-0:] is a full slice.)
                    history_raw = (raw[-(block_history * 2):]
                                   if block_history > 0 else raw[:0])
                    blocks_skipped += 1
                print(" done\n", file=sys.stderr)

            # Match RTL behaviour: the first processed block is index 0
            # regardless of the number of skipped blocks.
            dropped_seen = device.dropped_samples

            while stop.running:
                if (duration is not None
                        and (time.time() - start_time) >= duration):
                    break

                raw = device.read_sync(new_samples)
                if len(raw) < new_samples * 2:
                    break
                block_idx = blocks_processed
                timestamp = device.last_read_time or time.time()
                if sink is not None:
                    sink.tick()

                dropped_now = device.dropped_samples
                if dropped_now > dropped_seen:
                    dropped_total += dropped_now - dropped_seen
                    logger.warning(
                        "%d sample pairs lost before block %d (USB "
                        "overflow or a slow host); zero-filled so block "
                        "indices stay aligned", dropped_now - dropped_seen,
                        block_idx)
                    dropped_seen = dropped_now

                block_raw = np.concatenate([history_raw, raw])
                # A block of lost samples (all zeros, history included)
                # cannot hold a carrier; skipping its FFT lets capture
                # catch up after a long drop.  The first block of a drop
                # still carries the previous block's tail, and a burst
                # there peaks in this block's window, not in the last.
                detected = False
                if block_raw.any():
                    block_complex = raw_to_complex(block_raw,
                                                   bit_depth=bit_depth)
                    # Carrier detection via FFT (pyfftw when available)
                    fft_mag = np.abs(compute_fft(block_complex))
                    detected, peak_idx, peak_mag, noise_rms = \
                        carrier_detect_block(fft_mag, thresh_coeffs,
                                             window=window)

                if detected:
                    threshold = _compute_threshold(
                        fft_mag, thresh_coeffs, noise_rms)
                    _print_detection_line(block_idx, peak_idx, peak_mag,
                                          threshold, noise_rms)
                    detected_count += 1
                    # Card data is written only when a destination
                    # exists.  Display-only runs emit the stderr
                    # diagnostic above but no base64.
                    if sink is not None:
                        # v2 format line (raw int16 bytes)
                        sink.write(timestamp, block_idx, block_raw)

                # Update history from the tail of the raw read buffer.
                # (history 0 must carry over nothing: [-0:] is a full
                # slice.)
                history_raw = (raw[-(block_history * 2):]
                               if block_history > 0 else raw[:0])
                blocks_processed += 1

    except DeviceConfigError as e:
        print("ERROR configuring device: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except DeviceCaptureError as e:
        print("ERROR during capture: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        if sink is not None:
            sink.close()
        try:
            device.close()
        except Exception:
            logger.debug("device.close() raised during cleanup", exc_info=True)
        print("\nRead {} blocks.".format(blocks_processed), file=sys.stderr)
        if dropped_total:
            print("WARNING: {} sample pairs were lost and zero-filled; "
                  "detections spanning a gap are degraded".format(
                      dropped_total), file=sys.stderr)
        logger.info("Detected %d blocks out of %d",
                     detected_count, blocks_processed)

    return blocks_processed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def capture_cli(args=None):
    """Main capture CLI function.

    Supports all device types:
      - RTL-SDR:     thriftyx capture output.card --device-type rtlsdr
                     rtl_sdr -f 162M -s 2.4M - | \
                         thriftyx capture output.card --device-type rtlsdr \
                         --input -
      - Airspy Mini: thriftyx capture output.card --device-type airspy_mini
      - Airspy R2:   thriftyx capture output.card --device-type airspy_r2

    For RTL-SDR, the ``fastcard`` binary is used when available and no
    ``--input`` is given.  Otherwise a Python-based carrier detection
    fallback reads ``--input`` (default: stdin).
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('output', nargs='?', default=None,
                        help="Output .card file path ('-' for stdout, "
                             "default: stdout)")
    parser.add_argument('--input', dest='input', default=None,
                        help="Input raw binary file for RTL-SDR "
                             "('-' for stdin); read by the Python "
                             "capture, bypassing fastcard.  Default: "
                             "fastcard opens the dongle, or without "
                             "fastcard, stdin")
    parser.add_argument('--duration', dest='duration',
                        type=float, default=None,
                        help="Capture duration in seconds (default: until "
                             "Ctrl+C)")
    parser.add_argument('--fastcard', dest='fastcard', default='fastcard',
                        help="Path to fastcard binary")
    parser.add_argument('--rotate', dest='rotate', type=float,
                        default=None, metavar='SECONDS',
                        help="start a new output file every SECONDS "
                             "(on wall-clock boundaries); the output "
                             "path is then a strftime pattern, e.g. "
                             "'rx0_%%Y%%m%%dT%%H%%M%%S.card'.  Block "
                             "indices continue across files")
    parser.add_argument('-d', '--device-index', dest='device_index',
                        type=int, default=0,
                        help="0-based device enumeration index (any device "
                             "type; for Airspy, --airspy-serial takes "
                             "precedence)")

    setting_keys = ['device_type', 'sample_rate', 'tuner_freq',
                    'tuner_gain', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'bit_depth', 'bias_tee',
                    'lna_gain', 'mixer_gain', 'vga_gain',
                    'capture_skip',
                    # New Airspy options (P1 follow-ups):
                    'airspy_serial', 'gain_mode', 'combined_gain',
                    'lna_agc', 'mixer_agc', 'ppm', 'packing']
    # sample_rate and bit_depth default from the device profile of
    # device_type (settings.DEVICE_DERIVED_KEYS).  No card header can
    # replace the sample rate: chip_rate is checked against it even when
    # it is that default.
    config, extra_args = settings_module.load_args(
        parser, setting_keys, argv=args, sample_rate_final=True)

    # Validate configuration
    try:
        validation_warnings = config_validator.validate_config(config)
        for w in validation_warnings:
            logger.warning("Config warning: %s", w)
    except ConfigValidationError as e:
        print("ERROR: Invalid configuration: {}".format(e), file=sys.stderr)
        sys.exit(EXIT_CONFIG)

    device_type = config.device_type
    output_path = extra_args.get('output')
    rotate = extra_args.get('rotate')
    fastcard_path = extra_args.get('fastcard', 'fastcard')
    # fastcard reads the dongle itself; --input (a recording or stdin)
    # is read by the Python capture.
    use_fastcard = (device_type == 'rtlsdr'
                    and extra_args.get('input') is None
                    and shutil.which(fastcard_path))
    if rotate is not None:
        problem = None
        if not rotate > 0:
            problem = "--rotate needs a positive number of seconds"
        elif output_path is None or output_path == '-':
            problem = "--rotate needs an output file path"
        elif '%' not in output_path:
            problem = ("--rotate needs a strftime pattern in the output "
                       "path (e.g. rx0_%Y%m%dT%H%M%S.card), or every "
                       "file would get the same name")
        elif _pattern_repeats(output_path, rotate):
            problem = ("--rotate {:g}: the output pattern {} gives files "
                       "{:g} s apart the same name; add finer time "
                       "fields (e.g. %H%M%S)".format(rotate, output_path,
                                                    rotate))
        elif use_fastcard:
            problem = ("--rotate is not supported with the fastcard "
                       "binary; pass --fastcard '' to use the Python "
                       "capture")
        if problem:
            print("ERROR: {}".format(problem), file=sys.stderr)
            sys.exit(EXIT_CONFIG)

    try:
        if use_fastcard:
            # Prefer the fastcard binary for RTL-SDR (matches original Thrifty)
            logger.info("Using fastcard binary: %s", fastcard_path)
            _capture_rtlsdr_fastcard(config, extra_args)
        elif device_type == 'rtlsdr':
            # Python fallback: carrier detection + v2 .card format
            logger.info("using Python carrier detection (--input given, "
                        "or fastcard not found)")
            _capture_rtlsdr(config, extra_args,
                            _card_sink_for(output_path, rotate))
        else:
            # Every other (validated) device type is a HAL device.
            _capture_airspy(config, extra_args,
                            _card_sink_for(output_path, rotate))
    except BrokenPipeError:
        # Downstream consumer closed the pipe (e.g. ``head``)
        pass


# Legacy alias
_main = capture_cli
