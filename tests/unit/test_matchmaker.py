# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for matchmaker module."""

import io
import sys

from thriftyx import cli, toads_data
from thriftyx.matchmaker import load_matches, save_matches


def test_save_and_load_matches():
    """Test round-trip save/load of match data."""
    matches = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    buf = io.StringIO()
    save_matches(matches, buf)

    buf.seek(0)
    loaded = load_matches(buf)
    assert len(loaded) == 3
    for orig, loaded_match in zip(matches, loaded, strict=True):
        assert list(loaded_match) == orig


def test_load_matches_returns_lists():
    """Verify load_matches returns list of lists, not iterators."""
    buf = io.StringIO("1 2 3\n4 5 6\n")
    loaded = load_matches(buf)
    # Iterate twice — should work since they are lists, not iterators
    first = [list(m) for m in loaded]
    second = [list(m) for m in loaded]
    assert first == second


def _match_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['thriftyx', 'match', *argv])
    try:
        cli._main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_match_to_stdout_holds_only_matches(tmp_path, monkeypatch, capsys):
    """The counts used to be printed into the data on `-o -`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data.toads').write_text(''.join(
        '{} 1 10.{} 0 1.0 0 0.0 100.0 1.0 0 0.0 100.0 1.0\n'.format(rx, rx)
        for rx in (0, 1)))
    assert _match_cli(monkeypatch, '-o', '-') == 0
    out, err = capsys.readouterr()
    assert out == '0 1\n'
    assert 'Number of matches: 1' in err


def test_failed_match_keeps_the_previous_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data.toads').write_text('blk=2; carrier: yes @ 7.874 kHz '
                                         '/ 43:+0.00, SNR = 1588 / 65\n')
    (tmp_path / 'data.match').write_text('0 1\n')
    assert _match_cli(monkeypatch) == 1
    assert (tmp_path / 'data.match').read_text() == '0 1\n'


def test_match_on_a_toad_file_is_an_error(tmp_path, monkeypatch, capsys):
    """A .toad record is one field short of a .toads record; every line
    used to be skipped with a warning and match wrote no matches."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'rx0.toad').write_text(''.join(
        '0 10.{} {} 1.0 0 0.0 100.0 1.0 0 0.0 100.0 1.0\n'.format(i, i)
        for i in range(3)))
    (tmp_path / 'data.match').write_text('0 1\n')
    assert _match_cli(monkeypatch, 'rx0.toad') == 1
    err = capsys.readouterr().err
    assert 'rx0.toad: no .toads records (a .toad file?)' in err
    assert 'skipped line' not in err
    assert (tmp_path / 'data.match').read_text() == '0 1\n'


def test_short_lines_among_records_are_skipped(caplog):
    stream = io.StringIO('0 1 10.0 0 1.0 0 0.0 100.0 1.0 0 0.0 100.0 1.0\n'
                         '\n'
                         '0 1 10.1\n')
    assert len(toads_data.load_toads(stream)) == 1
    assert 'skipped line #3' in caplog.text
    assert 'skipped line #2' not in caplog.text
