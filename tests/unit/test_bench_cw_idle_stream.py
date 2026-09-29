# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""The CW bench sweep with an operator who takes their time.

The bench keeps one RX stream running for the whole sweep, so while it
waits at ``Enter = measure`` nobody reads the driver's bounded queue.  At
10 MSPS that queue holds 4 s; a longer wait used to fill it, log
``read_sync buffer full ... consumer too slow`` and count software drops.

These tests run the real ``bench.cmd_sweep``, ``bench.Airspy`` and
``AirspyR2Device`` against a virtual-time libairspy
(``tests/mocks/simulated_libairspy.py``), so a 10 s prompt costs
microseconds.  Idle transfers carry near-full-scale *poison*: anything
that leaks from idle time into a measurement shows up as clipping and as
a wrong carrier level.
"""

import csv
import importlib.util
import logging
import math
from pathlib import Path

import numpy as np
import pytest

from tests.mocks.simulated_libairspy import CHUNK_PAIRS, SimulatedLibairspy
from thriftyx.hal import airspy_mini as am
from thriftyx.hal.airspy_r2 import AirspyR2Device

_SPEC = importlib.util.spec_from_file_location(
    'bench_cw_level',
    Path(__file__).parents[2] / 'scripts' / 'bench_cw_level.py')
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

RATE = 2_500_000            # an R2 rate; the driver's cap is 4 s = 20 M int16
NOISE_DBFS_HZ = -110.0
IDLE_SECONDS = 10.0         # the operator's wait: well beyond the 4 s cap
SECONDS = 0.4               # per capture (the hardware run uses 5)
SETTLE = 0.1
# Generator level (as the bench is told) -> tone level at the receiver.
TONE_DBFS = {None: None, -100.0: -50.0, -80.0: -30.0, -60.0: -10.0}


def _longest_zero_run(z):
    """Longest run of exactly-zero complex samples (noise has none)."""
    zero = np.concatenate(([False], np.asarray(z) == 0, [False]))
    edges = np.flatnonzero(np.diff(zero.astype(np.int8)))
    return int((edges[1::2] - edges[0::2]).max(initial=0))


def expected_segments(rate=RATE, seconds=SECONDS):
    """Welch segments of an undisturbed capture (blocks of 8 segments)."""
    nfft = bench.fft_size(rate)
    full, rest = divmod(int(seconds * rate), nfft * 8)
    return full * 8 + rest // nfft


class Rig:
    """The bench, the driver and the virtual libairspy wired together."""

    def __init__(self, monkeypatch, tmp_path, register_read_seconds=0.0,
                 device=None):
        self.lib = SimulatedLibairspy(
            RATE, noise_n0=10 ** (NOISE_DBFS_HZ / 10),
            register_read_seconds=register_read_seconds)
        monkeypatch.setattr(am, '_lib', self.lib)
        self.dev = device(self.lib) if device else AirspyR2Device()
        self.dev._stream_event = self.lib.event
        monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                            lambda *_a, **_k: self.dev)
        monkeypatch.setattr(bench.time, 'sleep', lambda _s: None)
        self.monkeypatch = monkeypatch
        self.out = tmp_path / 'results.csv'
        self.at_enter = []          # driver state as the operator presses Enter
        # What every block that reaches the PSD looks like.  Idle transfers
        # are near-full-scale poison and a lost transfer is a run of exact
        # zeros: neither may ever be measured, counted or not.
        self.longest_zero_run = 0
        self.peak = 0.0
        real_add = bench.Accumulator.add

        def spying_add(acc, z):
            self.longest_zero_run = max(self.longest_zero_run,
                                        _longest_zero_run(z))
            self.peak = max(self.peak, float(np.abs(z.real).max()),
                            float(np.abs(z.imag).max()))
            real_add(acc, z)
        monkeypatch.setattr(bench.Accumulator, 'add', spying_add)
        # close() -> _stop_rx() zeroes the drop counters, so the end-of-run
        # figures are taken just before it.
        self.final = {}
        real_close = self.dev.close

        def close_and_record():
            self.final = {'software': self.dev.software_dropped_samples,
                          'dropped': self.dev.dropped_samples}
            real_close()
        self.dev.close = close_and_record

    def args(self, command, *extra):
        args = bench.build_parser().parse_args(
            [command, '--unit', 'R2-SIM', '--device', 'airspy_r2',
             '--rate', str(RATE), '--stages', '0/0/8', '--packing',
             '--bias-tee', '--amp-gain', '20', '--seconds', str(SECONDS),
             '--settle', str(SETTLE), '--out', str(self.out), *extra])
        bench._defaults(args)
        return args

    def operator(self, levels, idle=IDLE_SECONDS):
        """Answer the prompts: wait *idle* s, set the next level, Enter."""
        remaining = iter(levels)

        def fake_input(_prompt):
            self.lib.advance(idle)
            self.at_enter.append({
                'queued': self.dev._stream_total,
                'software': self.dev.software_dropped_samples,
                'idle_chunks': self.lib.idle_chunks})
            self.lib.set_tone_dbfs(TONE_DBFS[next(remaining)])
            return ''
        self.monkeypatch.setattr('builtins.input', fake_input)

    def sweep(self, levels=(None, -100.0, -80.0, -60.0), idle=IDLE_SECONDS):
        self.operator(levels, idle)
        text = ','.join('off' if tx is None else f'{tx:g}' for tx in levels)
        assert bench.cmd_sweep(self.args('sweep', f'--levels={text}')) == 0
        assert self.longest_zero_run < 16, "a zero gap reached the PSD"
        assert self.peak < bench.NEAR_FULL_SCALE, "idle poison reached the PSD"
        return self.rows()

    def rows(self):
        with open(self.out, newline='') as f:
            return list(csv.DictReader(f))


@pytest.fixture
def rig(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    yield rig
    rig.dev.close()


def assert_clean_row(row, tone_dbfs):
    """An undisturbed measurement of a tone at *tone_dbfs* (or none).

    The simulated scatter is a few thousandths of a dB, so the bounds are
    tight enough to see a leaked zero gap of a fraction of a transfer.
    """
    assert int(row['dropped']) == 0
    assert int(row['segments']) == expected_segments()
    # Poison from idle time would clip and wreck every figure below.
    assert float(row['near_fs_frac']) == 0.0
    assert float(row['noise_dbfs_hz']) == pytest.approx(NOISE_DBFS_HZ,
                                                        abs=0.1)
    power = 10 ** (NOISE_DBFS_HZ / 10) * RATE
    if tone_dbfs is None:
        assert row['detected'] == '0'
    else:
        power += 10 ** (tone_dbfs / 10)
        assert row['detected'] == '1'
        assert float(row['carrier_dbfs']) == pytest.approx(tone_dbfs, abs=0.05)
        assert float(row['cn0_dbhz']) == pytest.approx(
            tone_dbfs - NOISE_DBFS_HZ, abs=0.1)
        assert abs(float(row['freq_error_ppm'])) < 0.05
    assert float(row['rms_dbfs']) == pytest.approx(
        10 * math.log10(power), abs=0.05)


# ---------------------------------------------------------------------
# The bug and its fix
# ---------------------------------------------------------------------

def test_long_prompts_do_not_overflow_and_measurements_are_clean(
        rig, caplog):
    """Test A/B/E: OFF, -100, -80, -60 with a 10 s wait before each level."""
    with caplog.at_level(logging.WARNING, logger='thriftyx.hal.airspy_mini'):
        rows = rig.sweep()

    assert 'buffer full' not in caplog.text
    assert rig.final == {'software': 0, 'dropped': 0}
    assert len(rows) == 4
    for row, tx in zip(rows, TONE_DBFS, strict=True):
        assert_clean_row(row, TONE_DBFS[tx])
    # The waits were real stream time: transfers arrived and were discarded.
    cap_chunks = rig.dev._max_stream_values // (2 * CHUNK_PAIRS)
    assert rig.lib.idle_chunks > 3 * cap_chunks
    # Nothing queued up at any prompt, however long the operator took.
    assert [s['queued'] for s in rig.at_enter] == [0, 0, 0, 0]
    assert [s['software'] for s in rig.at_enter] == [0, 0, 0, 0]


def test_levels_do_not_leak_into_each_other(rig):
    """Test E: each row shows its own level, in any order of levels."""
    rows = rig.sweep(levels=(-60.0, None, -100.0, -80.0, -60.0))
    for row, tx in zip(rows, (-60.0, None, -100.0, -80.0, -60.0),
                       strict=True):
        assert_clean_row(row, TONE_DBFS[tx])


def test_rx_starts_once_and_stops_only_at_close(rig):
    """Test F: one persistent stream, no restart at any prompt."""
    rig.sweep()
    lib = rig.lib
    assert lib.start_calls == 1
    assert lib.busy_errors == 0
    assert lib.stop_calls == 1                    # from close(), at the end
    assert lib.calls.index('start_rx') < lib.calls.index('stop_rx')
    # The registers are read with RX running (after the settle read), so
    # they include the tuner's start-up calibration.
    assert lib.calls.index('start_rx') < lib.calls.index('r820t_read')
    assert lib.calls[-2:] == ['stop_rx', 'close']
    assert lib.calls.count('start_rx') == 1
    # The receiver was configured before, and never during, the stream.
    for setup in ('set_samplerate', 'set_packing', 'set_freq',
                  'set_rf_bias'):
        assert lib.calls.index(setup) < lib.calls.index('start_rx')
        assert lib.calls.count(setup) == 1


def test_first_prompt_precedes_the_stream(rig):
    """RX starts inside the first capture, so prompt 1 sees no traffic."""
    rig.sweep()
    assert rig.at_enter[0]['idle_chunks'] == 0
    assert rig.at_enter[1]['idle_chunks'] > 0


def test_gain_and_registers_are_recorded_with_the_row(rig):
    rows = rig.sweep(levels=(-100.0,))
    regs = rows[0]['registers'].split()
    assert len(regs) == 0x20                       # 0x00..0x1F
    assert regs[0] == '0x00=0x96' and regs[0x1F] == '0x1F=0x5F'


# ---------------------------------------------------------------------
# Control: the behaviour this replaces
# ---------------------------------------------------------------------

def test_control_without_pausing_reproduces_the_reported_symptoms(
        rig, caplog):
    """The simulation is faithful: with buffering never paused, exactly the
    console and CSV of the bug report appear -- buffer-full warnings at the
    prompts (not the first), software drops, yet clean CSV rows, because
    the measurement boundary already excluded the idle-phase drops."""
    rig.monkeypatch.setattr(am.AirspyMiniDevice, 'pause_buffering',
                            lambda self: None)
    with caplog.at_level(logging.WARNING, logger='thriftyx.hal.airspy_mini'):
        rows = rig.sweep()

    assert rig.final['software'] > 0
    assert rig.at_enter[0]['software'] == 0        # prompt 1: no stream yet
    assert all(s['software'] > 0 for s in rig.at_enter[1:])
    assert all(s['queued'] > 0 for s in rig.at_enter[1:])
    assert caplog.text.count('buffer full') >= 3
    for row, tx in zip(rows, TONE_DBFS, strict=True):
        assert_clean_row(row, TONE_DBFS[tx])       # boundary held


def test_register_read_that_outlasts_the_buffer_cannot_overflow(
        monkeypatch, tmp_path, caplog):
    """Test D: 32 control transfers of 0.25 s each (8 s, twice the 4 s cap)
    run with nobody consuming; the registers still come back, nothing
    overflows, and the setup phase leaves no trace in the row."""
    rig = Rig(monkeypatch, tmp_path, register_read_seconds=0.25)
    try:
        with caplog.at_level(logging.WARNING,
                             logger='thriftyx.hal.airspy_mini'):
            rows = rig.sweep(levels=(-80.0,))
        assert 'buffer full' not in caplog.text
        assert rig.final == {'software': 0, 'dropped': 0}
        assert len(rows[0]['registers'].split()) == 0x20
        cap_chunks = rig.dev._max_stream_values // (2 * CHUNK_PAIRS)
        assert rig.lib.idle_chunks > 2 * cap_chunks     # 8 s against 4 s
        assert_clean_row(rows[0], TONE_DBFS[-80.0])
    finally:
        rig.dev.close()


def test_control_register_read_overflows_without_pausing(
        monkeypatch, tmp_path, caplog):
    """The same slow register read, buffering left running: it overflows
    (what the earlier drain thread guarded against), yet the row is clean."""
    rig = Rig(monkeypatch, tmp_path, register_read_seconds=0.25)
    monkeypatch.setattr(am.AirspyMiniDevice, 'pause_buffering',
                        lambda self: None)
    try:
        with caplog.at_level(logging.WARNING,
                             logger='thriftyx.hal.airspy_mini'):
            rows = rig.sweep(levels=(-80.0,), idle=0.0)
        assert 'buffer full' in caplog.text
        assert rig.final['software'] > 0
        assert_clean_row(rows[0], TONE_DBFS[-80.0])
    finally:
        rig.dev.close()


# ---------------------------------------------------------------------
# Real losses inside the measurement are still found
# ---------------------------------------------------------------------

def _measurement_chunk(rig, block=1, chunk_in_block=1):
    """Signal-chunk index of a chunk inside a measurement block."""
    settle_chunks = math.ceil(int(SETTLE * RATE) / CHUNK_PAIRS)
    nfft = bench.fft_size(RATE)
    return settle_chunks + block * (nfft * 8 // CHUNK_PAIRS) + chunk_in_block


def test_hardware_drop_during_measurement_is_counted_and_excluded(
        rig, capsys):
    """Test C: libairspy reports 2 transfers lost inside the second block."""
    lost = 2 * CHUNK_PAIRS
    rig.lib.drop_plan[_measurement_chunk(rig)] = lost
    rows = rig.sweep(levels=(-80.0,))

    row = rows[0]
    assert int(row['dropped']) == lost
    assert int(row['segments']) == expected_segments() - 8   # one block
    # The zero-filled gap is excluded, so the tone level is unharmed.
    assert float(row['carrier_dbfs']) == pytest.approx(-30.0, abs=0.3)
    assert float(row['near_fs_frac']) == 0.0
    assert row['notes'] == ''                       # hardware, not overflow
    out = capsys.readouterr().out
    assert f'dropped {lost}' in out
    assert 'samples were dropped during the measurement' in out


def test_clean_capture_after_a_drop_is_not_affected(rig):
    """Test E: a drop in one row does not leak into the rows after it."""
    rig.lib.drop_plan[_measurement_chunk(rig)] = CHUNK_PAIRS
    rows = rig.sweep(levels=(-100.0, -80.0, -60.0))
    assert int(rows[0]['dropped']) == CHUNK_PAIRS
    for row, tx in zip(rows[1:], (-80.0, -60.0), strict=True):
        assert_clean_row(row, TONE_DBFS[tx])


def test_consumer_stall_during_measurement_is_still_a_reported_overflow(
        rig, caplog, capsys):
    """Test C, software side: a consumer that really is too slow (a 5 s
    stall inside the measurement, beyond the 4 s cap) overflows the queue.
    That is a genuine loss: counted, logged, noted in the row, and none of
    the stalled stream is measured."""
    _stall_in_second_block(rig)

    with caplog.at_level(logging.WARNING, logger='thriftyx.hal.airspy_mini'):
        rows = rig.sweep(levels=(-80.0,))

    row = rows[0]
    assert rig.final['software'] > 0
    assert int(row['dropped']) == rig.final['software']
    assert row['notes'] == f"{rig.final['software']} of them buffer overflow"
    assert 'buffer full' in caplog.text
    assert 'samples were dropped during the measurement' in \
        capsys.readouterr().out
    assert float(row['near_fs_frac']) == 0.0        # no poison measured
    assert float(row['carrier_dbfs']) == pytest.approx(-30.0, abs=0.3)


def test_buffering_is_paused_again_after_every_capture_even_a_failed_one(rig):
    """The receiver leaves the stream paused whenever it is not measuring,
    also when a capture raises."""
    receiver = bench.open_receiver(rig.args('measure', '--tx-dbm=-80'))
    setting = {'lna': 0, 'mixer': 0, 'vga': 8}
    acc = bench.Accumulator(bench.fft_size(RATE))

    receiver.capture(setting, acc, SECONDS, SETTLE)
    assert rig.dev._buffering_paused

    rig.monkeypatch.setattr(
        bench, 'raw_to_complex',
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError, match='boom'):
        receiver.capture(setting, acc, SECONDS, SETTLE)
    assert rig.dev._buffering_paused
    receiver.close()


def test_a_clean_five_second_ten_msps_capture_has_381_segments():
    """Requirement 6 in numbers: nfft 131072, blocks of 8 segments."""
    assert bench.fft_size(10_000_000) == 131072
    assert expected_segments(10_000_000, 5.0) == 381


def _stall_in_second_block(rig, seconds=5.0):
    """Make the consumer 'busy' for *seconds* inside the second block."""
    real_add = bench.Accumulator.add
    calls = {'n': 0}

    def stalling_add(acc, z):
        calls['n'] += 1
        if calls['n'] == 2:
            rig.lib.advance(seconds)
        real_add(acc, z)
    rig.monkeypatch.setattr(bench.Accumulator, 'add', stalling_add)


def test_only_the_stalled_capture_reports_an_overflow(rig):
    """The overflow count in ``notes`` is per capture, never carried over."""
    _stall_in_second_block(rig)
    rows = rig.sweep(levels=(-80.0, -60.0))
    software = rig.final['software']
    assert software > 0
    assert int(rows[0]['dropped']) == software
    assert rows[0]['notes'] == f'{software} of them buffer overflow'
    assert rows[1]['notes'] == ''
    assert_clean_row(rows[1], TONE_DBFS[-60.0])


def test_hardware_and_overflow_losses_are_told_apart(rig):
    """A stall (buffer overflow) and, in a later block, a USB loss: both
    are in ``dropped``, only the former in the ``notes`` count."""
    _stall_in_second_block(rig)
    lost = CHUNK_PAIRS
    rig.lib.drop_plan[_measurement_chunk(rig, block=2)] = lost
    rows = rig.sweep(levels=(-80.0,))
    software = rig.final['software']
    assert software > 0
    assert int(rows[0]['dropped']) == software + lost
    assert rows[0]['notes'] == f'{software} of them buffer overflow'
    assert int(rows[0]['segments']) == expected_segments() - 8   # USB block


def test_hardware_losses_outside_the_measurement_stay_out_of_the_row(rig):
    """Test C/D, hardware side: every idle and register-phase transfer
    reports a loss, and one more is lost during the settle read.  The
    driver counts them all; none belongs to the measurement."""
    rig.lib.idle_drop_pairs = 12345
    rig.lib.drop_plan[0] = CHUNK_PAIRS                     # in the settle read
    rows = rig.sweep(levels=(-100.0, -80.0))
    assert rig.final['dropped'] > 0 and rig.final['software'] == 0
    for row, tx in zip(rows, (-100.0, -80.0), strict=True):
        assert_clean_row(row, TONE_DBFS[tx])
        assert row['notes'] == ''


def test_a_failed_register_read_still_measures_cleanly(rig, monkeypatch):
    """A libairspy without airspy_r820t_read: the row says the registers
    are unavailable, and the boundary is still taken, so nothing stays
    paused (the next read_sync would raise) and the rows are clean."""
    monkeypatch.delattr(SimulatedLibairspy, 'airspy_r820t_read')
    rows = rig.sweep(levels=(-100.0, -80.0))
    for row, tx in zip(rows, (-100.0, -80.0), strict=True):
        assert row['registers'].startswith('unavailable')
        assert_clean_row(row, TONE_DBFS[tx])


class RacingDevice(AirspyR2Device):
    """Delivers one hardware-loss transfer at the worst moment.

    Right after the measurement boundary (the second ``resume_buffering``
    of a capture: the first is the settle read) the *next read of the drop
    counter* is preceded by a transfer that reports a loss, as a libairspy
    consumer thread could deliver it at any instant.
    """

    def __init__(self, lib, lost_pairs):
        self._fire = lambda: lib._deliver(lib._signal_chunk(), lost_pairs)
        self._resumes = 0
        self._armed = False
        super().__init__()

    @property
    def dropped_samples(self):
        if self._armed:
            self._armed = False
            self._fire()
        return self._dropped

    @dropped_samples.setter
    def dropped_samples(self, value):
        self._dropped = value

    def resume_buffering(self):
        baseline = super().resume_buffering()
        self._resumes += 1
        self._armed = self._resumes == 2
        return baseline


def test_a_loss_arriving_right_after_the_boundary_is_counted_and_excluded(
        monkeypatch, tmp_path):
    """A loss delivered immediately after the boundary belongs to the
    measurement: counted, its gap excluded.  (This guards the bench's use
    of the boundary -- baseline from resume_buffering(), not a separate
    counter read.  That the driver's count-and-queue and boundary are each
    one critical section is proven by TestBoundaryAtomicity in
    test_airspy_streaming_core.py.)"""
    lost = CHUNK_PAIRS
    rig = Rig(monkeypatch, tmp_path,
              device=lambda lib: RacingDevice(lib, lost))
    try:
        rows = rig.sweep(levels=(-80.0,))
    finally:
        rig.dev.close()
    assert int(rows[0]['dropped']) == lost
    assert float(rows[0]['carrier_dbfs']) == pytest.approx(-30.0, abs=0.3)
