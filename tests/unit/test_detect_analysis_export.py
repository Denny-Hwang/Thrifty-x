# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""`analyze_detect --export` renders every default plot family to PNG.

Runs the real CLI on a synthetic 6 MSPS card holding one Gold-code
burst, headless (Agg, no Qt), and checks the documented output layout
`<prefix>_block<N>/<plot>.png`, and what the FFT-window panel shows.
"""

import io
import sys
import types

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from thriftyx import block_data, detect_analysis, gold  # noqa: E402
from thriftyx.template_generate import resample  # noqa: E402

FS = 6_000_000
CHIP_RATE = 0.999707e6
BLOCK, HISTORY = 32768, 12278
FAMILIES = ['overview', 'time', 'overlays', 'spectra', 'corrs']
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _card_with_one_burst():
    rng = np.random.default_rng(7)
    code = np.array(gold.gold(10, 0, 'gold'), dtype=float)
    sps = FS / CHIP_RATE
    keyed = code[(np.arange(int(len(code) * sps)) / sps).astype(int)]
    block = (rng.normal(0, 0.01, BLOCK)
             + 1j * rng.normal(0, 0.01, BLOCK)).astype(np.complex64)
    start = HISTORY + 1500
    n = np.arange(len(keyed))
    block[start:start + len(keyed)] += (
        0.1 * keyed * np.exp(2j * np.pi * 50e3 * n / FS))
    buf = io.StringIO()
    block_data.write_card_header(buf, bit_depth=12, sample_rate=FS,
                                 block_size=BLOCK, block_history=HISTORY)
    block_data.card_writer(buf, 1_700_000_000.0, 5, block, bit_depth=12)
    return buf.getvalue()


def test_export_writes_one_png_per_plot_family(tmp_path, monkeypatch):
    card = tmp_path / 'rx0.card'
    card.write_text(_card_with_one_burst())
    template = tmp_path / 'template.npy'
    np.save(template, resample(gold.gold(10, 0, 'gold'), FS / CHIP_RATE))
    monkeypatch.chdir(tmp_path)  # no detector.cfg; header supplies geometry
    monkeypatch.setattr(sys, 'argv', [
        'analyze_detect', str(card), '-z', str(template),
        '--export', str(tmp_path / 'plots')])

    detect_analysis._main()

    block_dir = tmp_path / 'plots_block5'
    written = sorted(p.name for p in block_dir.iterdir())
    assert written == sorted(f + '.png' for f in FAMILIES)
    for name in written:
        data = (block_dir / name).read_bytes()
        assert data.startswith(PNG_MAGIC) and len(data) > 10_000, name


@pytest.mark.parametrize('window, xlim, markers', [
    # The default whole-band window used to zoom to bins -10..9 around
    # DC, hiding the carrier the overview is meant to show.
    ((0, -1), (-8192, 8191), []),
    ((300, 400), (290, 410), [300, 400]),
    ((-10, 10), (-20, 20), [-10, 10]),
    ((-400, -300), (-410, -290), [-400, -300]),
    ((8000, 9000), (-8192, 8191), []),     # wraps past Nyquist
])
def test_fft_window_panel_keeps_the_window_in_view(window, xlim, markers):
    from matplotlib.figure import Figure

    n = 16384
    ax = Figure().add_subplot()
    ax.plot(np.arange(-n // 2, n // 2), np.ones(n))
    ax.set_xlim(-n // 2, n // 2 - 1)
    plotter = types.SimpleNamespace(settings=types.SimpleNamespace(
        carrier_window=window, block_len=n))
    detect_analysis.Plotter._plot_fft_window(plotter, ax, zoom_to_window=True)
    assert tuple(ax.get_xlim()) == xlim
    assert [line.get_xdata()[0] for line in ax.get_lines()[1:]] == markers
