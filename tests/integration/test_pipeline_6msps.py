# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""End-to-end: capture -> detect -> identify -> match -> tdoa -> pos at 6 MSPS.

Three simulated Airspy receivers with different clock offsets and clock
drift record a beacon at a known position and a mobile transmitter.
Each stage runs through its real CLI entry point and hands the next
stage real files, so the .card/.toad/.toads/.match/.tdoa formats are
exercised as well as the numerics.  The recovered position must match
the true one.

Signals: on-off keyed carrier bursts carrying a 10-bit Gold code at the
Thrifty chip rate, the transmitter design the pipeline expects.  The
capture loop reads them through a complete HAL device
(tests/mocks/scripted_device.py); its wall clock is replaced by the
receiver's sample clock so card timestamps behave as on NTP-synced
nodes.
"""

import sys
import types

import numpy as np
import pytest

from thriftyx import (airspy_capture, detect, gold, identify, matchmaker,
                      pos_est, settings, tdoa_est, toads_data)
from thriftyx.block_data import complex_to_raw
from thriftyx.template_generate import resample
from tests.mocks.scripted_device import ScriptedSDRDevice

FS = 6_000_000
CHIP_RATE = 0.999707e6
C = tdoa_est.SPEED_OF_LIGHT
DURATION = 0.4                                  # seconds recorded per RX

RX_POS = {0: (0.0, 0.0), 1: (1200.0, 0.0), 2: (0.0, 1000.0)}
BEACON_ID, MOBILE_ID = 0, 1
# The beacon must not be equidistant from the receivers (as the
# circumcentre (600, 500) of this layout would be): its TDOAs would all
# be zero, and a sign or index error in the beacon geometry correction
# would go unnoticed.
TX_POS = {BEACON_ID: (1000.0, 800.0), MOBILE_ID: (350.0, 250.0)}
CARRIER_HZ = {BEACON_ID: 50e3, MOBILE_ID: 120e3}
# Emission times: bursts alternate beacon / mobile every 25 ms.
EMISSIONS = ([(BEACON_ID, 0.010 + 0.05 * i) for i in range(8)]
             + [(MOBILE_ID, 0.035 + 0.05 * i) for i in range(7)])
# Receiver clocks: (true time of sample 0 [s], sample-clock error ratio).
CLOCKS = {0: (0.0, 0.0), 1: (-0.0137, 3e-6), 2: (0.0071, -2e-6)}

AMPLITUDE = 0.1       # |z|, i.e. -20 dBFS
NOISE_SIGMA = 0.01    # per I/Q component


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _receiver_stream(rxid, code, rng):
    """int16 I/Q that receiver `rxid` records over DURATION."""
    t0, clock_error = CLOCKS[rxid]
    fs_rx = FS * (1 + clock_error)
    n_samples = int(DURATION * FS)
    sig = (rng.normal(0, NOISE_SIGMA, n_samples)
           + 1j * rng.normal(0, NOISE_SIGMA, n_samples))
    n = np.arange(n_samples)
    for txid, t_emit in EMISSIONS:
        t_arrive = t_emit + _dist(TX_POS[txid], RX_POS[rxid]) / C
        start = (t_arrive - t0) * fs_rx          # fractional sample
        span = slice(int(np.floor(start)),
                     int(np.ceil(start + len(code) * fs_rx / CHIP_RATE)) + 1)
        chips = np.floor((n[span] - start) * CHIP_RATE / fs_rx).astype(int)
        on = (chips >= 0) & (chips < len(code))
        keyed = np.zeros(len(chips))
        keyed[on] = code[chips[on]]
        phase = rng.uniform(0, 2 * np.pi)
        carrier = np.exp(2j * np.pi * CARRIER_HZ[txid] * n[span] / FS + phase)
        sig[span] += AMPLITUDE * keyed * carrier
    return complex_to_raw(sig.astype(np.complex64), bit_depth=12)


def _capture(monkeypatch, rxid, stream, card_path):
    """Run the real capture loop on a simulated receiver."""
    device = ScriptedSDRDevice(stream=stream)
    t0, _ = CLOCKS[rxid]
    monkeypatch.setattr('thriftyx.hal.device_factory.create_device',
                        lambda *_a, **_k: device)
    monkeypatch.setattr(airspy_capture, 'time', types.SimpleNamespace(
        time=lambda: t0 + device.samples_read / FS))
    config = settings.Namespace(settings.load({
        'device_type': 'airspy_mini', 'sample_rate': '6M',
        'capture_skip': '0', 'tuner_freq': '433.83M'}))
    with open(card_path, 'w') as card:
        airspy_capture._capture_airspy(
            config, settings.Namespace({'duration': None}), card)


def _run(monkeypatch, module, *argv):
    monkeypatch.setattr(sys, 'argv', [module.__name__, *map(str, argv)])
    module._main()


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_six_msps_pipeline_recovers_mobile_position(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # every CLI default path lands here
    rng = np.random.default_rng(2026)
    code = np.array(gold.gold(10, 0, 'gold'), dtype=float)

    template = tmp_path / 'template.npy'
    np.save(template, resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE))
    bin_hz = FS / 32768
    (tmp_path / 'freqmap.cfg').write_text(
        "".join("{}: {:.0f} - {:.0f}\n".format(
            txid, hz / bin_hz - 8, hz / bin_hz + 8)
            for txid, hz in CARRIER_HZ.items())
        + "".join("@{}: 0\n".format(rxid) for rxid in RX_POS))
    (tmp_path / 'pos-rx.cfg').write_text(
        "".join("{}: {} {}\n".format(k, *v) for k, v in RX_POS.items()))
    (tmp_path / 'pos-beacon.cfg').write_text(
        "{}: {} {}\n".format(BEACON_ID, *TX_POS[BEACON_ID]))

    toads = []
    for rxid in RX_POS:
        card = tmp_path / 'rx{}.card'.format(rxid)
        _capture(monkeypatch, rxid, _receiver_stream(rxid, code, rng), card)
        toad = tmp_path / 'rx{}.toad'.format(rxid)
        _run(monkeypatch, detect, card, '-o', toad, '-z', template,
             '-r', rxid, '--quiet')
        toads.append(toad)
        # A burst can be detected in two overlapping blocks; identify
        # removes those duplicates below.
        assert len(toad.read_text().splitlines()) >= len(EMISSIONS), card

    _run(monkeypatch, identify, *toads, '-o', 'data.toads',
         '-m', 'freqmap.cfg')
    with open('data.toads') as toads_file:
        identified = toads_data.load_toads(toads_file)
    for rxid in RX_POS:
        for txid in CARRIER_HZ:
            expected = sum(1 for t, _ in EMISSIONS if t == txid)
            got = sum(1 for d in identified
                      if d.rxid == rxid and d.txid == txid)
            assert got == expected, (rxid, txid)
    _run(monkeypatch, matchmaker, 'data.toads', '-o', 'data.match',
         '-w', '0.01', '-n', '3')
    _run(monkeypatch, tdoa_est, 'data.toads', 'data.match', '-o', 'data.tdoa',
         '-s', '6M')
    _run(monkeypatch, pos_est, 'data.tdoa', '-o', 'data.pos')

    positions = pos_est.load_positions('data.pos')
    mobile = positions[positions['tx'] == MOBILE_ID]
    assert len(mobile) == 7
    errors = np.hypot(mobile['x'] - TX_POS[MOBILE_ID][0],
                      mobile['y'] - TX_POS[MOBILE_ID][1])
    # Sub-metre in practice (0.3-0.8 m with this seed); 3 m leaves margin
    # while still failing on a half-sample SoA error (25 m at 6 MSPS).
    assert np.max(errors) < 3.0, errors
