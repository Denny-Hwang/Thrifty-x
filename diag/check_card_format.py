#!/usr/bin/env python3
"""Diagnose Thrifty v2 (or v1) ``.card`` file sample format.

Properly handles the Thrifty card-file format:

  Line 1 (v2 only):  ``#v2 bit_depth=12 sample_rate=10000000``
  Lines 2..N:        ``<timestamp> <block_idx> <base64-encoded payload>``

The script base64-decodes the payload of *every* data block, then tries
to interpret the raw bytes as ``int16``, ``float32``, and ``uint8``,
reporting which interpretation is plausible.  Two modes are supported:

  * **first-block** (default): print detailed stats for block 0 only.
  * **--all-blocks**: scan every block, summarise stats across all of
    them, and emit a header/version + per-block plausibility check.

Usage
-----
    python diag/check_card_format.py [path/to/file.card] [--all-blocks]

If no path is given, the script searches the workspace for the most
recent ``.card`` file.  Exits 2 if no file is found, 0 on a successful
analysis, 1 if the verdict is "unknown format".
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
    for root in (Path("/home/user/Thrifty-x"), Path.home()):
        if not root.is_dir():
            continue
        found = sorted(root.rglob("*.card"))
        if found:
            return found[0]
    return None


def _parse_header(line: str) -> dict[str, str]:
    """Parse a ``#v2 ...`` header line into a key=value dict.  Returns an
    empty dict for non-v2 (legacy v1) files."""
    if not line.startswith("#v2 "):
        return {}
    meta: dict[str, str] = {}
    for kv in line[4:].strip().split():
        if "=" in kv:
            k, v = kv.split("=", 1)
            meta[k] = v
    return meta


def iter_blocks(path: Path) -> Iterator[tuple[str, str, bytes]]:
    """Yield ``(timestamp, block_idx, raw_bytes)`` for every data block."""
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
            timestamp, block_idx, raw_b64 = parts
            try:
                payload = base64.b64decode(raw_b64)
            except (ValueError, Exception):
                continue
            yield timestamp, block_idx, payload


def read_header(path: Path) -> tuple[Optional[str], dict[str, str]]:
    """Return ``(version_string, metadata_dict)`` for the .card file.

    For legacy v1 (no header) ``version_string`` is ``"v1"`` and the
    metadata dict is empty.
    """
    with path.open("r") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#v2 "):
                return "v2", _parse_header(line)
            if line.startswith("#"):
                continue
            return "v1", {}
    return None, {}


def _stats_int16(buf: bytes) -> dict[str, float]:
    arr = np.frombuffer(buf, dtype=np.int16)
    return {
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "abs_p95": float(np.percentile(np.abs(arr), 95)),
        "abs_p99": float(np.percentile(np.abs(arr), 99)),
        "in_12bit": bool(arr.min() >= -2048 and arr.max() <= 2047),
        "in_full":  bool(arr.min() >= -32768 and arr.max() <= 32767),
        "clip_count": int(np.sum((arr == 32767) | (arr == -32768))),
        "len": int(arr.size),
    }


def _stats_float32(buf: bytes) -> dict[str, float]:
    if len(buf) % 4 != 0:
        return {"valid": False, "reason": "size not multiple of 4"}
    arr = np.frombuffer(buf, dtype=np.float32)
    with np.errstate(over="ignore", invalid="ignore"):
        finite_mask = np.isfinite(arr)
    finite_ratio = float(finite_mask.mean())
    if finite_ratio == 0.0:
        return {"valid": False, "reason": "no finite values",
                "finite_ratio": 0.0, "len": int(arr.size)}
    finite = arr[finite_mask]
    return {
        "valid": True,
        "len": int(arr.size),
        "finite_ratio": finite_ratio,
        "min": float(finite.min()) if finite.size else float("nan"),
        "max": float(finite.max()) if finite.size else float("nan"),
        "mean": float(finite.mean()) if finite.size else float("nan"),
        "std": float(finite.std()) if finite.size else float("nan"),
        "in_range_pm2": bool(finite_ratio > 0.95
                              and finite.size > 0
                              and finite.min() >= -2.0
                              and finite.max() <= 2.0),
    }


def _stats_uint8(buf: bytes) -> dict[str, float]:
    arr = np.frombuffer(buf, dtype=np.uint8)
    return {
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "centred_near_127": bool(abs(float(arr.mean()) - 127.4) < 30.0),
    }


def _verdict(i16: dict, f32: dict, u8: dict, bytes_per_complex: float) -> str:
    """Pick a single one-line verdict using the int16 / float32 / uint8 stats."""
    f32_ok = bool(f32.get("valid")) and bool(f32.get("in_range_pm2"))
    i16_full = bool(i16.get("in_full"))
    i16_12bit = bool(i16.get("in_12bit"))
    if f32_ok and not i16_12bit and bytes_per_complex >= 7.5:
        return ("FLOAT32_IQ: data is float32 IQ saved as raw bytes "
                "(bytes/complex ≈ 8). Most likely libairspy fallback.")
    if i16_full and not f32_ok and 3.5 <= bytes_per_complex <= 4.5:
        if i16_12bit:
            return ("INT16_IQ in 12-bit raw range (-2048..+2047). "
                    "raw_to_complex /32768.0 underutilises ~16x — "
                    "consider /2048.0 if SNR is unexpectedly low.")
        return ("INT16_IQ scaled to FULL int16 range. "
                "block_data.raw_to_complex(/32768.0) matches "
                "(bytes/complex == 4).")
    if u8.get("centred_near_127") and bytes_per_complex == 2.0:
        return "Legacy v1 RTL-SDR uint8 IQ (bytes/complex == 2)."
    if f32_ok and i16_full:
        return ("AMBIGUOUS: both int16 and float32 interpretations plausible. "
                "Disambiguate via bytes/complex and FFT shape.")
    return "UNKNOWN format. Manual inspection required."


def analyse_first_block(path: Path) -> int:
    print(f"Analyzing: {path}")
    print(f"File size: {path.stat().st_size} bytes")
    version, meta = read_header(path)
    print(f"Card version: {version!r}  metadata: {meta}")
    print()

    it = iter_blocks(path)
    try:
        timestamp, block_idx, payload = next(it)
    except StopIteration:
        print("ERROR: no data blocks in file")
        return 1

    print("=== First Data Block ===")
    print(f"  timestamp: {timestamp}")
    print(f"  block_idx: {block_idx}")
    print(f"  base64-decoded bytes: {len(payload)}")
    print()

    i16 = _stats_int16(payload)
    f32 = _stats_float32(payload)
    u8 = _stats_uint8(payload)

    print("=== Interpreted as int16 (Airspy v2 / 12-bit) ===")
    for k in ("len", "min", "max", "mean", "std", "abs_p95", "abs_p99",
              "in_12bit", "in_full", "clip_count"):
        print(f"  {k}: {i16[k]}")
    print()

    print("=== Interpreted as float32 (FLOAT32_IQ) ===")
    for k in ("valid", "len", "finite_ratio", "min", "max", "mean", "std",
              "in_range_pm2"):
        if k in f32:
            print(f"  {k}: {f32[k]}")
    print()

    print("=== Interpreted as uint8 (legacy v1) ===")
    for k in ("min", "max", "mean", "centred_near_127"):
        print(f"  {k}: {u8[k]}")
    print()

    block_size_hint = int(meta.get("block_size", 65536))
    bytes_per_complex = len(payload) / block_size_hint
    print("=" * 60)
    print("=== FORMAT VERDICT ===")
    print("=" * 60)
    print(f"  bytes/complex (assuming block_size={block_size_hint}): "
          f"{bytes_per_complex:.2f}")
    print(f"  → {_verdict(i16, f32, u8, bytes_per_complex)}")
    return 0


def analyse_all_blocks(path: Path) -> int:
    print(f"Analyzing (ALL blocks): {path}")
    print(f"File size: {path.stat().st_size} bytes")
    version, meta = read_header(path)
    print(f"Card version: {version!r}  metadata: {meta}")
    block_size_hint = int(meta.get("block_size", 65536))
    print()

    sizes: list[int] = []
    int16_min = []
    int16_max = []
    int16_mean = []
    int16_std = []
    int16_clip = []
    f32_finite_ratios = []
    f32_in_range = 0
    bins_seen: list[int] = []  # carrier bin for each block

    block_count = 0
    first_payload: Optional[bytes] = None
    for _, _, payload in iter_blocks(path):
        block_count += 1
        sizes.append(len(payload))
        if first_payload is None:
            first_payload = payload
        i16_arr = np.frombuffer(payload, dtype=np.int16)
        int16_min.append(int(i16_arr.min()))
        int16_max.append(int(i16_arr.max()))
        int16_mean.append(float(i16_arr.mean()))
        int16_std.append(float(i16_arr.std()))
        int16_clip.append(int(np.sum((i16_arr == 32767) |
                                      (i16_arr == -32768))))
        if len(payload) % 4 == 0:
            f32_arr = np.frombuffer(payload, dtype=np.float32)
            with np.errstate(over="ignore", invalid="ignore"):
                finite_mask = np.isfinite(f32_arr)
            f32_finite_ratios.append(float(finite_mask.mean()))
            if finite_mask.any():
                finite = f32_arr[finite_mask]
                if (finite_mask.mean() > 0.95
                        and finite.min() >= -2.0
                        and finite.max() <= 2.0):
                    f32_in_range += 1
        # Carrier-bin histogram via FFT magnitude peak.
        if i16_arr.size >= 4 and i16_arr.size % 2 == 0:
            iq = (i16_arr.astype(np.float32) / 32768.0)
            cx = iq[0::2] + 1j * iq[1::2]
            mag = np.abs(np.fft.fft(cx))
            # Restrict to lower-frequency half (positive bins) for the
            # histogram so we don't get dominated by mirror peaks.
            half = mag.size // 2
            bins_seen.append(int(np.argmax(mag[:half])))

    if block_count == 0:
        print("ERROR: no decodable blocks found")
        return 1

    unique_sizes = sorted(set(sizes))
    print("=== Block inventory ===")
    print(f"  decoded record count: {block_count}")
    print(f"  unique block byte sizes: {unique_sizes}")
    print(f"  total decoded bytes: {sum(sizes)}")
    if len(unique_sizes) == 1:
        b = unique_sizes[0]
        print(f"  bytes/complex (assuming block_size={block_size_hint}): "
              f"{b/block_size_hint:.2f}")
        print(f"  implied complex samples per block (b/4): {b/4:.0f}")
        print(f"  implied complex samples per block (b/8): {b/8:.0f}")
    print()

    print("=== int16 plausibility (per-block summary across all blocks) ===")
    print(f"  min  : range=[{min(int16_min)}, {max(int16_min)}]  "
          f"median={int(np.median(int16_min))}")
    print(f"  max  : range=[{min(int16_max)}, {max(int16_max)}]  "
          f"median={int(np.median(int16_max))}")
    print(f"  mean : range=[{min(int16_mean):.2f}, {max(int16_mean):.2f}]  "
          f"median={float(np.median(int16_mean)):.2f}")
    print(f"  std  : range=[{min(int16_std):.2f}, {max(int16_std):.2f}]  "
          f"median={float(np.median(int16_std)):.2f}")
    clip_total = int(sum(int16_clip))
    clip_blocks = int(sum(1 for c in int16_clip if c > 0))
    print(f"  ADC clipping (samples at +/-32768): "
          f"{clip_total} total across {clip_blocks}/{block_count} blocks")
    print()

    print("=== float32 plausibility (across all blocks) ===")
    if f32_finite_ratios:
        print(f"  finite_ratio: min={min(f32_finite_ratios):.4f}  "
              f"max={max(f32_finite_ratios):.4f}  "
              f"median={float(np.median(f32_finite_ratios)):.4f}")
        print(f"  blocks with float32 in [-2, +2] AND finite_ratio>0.95: "
              f"{f32_in_range}/{block_count}")
    else:
        print("  (no blocks divisible by 4 — float32 interpretation impossible)")
    print()

    print("=== Carrier-bin histogram (top-10 dominant bins across blocks) ===")
    if bins_seen:
        counts = np.bincount(bins_seen)
        order = np.argsort(counts)[::-1]
        for i in order[:10]:
            if counts[i] == 0:
                break
            print(f"  bin {int(i):>5d}: {int(counts[i])} blocks")
        unique_bins = len(set(bins_seen))
        print(f"  total unique bins observed: {unique_bins}")
    print()

    # Pick a verdict using the first block's stats (consistent with the
    # single-block path) but check that all blocks share the same size.
    i16 = _stats_int16(first_payload)
    f32 = _stats_float32(first_payload)
    u8 = _stats_uint8(first_payload)
    bytes_per_complex = (unique_sizes[0] / block_size_hint
                         if len(unique_sizes) == 1
                         else float("nan"))
    print("=" * 60)
    print("=== FORMAT VERDICT ===")
    print("=" * 60)
    print(f"  → {_verdict(i16, f32, u8, bytes_per_complex)}")
    if len(unique_sizes) != 1:
        print(f"  WARNING: block sizes are not uniform: {unique_sizes}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("card", nargs="?", default=None,
                        help="Path to a .card file (default: auto-detect)")
    parser.add_argument("--all-blocks", action="store_true",
                        help="Scan every block and summarise stats")
    args = parser.parse_args(argv[1:])

    target = _find_card_file(args.card)
    if target is None:
        print("ERROR: no .card file found.")
        for d in CANDIDATE_DIRS:
            print(f"  searched: {d}")
        if args.card:
            print(f"  explicit arg: {args.card}")
        return 2
    if args.all_blocks:
        return analyse_all_blocks(target)
    return analyse_first_block(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
