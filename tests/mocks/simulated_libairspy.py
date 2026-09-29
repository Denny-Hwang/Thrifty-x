# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""A virtual-time libairspy: USB streaming without USB, threads or sleeping.

Patched in as ``thriftyx.hal.airspy_mini._lib``, it lets the *real*
``AirspyMiniDevice`` (its ctypes callback, bounded queue, counters and
``read_sync``) and the *real* CW bench run through operator prompts
that last "10 s" in microseconds.

Time passes only in two ways, both synchronous:

* :meth:`SimulatedLibairspy.advance` -- the consumer is away (an operator
  prompt, a slow control transfer): transfers arrive while nobody reads.
  They carry *poison* (near full scale), so anything that leaks into a
  measurement shows up as clipping and as a wrong carrier level.
* :attr:`SimulatedLibairspy.event` -- ``read_sync`` waits for samples:
  one transfer of the tone plus noise arrives per wait.

Transfers are delivered through the callback the driver registered with
``airspy_start_rx``, as libairspy's consumer thread would.  Like the
real library, a second ``airspy_start_rx`` while streaming fails
(``AIRSPY_ERROR_BUSY`` = -6), and a hardware loss is reported with the
transfer that follows it.
"""

import ctypes
import math

import numpy as np

from thriftyx.block_data import AIRSPY_INT16_FULL_SCALE, complex_to_raw
from thriftyx.hal import airspy_mini as am

AIRSPY_ERROR_BUSY = -6

# IQ pairs per USB transfer (unpacked; libairspy: 262144 B / 2 B / 2).
CHUNK_PAIRS = 65536


class _PumpEvent:
    """``threading.Event`` stand-in: waiting lets one transfer arrive."""

    def __init__(self, lib):
        self._lib = lib

    def set(self):
        pass

    def clear(self):
        pass

    def is_set(self):
        return False

    def wait(self, timeout=None):
        self._lib.deliver_signal()
        return True


class SimulatedLibairspy:
    """Fake libairspy module driven by a virtual clock.

    Parameters
    ----------
    rate : int
        Sample rate in Hz.
    tone_offset_hz, noise_n0 : float
        Tone offset from the tuned centre, and white-noise density
        (full-scale power per Hz).  The tone amplitude is set with
        :meth:`set_tone_dbfs`.
    register_read_seconds : float
        Virtual time one ``airspy_r820t_read`` control transfer takes with
        nobody consuming the stream.
    """

    def __init__(self, rate, tone_offset_hz=15e3, noise_n0=1e-11, seed=1,
                 register_read_seconds=0.0):
        self.rate = int(rate)
        self.tone_offset_hz = tone_offset_hz
        self._sigma = math.sqrt(noise_n0 * rate / 2)
        self._rng = np.random.default_rng(seed)
        self._amp = 0.0
        self._index = 0                 # absolute index of the next sample
        self.register_read_seconds = register_read_seconds
        self.regs = {reg: 0x96 if reg == 0 else 0x40 + reg
                     for reg in range(0x20)}
        self.event = _PumpEvent(self)
        self._poison = np.full(
            2 * CHUNK_PAIRS, int(AIRSPY_INT16_FULL_SCALE) - 1, dtype=np.int16)

        self.callback = None            # set while "streaming"
        self.calls = []                 # libairspy calls, in order
        self.start_calls = 0
        self.stop_calls = 0
        self.busy_errors = 0
        self.signal_chunks = 0          # transfers delivered to read_sync
        self.idle_chunks = 0            # transfers delivered via advance()
        self.drop_plan = {}             # signal chunk -> pairs lost before it

    # -- what the "hardware" produces ----------------------------------

    @property
    def streaming(self):
        return self.callback is not None

    def set_tone_dbfs(self, level):
        """Tone level in dBFS at the receiver (``None`` = no tone)."""
        self._amp = 0.0 if level is None else 10 ** (level / 20)

    def _signal_chunk(self):
        n = np.arange(self._index, self._index + CHUNK_PAIRS)
        self._index += CHUNK_PAIRS
        noise = self._sigma * (
            self._rng.standard_normal(CHUNK_PAIRS)
            + 1j * self._rng.standard_normal(CHUNK_PAIRS))
        z = self._amp * np.exp(2j * np.pi * self.tone_offset_hz * n
                               / self.rate) + noise
        return complex_to_raw(z.astype(np.complex64), bit_depth=12)

    def _deliver(self, samples, dropped=0):
        """Call the driver's registered callback with one transfer."""
        transfer = am._AirspyTransfer(
            device=None, ctx=None,
            samples=ctypes.c_void_p(samples.ctypes.data),
            sample_count=len(samples) // 2,
            dropped_samples=dropped,
            sample_type=am.AIRSPY_SAMPLE_INT16_IQ)
        assert self.callback(ctypes.byref(transfer)) == 0

    def deliver_signal(self):
        """One transfer of the signal arrives (a consumer is waiting)."""
        assert self.streaming, "waiting for samples while RX is stopped"
        dropped = self.drop_plan.pop(self.signal_chunks, 0)
        self.signal_chunks += 1
        self._deliver(self._signal_chunk(), dropped)

    def advance(self, seconds):
        """*seconds* pass with nobody reading; the stream (if any) runs."""
        if not self.streaming:
            return
        for _ in range(math.ceil(seconds * self.rate / CHUNK_PAIRS)):
            self.idle_chunks += 1
            self._deliver(self._poison)

    # -- the libairspy API the driver calls ----------------------------

    def airspy_open(self, _handle_ptr):
        self.calls.append('open')
        return 0

    def airspy_set_sample_type(self, _handle, _sample_type):
        return 0

    def airspy_board_partid_serialno_read(self, _handle, _info):
        return -1

    def airspy_set_samplerate(self, _handle, rate):
        self.calls.append('set_samplerate')
        self.rate = int(rate.value)
        return 0

    def airspy_set_packing(self, _handle, _enabled):
        self.calls.append('set_packing')
        return 0

    def airspy_set_freq(self, _handle, _freq):
        self.calls.append('set_freq')
        return 0

    def airspy_set_rf_bias(self, _handle, _enabled):
        self.calls.append('set_rf_bias')
        return 0

    def airspy_set_lna_gain(self, _handle, _value):
        return 0

    def airspy_set_mixer_gain(self, _handle, _value):
        return 0

    def airspy_set_vga_gain(self, _handle, _value):
        return 0

    def airspy_set_lna_agc(self, _handle, _enabled):
        return 0

    def airspy_set_mixer_agc(self, _handle, _enabled):
        return 0

    def airspy_r820t_read(self, _handle, reg, value_ref):
        self.calls.append('r820t_read')
        self.advance(self.register_read_seconds)
        value_ref._obj.value = self.regs[reg.value]
        return 0

    def airspy_start_rx(self, _handle, callback, _ctx):
        self.calls.append('start_rx')
        if self.streaming:
            self.busy_errors += 1
            return AIRSPY_ERROR_BUSY
        self.callback = callback
        self.start_calls += 1
        return 0

    def airspy_stop_rx(self, _handle):
        self.calls.append('stop_rx')
        self.stop_calls += 1
        self.callback = None
        return 0

    def airspy_close(self, _handle):
        self.calls.append('close')
        return 0
