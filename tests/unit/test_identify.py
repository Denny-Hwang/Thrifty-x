# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Unit tests for thriftyx.identify — transmitter-window detection, the
--map file and the command line.

The math here is small but production-critical: an off-by-one in the
window-edge computation silently mis-labels transmitters and breaks the
downstream `match → tdoa → pos` pipeline.
"""

import io
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

from thriftyx import cli, identify, toads_data
from thriftyx.exceptions import ConfigError, FileFormatError


def test_single_cluster_yields_one_transmitter():
    """A single tightly-clustered carrier bin shows up as one tx."""
    freqs = np.array([100, 100, 100, 101, 99, 100, 100, 100], dtype=int)
    edges = identify.detect_transmitter_windows(freqs)
    # edges is [first_bin, ..., last_bin]; #txs == len(edges) - 1
    assert len(edges) - 1 == 1


def test_two_well_separated_clusters_yield_two_transmitters():
    """Two clusters with a noise gap between them are split."""
    cluster_a = np.full(40, 100, dtype=int)
    cluster_b = np.full(40, 500, dtype=int)
    freqs = np.concatenate([cluster_a, cluster_b])
    edges = identify.detect_transmitter_windows(freqs)
    assert len(edges) - 1 == 2
    # The split edge must land in the empty gap (101..499).
    inner_edge = int(edges[1])
    assert 101 < inner_edge < 500


def test_edges_bracket_the_full_bin_range():
    """First / last edge equal the min / max+1 of the input."""
    freqs = np.array([10, 10, 10, 50, 50, 50], dtype=int)
    edges = identify.detect_transmitter_windows(freqs)
    assert int(edges[0]) == 10
    # last_bin = first_bin + len(bincount) = 10 + 41 = 51
    assert int(edges[-1]) == 51


def test_unidentified_tx_sentinel_value():
    """Sentinel value is stable; downstream code relies on it being -1."""
    assert identify.UNIDENTIFIED_TX == -1


# --- the command line -------------------------------------------------------

def _toad_line(rxid, timestamp, carrier_bin, block=None):
    block = int(timestamp * 100) if block is None else block
    return ("{} {:.6f} {} {} 1000 0.1 50.0 1.0 {} 0.1 40.0 1.0\n".format(
        rxid, timestamp, block, block * 1000 + 0.5, carrier_bin))


def _write_toad(path, rxid, carrier_bins=(102, 127), start=0.0):
    path.write_text(''.join(_toad_line(rxid, start + 0.1 * i, carrier_bin)
                            for i, carrier_bin in enumerate(carrier_bins)))


def _thriftyx(monkeypatch, *argv):
    """Run ``thriftyx *argv`` in-process; return its exit status."""
    monkeypatch.setattr(sys, 'argv', ['thriftyx', *map(str, argv)])
    try:
        cli._main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_default_inputs_are_the_toad_files(tmp_path, monkeypatch):
    """Regression: the '*.toad' default was a string, so identify globbed
    '*', '.', 't', ... -- every file in the directory, then the
    directory itself -- and always failed."""
    monkeypatch.chdir(tmp_path)
    for rxid in range(3):
        _write_toad(tmp_path / 'rx{}.toad'.format(rxid), rxid)
    (tmp_path / 'freqmap.cfg').write_text('1: 100 - 105\n')
    (tmp_path / 'old.toads').write_text(
        '0 1 ' + _toad_line(0, 0.5, 102)[2:])
    assert _thriftyx(monkeypatch, 'identify') == 0
    lines = (tmp_path / 'data.toads').read_text().splitlines()
    assert lines[0] == '# source_files: [rx0.toad rx1.toad rx2.toad]'
    assert len(lines) == 1 + 6


def test_unmatched_input_is_an_error_and_keeps_the_output(
        tmp_path, monkeypatch, capsys):
    """A mistyped receiver file used to be dropped silently, and every
    failed run truncated the previous data.toads."""
    monkeypatch.chdir(tmp_path)
    _write_toad(tmp_path / 'rx0.toad', 0)
    _write_toad(tmp_path / 'rx1.toad', 1)
    (tmp_path / 'data.toads').write_text('previous result\n')
    assert _thriftyx(monkeypatch, 'identify', 'rx0.toad', 'rx1.tod') == 1
    assert 'rx1.tod' in capsys.readouterr().err
    assert (tmp_path / 'data.toads').read_text() == 'previous result\n'

    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert _thriftyx(monkeypatch, 'identify') == 1
    assert '*.toad' in capsys.readouterr().err


def test_output_to_stdout_holds_only_records(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_toad(tmp_path / 'rx0.toad', 0)
    assert _thriftyx(monkeypatch, 'identify', 'rx0.toad', '-o', '-') == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == '# source_files: [rx0.toad]'
    assert len(toads_data.load_toads(io.StringIO(out))) == 2
    assert 'Removed 0 duplicates' in err


def test_non_toad_lines_are_a_format_error(tmp_path):
    """detect's summary lines saved with `> rx0.toad` used to crash
    identify with a bare ValueError traceback."""
    path = tmp_path / 'rx0.toad'
    path.write_text(_toad_line(0, 0.1, 102)
                    + 'blk=2; carrier: yes @ 7.874 kHz /  43:+0.00, SNR = '
                      '1588 / 65 = 27.76 dB; corr: yes @ 20344-0.018\n')
    with pytest.raises(FileFormatError, match=r'rx0\.toad: line 2 is not'):
        identify.load_toad_files([str(path)])


# --- --map ------------------------------------------------------------------

def _detections(bins_by_rx):
    return [toads_data.DetectionResult(
                0.1 * i, i, 1000.0 * i, toads_data.CarrierSyncInfo(
                    carrier_bin, 0.1, 40.0, 1.0),
                toads_data.CorrDetectionInfo(1000, 0.1, 50.0, 1.0),
                rxid=rxid)
            for rxid, bins in bins_by_rx.items()
            for i, carrier_bin in enumerate(bins)]


def test_map_receivers_without_offset_line_use_zero():
    """Regression: a receiver without an '@rxid' line raised KeyError, so
    the guide's example map failed with a third receiver (and a map
    without '@' lines always failed)."""
    detections = _detections({0: [102, 127], 1: [104, 129], 2: [101, 126]})
    freqmap = identify.load_freqmap(io.StringIO(
        '1: 100 - 105\n2: 125 - 130\n'))
    assert identify.classify_transmitters(detections, freqmap) == [
        1, 2, 1, 2, 1, 2]

    # An '@' line shifts its own receiver only.
    freqmap = identify.load_freqmap(io.StringIO(
        '1: 100 - 105\n2: 125 - 130\n@1: 20\n'))
    detections = _detections({0: [102], 1: [122, 147], 2: [101]})
    assert identify.classify_transmitters(detections, freqmap) == [
        1, 1, 2, 1]


@pytest.mark.parametrize('line, match', [
    ('1: 49 - 51 kHz', 'FFT bins, not Hz'),
    ('1: 49 - 51k', 'FFT bins, not Hz'),
    ('1: 100 - 105 M', 'FFT bins, not Hz'),
    ('1: 18300Hz - 18700Hz', 'invalid line'),
    ('1: 49k - 51k', 'invalid line'),
    ('one: 100 - 105', 'invalid line'),
    ('@x: 2', 'invalid line'),
])
def test_map_errors_name_the_line(line, match):
    """Ranges with a Hz unit (which the guide used to promise) and
    malformed lines raised bare ValueErrors; an SI prefix without the
    unit ('51k') was multiplied into the bins."""
    stream = io.StringIO('0: 10 - 20\n' + line + '\n')
    stream.name = 'freqmap.cfg'
    with pytest.raises(ConfigError, match=match) as excinfo:
        identify.load_freqmap(stream)
    assert 'freqmap.cfg' in str(excinfo.value)
    assert line.split(':')[0] in str(excinfo.value)


def test_shipped_map_loads():
    path = (Path(__file__).resolve().parents[2] / 'rpi' / 'freq-map.cfg')
    with open(path) as file_:
        freqmap = identify.load_freqmap(file_)
    assert freqmap.tx_ranges[0] == (4.0, 17.0)
    assert freqmap.rx_offset == {0: 7.0, 1: 0.0, 2: 3.0, 3: 4.0}


def test_map_accepts_single_bins_and_negative_ranges():
    freqmap = identify.load_freqmap(io.StringIO('1: 42\n2: -30 - -20\n'))
    assert freqmap.tx_ranges == {1: (42.0, 42.0), 2: (-30.0, -20.0)}


# --- receivers sharing an rxid ----------------------------------------------

def test_warns_when_receivers_share_an_rxid(tmp_path, caplog):
    """The README's workflow left every receiver at rxid 0; match then
    paired nothing, without any warning."""
    for name in ('rx0.toad', 'rx1.toad'):
        _write_toad(tmp_path / name, 0)
    with caplog.at_level(logging.WARNING):
        identify.load_toad_files([str(tmp_path / '*.toad')])
    assert 'both hold rxid 0' in caplog.text


def test_no_warning_for_consecutive_files_of_one_receiver(tmp_path, caplog):
    """--rotate output: one receiver's files follow each other."""
    _write_toad(tmp_path / 'rx0_a.toad', 0, start=0.0)
    _write_toad(tmp_path / 'rx0_b.toad', 0, start=10.0)
    _write_toad(tmp_path / 'rx1.toad', 1, start=0.0)
    with caplog.at_level(logging.WARNING):
        detections, _ = identify.load_toad_files([str(tmp_path / '*.toad')])
    assert len(detections) == 6
    assert caplog.text == ''
