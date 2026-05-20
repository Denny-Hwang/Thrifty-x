#!/usr/bin/env python3
"""FFT analysis of a .card block to diagnose the dual-bin / low-SNR
symptom described in the diagnostic prompt.

Two FFTs are run on the first data block of the given .card file:
one assuming INT16_IQ (the project's canonical path), one assuming
FLOAT32_IQ-misinterpreted-as-INT16 (Phase A scenario 1-bis).  The plot
shows both spectra side by side so the operator can see at a glance
which interpretation produces a single clean carrier and which yields
the two-bin / 14 dB SNR pattern seen on the R2.

Usage:
    python diag/check_fft_dualbin.py [path/to/file.card]
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


CANDIDATE_DIRS = [
    Path.home() / "github" / "Thrifty-x" / "example" /
        "gs_r2_161_3_20260513_153826",
    Path("/home/user/Thrifty-x/example"),
    Path("/home/user/Thrifty-x/diag"),
    Path.home() / "Thrifty-x" / "example",
]


def _find_card_file(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for d in CANDIDATE_DIRS:
        if not d.is_dir():
            continue
        for pattern in ("*r2*.card", "*.card"):
            matches = sorted(d.glob(pattern))
            if matches:
                return matches[0]
    for root in (Path("/home/user/Thrifty-x"), Path.home()):
        if not root.is_dir():
            continue
        found = sorted(root.rglob("*.card"))
        if found:
            return found[0]
    return None


def _read_first_block(path: Path) -> tuple[str, bytes, dict]:
    metadata: dict[str, str] = {}
    with path.open("r") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if line.startswith("#v2 "):
                for kv in line[4:].split():
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        metadata[k] = v
                continue
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            return parts[1], base64.b64decode(parts[2]), metadata
    raise ValueError(f"No data lines in {path}")


def _peaks_summary(mag: np.ndarray, freqs_khz: np.ndarray,
                   top: int = 6) -> list[tuple[int, float, float]]:
    half = len(mag) // 2
    idx = np.argsort(mag[:half])[-top:][::-1]
    out = []
    for i in idx:
        out.append((int(i), float(freqs_khz[i]),
                    float(20 * np.log10(mag[i] + 1e-12))))
    return out


def analyse(path: Path, out_png: Path) -> int:
    block_idx, raw_bytes, metadata = _read_first_block(path)
    sample_rate = int(metadata.get("sample_rate", "10000000"))
    print(f"Block {block_idx}: {len(raw_bytes)} bytes")
    print(f"Metadata: {metadata}")

    # ===== Interpretation 1: INT16_IQ (canonical /32768.0) =====
    raw_i16 = np.frombuffer(raw_bytes, dtype=np.int16)
    if raw_i16.size % 2 != 0:
        raw_i16 = raw_i16[:-1]
    iq_i16 = (raw_i16.astype(np.float32) / 32768.0)
    iq_i16 = iq_i16[0::2] + 1j * iq_i16[1::2]
    iq_i16 = iq_i16.astype(np.complex64)

    # ===== Interpretation 2: FLOAT32_IQ misinterpreted =====
    has_f32 = (len(raw_bytes) % 4) == 0
    if has_f32:
        raw_f32 = np.frombuffer(raw_bytes, dtype=np.float32)
        if raw_f32.size % 2 != 0:
            raw_f32 = raw_f32[:-1]
        iq_f32 = raw_f32[0::2] + 1j * raw_f32[1::2]
        iq_f32 = iq_f32.astype(np.complex64)
    else:
        iq_f32 = None

    nrows = 2 if iq_f32 is not None else 1
    fig, axes = plt.subplots(nrows, 1, figsize=(14, 5 * nrows), squeeze=False)
    axes = axes[:, 0]

    fft_i16 = np.fft.fft(iq_i16)
    mag_i16 = np.abs(fft_i16)
    freqs_khz_i16 = np.fft.fftfreq(len(iq_i16), d=1.0 / sample_rate) / 1000

    half = len(mag_i16) // 2
    axes[0].plot(freqs_khz_i16[:half],
                 20 * np.log10(mag_i16[:half] + 1e-12),
                 linewidth=0.6)
    axes[0].set_title(
        f"FFT (INT16_IQ interpretation, /32768.0) — block {block_idx}"
        f"  N={len(iq_i16)}  fs={sample_rate/1e6:.3f} MSPS")
    axes[0].set_xlabel("Frequency (kHz)")
    axes[0].set_ylabel("Magnitude (dB)")
    axes[0].set_xlim(0, 200)
    axes[0].grid(True, alpha=0.3)

    print("INT16 interpretation peaks (top 6):")
    for idx, freq_khz, mag_db in _peaks_summary(mag_i16, freqs_khz_i16):
        print(f"  bin {idx:>5d}  f={freq_khz:>8.2f} kHz  mag={mag_db:>7.2f} dB")
        if freq_khz <= 200:
            axes[0].annotate(
                f"bin {idx}\n{freq_khz:.1f} kHz\n{mag_db:.1f} dB",
                xy=(freq_khz, mag_db), fontsize=8,
                arrowprops=dict(arrowstyle="->", color="red"),
                xytext=(freq_khz + 5, mag_db + 5))

    if iq_f32 is not None:
        fft_f32 = np.fft.fft(iq_f32)
        mag_f32 = np.abs(fft_f32)
        freqs_khz_f32 = np.fft.fftfreq(len(iq_f32), d=1.0 / sample_rate) / 1000

        half_f32 = len(mag_f32) // 2
        axes[1].plot(freqs_khz_f32[:half_f32],
                     20 * np.log10(mag_f32[:half_f32] + 1e-12),
                     linewidth=0.6)
        axes[1].set_title(
            f"FFT (FLOAT32_IQ interpretation) — block {block_idx}  "
            f"N={len(iq_f32)}")
        axes[1].set_xlabel("Frequency (kHz)")
        axes[1].set_ylabel("Magnitude (dB)")
        axes[1].set_xlim(0, 200)
        axes[1].grid(True, alpha=0.3)

        print("FLOAT32 interpretation peaks (top 6):")
        for idx, freq_khz, mag_db in _peaks_summary(mag_f32, freqs_khz_f32):
            print(f"  bin {idx:>5d}  f={freq_khz:>8.2f} kHz  "
                  f"mag={mag_db:>7.2f} dB")
            if freq_khz <= 200:
                axes[1].annotate(
                    f"bin {idx}\n{freq_khz:.1f} kHz\n{mag_db:.1f} dB",
                    xy=(freq_khz, mag_db), fontsize=8,
                    arrowprops=dict(arrowstyle="->", color="red"),
                    xytext=(freq_khz + 5, mag_db + 5))

    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=140)
    print(f"\nSaved: {out_png}")
    return 0


def main(argv: list[str]) -> int:
    explicit = argv[1] if len(argv) > 1 else None
    target = _find_card_file(explicit)
    if target is None:
        print("ERROR: no .card file found for FFT analysis.")
        return 2
    suffix = target.stem
    out_png = (Path(__file__).resolve().parent /
               f"phase_c_fft_comparison_{suffix}.png")
    return analyse(target, out_png)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
