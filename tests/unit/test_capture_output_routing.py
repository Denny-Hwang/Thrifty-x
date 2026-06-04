# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for capture card-output routing and the interactive TTY guard.

``thriftyx capture`` (Airspy) must mirror the RTL/fastcard console UX:
human-readable carrier-detection diagnostics go to stderr, while base64
.card data is emitted only when a destination is actually requested -- to a
file, or to a piped stdout -- but is NEVER dumped onto an interactive
terminal.  This mirrors the RTL reference fastcard, which leaves
``out == NULL`` (no card output at all) when no ``-o`` flag is given
(fastcapture/fastcard_cli.c).
"""

import io
import re
import sys

import numpy as np

import thriftyx.airspy_capture as ac
from thriftyx.airspy_capture import _resolve_card_output, _capture_airspy
from thriftyx.settings import Namespace


# Matches the fastcard per-block detection line format
# (fastcapture/fastcard_cli.c: "block #%...: mag[%u] = %.1f
#  (thresh = %.1f, noise = %.1f)").
BLOCK_LINE_RE = re.compile(
    r"^block #(-?\d+): mag\[\d+\] = \d+\.\d "
    r"\(thresh = \d+\.\d, noise = \d+\.\d\)$")


# ---------------------------------------------------------------------------
# _resolve_card_output: the RTL/fastcard destination pattern
# ---------------------------------------------------------------------------

def test_resolve_none_on_tty_suppresses_output(monkeypatch):
    """No output arg + interactive TTY -> None (display only)."""
    monkeypatch.setattr(ac, "_stdout_is_tty", lambda: True)
    assert _resolve_card_output(None) is None


def test_resolve_none_on_pipe_returns_stdout(monkeypatch):
    """No output arg + piped stdout -> stdout (so piping still works)."""
    monkeypatch.setattr(ac, "_stdout_is_tty", lambda: False)
    assert _resolve_card_output(None) is sys.stdout


def test_resolve_dash_returns_stdout_even_on_tty(monkeypatch):
    """Explicit ``-`` always means stdout, even on a TTY."""
    monkeypatch.setattr(ac, "_stdout_is_tty", lambda: True)
    assert _resolve_card_output('-') is sys.stdout


def test_resolve_path_opens_file(tmp_path):
    """An explicit path is opened for writing regardless of TTY state."""
    p = tmp_path / "out.card"
    handle = _resolve_card_output(str(p))
    try:
        assert handle is not None
        assert handle is not sys.stdout
    finally:
        handle.close()
    assert p.exists()


def test_stdout_is_tty_defensive_on_bad_stream(monkeypatch):
    """_stdout_is_tty tolerates a stream without/with broken isatty()."""
    class _NoIsatty:
        pass

    monkeypatch.setattr(sys, "stdout", _NoIsatty())
    assert ac._stdout_is_tty() is False


# ---------------------------------------------------------------------------
# _capture_airspy: channel separation + carrier gating
# ---------------------------------------------------------------------------

class _FakeAirspyDevice:
    """Minimal fake Airspy device that yields a fixed number of blocks."""

    def __init__(self, n_blocks):
        # block_size 8, history 2 -> new_samples 6 -> 12 int16 values/read.
        self._buffers = [np.arange(12, dtype=np.int16) + 100 * i
                         for i in range(n_blocks)]
        self.dropped_samples = 0

    def open(self):
        return None

    def close(self):
        return None

    def set_sample_rate(self, _rate):
        return None

    def set_center_freq(self, _freq):
        return None

    def set_bias_tee(self, _enabled):
        return None

    def set_packing(self, _enabled):
        return None

    def apply_gain_mode(self, _mode, **_kwargs):
        return None

    def read_sync(self, _num_samples):
        if not self._buffers:
            return np.array([], dtype=np.int16)
        return self._buffers.pop(0)


def _config():
    return Namespace({
        'device_type': 'airspy_r2',
        'sample_rate': 3_000_000,
        'tuner_freq': 433_920_000,
        'block_size': 8,
        'block_history': 2,
        'capture_skip': 0,
        'carrier_window': (0, -1, False),
        'carrier_threshold': (0.0, 0.0, 0.0),
        'lna_gain': 0,
        'mixer_gain': 0,
        'vga_gain': 0,
        'bias_tee': False,
        'gain_mode': 'manual',
        'combined_gain': 0,
        'lna_agc': False,
        'mixer_agc': False,
        'ppm': 0.0,
        'packing': False,
    })


def _run_capture(monkeypatch, output_file, detect_pattern):
    """Run ``_capture_airspy`` with a fake device and detection pattern.

    ``detect_pattern`` is an iterable of booleans, one per processed block;
    a truthy value marks that block as a carrier detection.  Returns the
    captured stderr text.
    """
    fake = _FakeAirspyDevice(len(detect_pattern))
    monkeypatch.setattr(
        'thriftyx.hal.device_factory.create_device',
        lambda _device_type, **_kwargs: fake)

    pattern = iter(detect_pattern)
    monkeypatch.setattr(
        ac, 'carrier_detect_block',
        lambda *_a, **_k: (bool(next(pattern)), 1, 10.0, 1.0))

    err = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', err)
    _capture_airspy(_config(), Namespace({'duration': None}), output_file)
    return err.getvalue()


def _block_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith('block #')]


def _card_lines(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith('#')]


def test_display_only_emits_diagnostics_no_card_data(monkeypatch):
    """output_file=None: stderr gets banner + gated block lines, and no
    base64 / header leaks onto any channel."""
    err = _run_capture(monkeypatch, None, [True, False, True])

    assert 'block size:' in err          # startup banner present
    blines = _block_lines(err)
    assert len(blines) == 2              # only the 2 detected blocks
    assert all(BLOCK_LINE_RE.match(ln) for ln in blines)
    assert '#v2' not in err              # no card header leaked to stderr


def test_file_output_writes_header_and_gated_cards(monkeypatch):
    """A real destination receives the v2 header and exactly the detected
    blocks, while diagnostics still go to stderr."""
    out = io.StringIO()
    err = _run_capture(monkeypatch, out, [True, False, True])

    text = out.getvalue()
    assert text.startswith('#v2 ')
    assert len(_card_lines(text)) == 2   # gating: 2 of 3 blocks written
    assert len(_block_lines(err)) == 2   # matching diagnostics on stderr


def test_no_detection_writes_header_but_no_cards(monkeypatch):
    """With a destination but no detections, the header is written but no
    card lines (and no spurious block diagnostics)."""
    out = io.StringIO()
    err = _run_capture(monkeypatch, out, [False, False])

    text = out.getvalue()
    assert text.startswith('#v2 ')
    assert _card_lines(text) == []
    assert _block_lines(err) == []


def test_detection_line_format_matches_fastcard(monkeypatch):
    """Per-block line format byte-matches the fastcard reference."""
    err = _run_capture(monkeypatch, None, [True])
    blines = _block_lines(err)
    assert len(blines) == 1
    assert BLOCK_LINE_RE.match(blines[0])


# ---------------------------------------------------------------------------
# capture_cli: end-to-end routing + "no file created" guarantee
# ---------------------------------------------------------------------------

def _drive_cli(monkeypatch, tmp_path, argv, tty):
    """Drive the real ``capture_cli`` with a controlled config + fake device.

    Returns ``(stdout_text, stderr_text)``.
    """
    monkeypatch.chdir(tmp_path)

    fake = _FakeAirspyDevice(3)
    monkeypatch.setattr(
        'thriftyx.hal.device_factory.create_device',
        lambda _device_type, **_kwargs: fake)

    # Detect on blocks 0 and 2, but not block 1 -> exercises gating.
    state = {'n': 0}

    def _detect(*_a, **_k):
        i = state['n']
        state['n'] += 1
        return (i != 1, 1, 10.0, 1.0)

    monkeypatch.setattr(ac, 'carrier_detect_block', _detect)
    monkeypatch.setattr(ac, '_stdout_is_tty', lambda: tty)

    extra = {'output': argv[0] if argv else None, 'duration': None,
             'input': None, 'fastcard': 'fastcard', 'device_index': 0}
    monkeypatch.setattr(
        ac.settings_module, 'load_args',
        lambda *_a, **_k: (_config(), Namespace(dict(extra))))
    monkeypatch.setattr(ac.config_validator, 'validate_config', lambda _c: [])

    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    monkeypatch.setattr(sys, 'stderr', err)
    ac.capture_cli(list(argv))
    return out.getvalue(), err.getvalue()


def test_cli_tty_no_output_creates_no_file(monkeypatch, tmp_path):
    """`thriftyx capture` on a TTY: diagnostics only, no base64, no file."""
    out, err = _drive_cli(monkeypatch, tmp_path, [], tty=True)

    assert _card_lines(out) == []
    assert '#v2' not in out
    assert list(tmp_path.iterdir()) == []   # nothing written to disk
    assert 'block size:' in err
    assert len(_block_lines(err)) == 2


def test_cli_pipe_no_output_streams_base64(monkeypatch, tmp_path):
    """`thriftyx capture | ...`: base64 flows through stdout, TTY guard
    does not block the pipe."""
    out, err = _drive_cli(monkeypatch, tmp_path, [], tty=False)

    assert out.startswith('#v2 ')
    assert len(_card_lines(out)) == 2
    assert list(tmp_path.iterdir()) == []   # still no default file on disk
    assert len(_block_lines(err)) == 2


def test_cli_file_output_writes_card_file(monkeypatch, tmp_path):
    """`thriftyx capture out.card`: base64 to the file, diagnostics to
    stderr, nothing on stdout."""
    out, err = _drive_cli(monkeypatch, tmp_path, ['out.card'], tty=True)

    card = tmp_path / 'out.card'
    assert card.exists()
    text = card.read_text()
    assert text.startswith('#v2 ')
    assert len(_card_lines(text)) == 2
    assert _card_lines(out) == []
    assert len(_block_lines(err)) == 2
