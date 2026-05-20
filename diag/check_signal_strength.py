#!/usr/bin/env python3
"""Signal-strength / gain / clipping diagnostic for a Thrifty v2 .card file.

This is the Phase E follow-up requested after real-data testing showed
the FLOAT32 fallback hypothesis was *not* the cause of the observed
weak-SNR symptom for the ``gs_r2_161_3_20260513_152100_TX2_Gain000``
capture (all gain stages at 0).

What it does
------------
For every block in the .card file (base64-decoded payload, interpreted
as INT16 IQ pairs, scaled by /32768.0):

  * compute the FFT-magnitude carrier peak inside the configured
    ``carrier_window`` (taken from a sibling ``detector.cfg`` when
    available, otherwise the conservative default 7..124),
  * compute the noise RMS from bins *outside* the carrier window
    (matches thriftyx.carrier_detect's reference),
  * derive a per-block carrier SNR in dB,
  * count ADC clipping samples (int16 == +/-32768),
  * count how many blocks have RMS amplitude in each decile bin
    (proxy for input-level headroom).

Then prints:

  * carrier-bin histogram (top 10),
  * SNR distribution (percentiles),
  * mean / median peak magnitude vs. noise RMS,
  * clipping totals,
  * a one-line recommendation on whether to *raise* or *lower* gain.

Usage
-----
    python diag/check_signal_strength.py [path/to/file.card]
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path
from typing import Iterator, Optional

import numpy as np


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
    return None


def _iter_blocks(path: Path) -> Iterator[bytes]:
    with path.open("r") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line or line.startswith("#") or line.startswith("\n"):
                continue
            if line.startswith("Using Volk machine:") or line.startswith("linux;"):
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            try:
                yield base64.b64decode(parts[2])
            except Exception:
                continue


def _read_v2_header(path: Path) -> dict[str, str]:
    with path.open("r") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#v2 "):
                out: dict[str, str] = {}
                for kv in s[4:].split():
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        out[k] = v
                return out
            if s.startswith("#"):
                continue
            break
    return {}


def _read_cfg_window(card_path: Path) -> tuple[int, int]:
    """Look for a sibling ``detector*.cfg`` and read ``carrier_window``.

    Falls back to (7, 124) — the value used in example/detector_r2.cfg.
    """
    for cfg in card_path.parent.glob("detector*.cfg"):
        for line in cfg.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("carrier_window"):
                rhs = stripped.split(":", 1)[1].strip()
                if "-" in rhs:
                    lo, hi = rhs.split("-", 1)
                    try:
                        return int(lo.strip()), int(hi.strip())
                    except ValueError:
                        pass
    return 7, 124


def _read_cfg_gains(card_path: Path) -> dict[str, object]:
    """Extract gain-related config from a sibling detector*.cfg."""
    keys = ("lna_gain", "mixer_gain", "vga_gain", "tuner_freq",
            "sample_rate", "bias_tee", "tuner_gain",
            "gain_mode", "combined_gain", "carrier_threshold")
    out: dict[str, object] = {}
    for cfg in card_path.parent.glob("detector*.cfg"):
        for line in cfg.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                continue
            k, _, v = stripped.partition(":")
            key = k.strip()
            if key in keys and key not in out:
                out[key] = v.strip()
    return out


def analyse(card_path: Path) -> int:
    meta = _read_v2_header(card_path)
    cfg = _read_cfg_gains(card_path)
    win_lo, win_hi = _read_cfg_window(card_path)
    sample_rate = int(meta.get("sample_rate", "10000000"))
    block_size = int(meta.get("block_size", "65536"))

    print(f"Analyzing signal strength: {card_path}")
    print(f"  metadata: {meta}")
    print(f"  detector.cfg (relevant keys): {cfg}")
    print(f"  carrier_window (used): {win_lo}-{win_hi}")
    print()

    carrier_bins: list[int] = []
    snr_db: list[float] = []
    peak_mag: list[float] = []
    noise_rms: list[float] = []
    clip_samples: list[int] = []
    rms_amplitude: list[float] = []
    block_count = 0

    for payload in _iter_blocks(card_path):
        block_count += 1
        arr = np.frombuffer(payload, dtype=np.int16)
        if arr.size < 4 or arr.size % 2 != 0:
            continue
        iq = (arr.astype(np.float32) / 32768.0)
        cx = iq[0::2] + 1j * iq[1::2]
        # FFT magnitude
        mag = np.abs(np.fft.fft(cx))
        # Restrict to configured carrier window.  carrier_window is given
        # in FFT-bin units in detector*.cfg (the same convention used by
        # thriftyx.setting_parsers.normalize_freq_range).
        lo = max(0, win_lo)
        hi = min(len(mag) // 2, win_hi)
        if hi <= lo:
            continue
        sub = mag[lo:hi + 1]
        peak_idx_local = int(np.argmax(sub))
        peak_idx = lo + peak_idx_local
        peak = float(sub[peak_idx_local])
        # Noise floor: bins outside the carrier window in the lower half.
        outside_mask = np.ones(len(mag) // 2, dtype=bool)
        outside_mask[lo:hi + 1] = False
        outside = mag[:len(mag) // 2][outside_mask]
        # Match thriftyx.carrier_detect: noise_rms over the *outside*
        # bins is the reference power.
        noise = float(np.sqrt(np.mean(outside ** 2))) if outside.size else 0.0
        if noise > 0:
            snr = 20.0 * np.log10(peak / noise)
        else:
            snr = float("nan")
        carrier_bins.append(peak_idx)
        snr_db.append(snr)
        peak_mag.append(peak)
        noise_rms.append(noise)
        clip_samples.append(int(np.sum((arr == 32767) | (arr == -32768))))
        rms_amplitude.append(float(np.sqrt(np.mean(iq ** 2))))

    if block_count == 0:
        print("ERROR: no decodable blocks found")
        return 1
    print(f"=== Blocks scanned: {block_count} ===\n")

    print("=== Carrier-bin histogram (top 10) ===")
    if carrier_bins:
        counts = np.bincount(carrier_bins)
        order = np.argsort(counts)[::-1]
        bin_res = sample_rate / block_size
        for i in order[:10]:
            if counts[i] == 0:
                break
            print(f"  bin {int(i):>5d}  "
                  f"f={int(i) * bin_res / 1000:>7.2f} kHz  "
                  f"{int(counts[i])} blocks")
    print()

    print("=== Carrier SNR distribution (dB) ===")
    snr_arr = np.asarray([s for s in snr_db if np.isfinite(s)])
    if snr_arr.size:
        for q in (5, 25, 50, 75, 95):
            print(f"  p{q:>2d}:  {np.percentile(snr_arr, q):>6.2f}")
        print(f"  mean: {snr_arr.mean():>6.2f}")
        print(f"  max:  {snr_arr.max():>6.2f}")
    print()

    print("=== Peak magnitude (linear) vs noise RMS (linear) ===")
    print(f"  peak  median = {float(np.median(peak_mag)):.4e}")
    print(f"  noise median = {float(np.median(noise_rms)):.4e}")
    print()

    print("=== ADC headroom ===")
    clip_total = int(sum(clip_samples))
    clip_blocks = int(sum(1 for c in clip_samples if c > 0))
    print(f"  clipped samples (==+/-32768): "
          f"{clip_total} total across {clip_blocks}/{block_count} blocks")
    rms_pct = np.percentile(rms_amplitude, [50, 95, 99])
    print(f"  block RMS amplitude p50/p95/p99: "
          f"{rms_pct[0]:.4f} / {rms_pct[1]:.4f} / {rms_pct[2]:.4f}")
    print(f"  (full-scale = 1.0; healthy operating point ≈ 0.1-0.4 "
          "with no clipping)")
    print()

    print("=" * 60)
    print("=== RECOMMENDATION ===")
    print("=" * 60)
    median_snr = float(np.median(snr_arr)) if snr_arr.size else float("nan")
    median_rms = float(np.median(rms_amplitude))
    headroom_db = 20.0 * np.log10(1.0 / max(median_rms, 1e-9))
    suggestion: list[str] = []
    if median_snr < 18.0:
        if clip_total == 0 and median_rms < 0.05:
            suggestion.append(
                f"  RAISE GAIN. median SNR {median_snr:.1f} dB is below the "
                f"~25 dB target, median input RMS is only {median_rms:.4f} "
                f"({headroom_db:.1f} dB below full-scale) and no clipping is "
                f"present. Try LNA/Mixer/VGA = 4 first, then 6/6/6.")
        elif clip_total > 0:
            suggestion.append(
                f"  LOWER GAIN slightly. {clip_total} clipped samples in "
                f"{clip_blocks} blocks indicate ADC saturation; the carrier "
                "is being suppressed by the AGC headroom. Step LNA/Mixer/VGA "
                "down by 2 each.")
        else:
            suggestion.append(
                f"  Median SNR {median_snr:.1f} dB and RMS {median_rms:.4f} "
                "are both modest. Likely *gain* is too low; raise LNA first "
                "(LNA primarily drives NF), then mixer, last VGA.")
    elif median_snr < 25.0:
        suggestion.append(
            f"  Borderline. SNR {median_snr:.1f} dB will still produce "
            "intermittent corr detections. Raise one stage at a time and "
            "re-check.")
    else:
        suggestion.append(
            f"  SNR {median_snr:.1f} dB looks healthy. If correlation is "
            "still failing, suspect template mismatch / timing, not gain.")

    # bias_tee sanity nag.
    bt = str(cfg.get("bias_tee", "")).lower()
    if bt in ("true", "yes", "1", "on"):
        suggestion.append(
            "  bias_tee=true in detector.cfg. If no active LNA on the "
            "antenna chain requires DC power, set bias_tee=false to avoid "
            "leaking ~+4.5 V into a passive antenna.")

    for s in suggestion:
        print(s)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("card", nargs="?", default=None,
                        help="Path to a .card file (default: auto-detect)")
    args = parser.parse_args(argv[1:])

    target = _find_card_file(args.card)
    if target is None:
        print("ERROR: no .card file found.")
        for d in CANDIDATE_DIRS:
            print(f"  searched: {d}")
        return 2
    return analyse(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
