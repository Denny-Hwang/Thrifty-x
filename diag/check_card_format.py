#!/usr/bin/env python3
"""Diagnose .card file sample format.

Reads the first data block of an Airspy v2 .card file (or a v1 RTL-SDR
.card file as a control), tries to interpret the raw bytes as int16,
float32, and uint8, and prints which interpretation fits the expected
value ranges.

Usage
-----
    python diag/check_card_format.py [path/to/file.card]

If no path is given, the script will search the workspace for the most
recent ``.card`` file, then fall back to looking in
``~/github/Thrifty-x/example/gs_r2_161_3_20260513_153826`` (the
directory referenced in the diagnostic prompt).  If neither is found
the script reports the missing-data condition and exits with code 2 so
that it can be safely scripted.
"""
from __future__ import annotations

import base64
import glob
import os
import sys
from pathlib import Path
from typing import Optional

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
        # Prefer R2 captures, fall back to any .card
        for pattern in ("*r2*.card", "*.card"):
            matches = sorted(d.glob(pattern))
            if matches:
                return matches[0]
    # Last resort: walk the workspace
    for root in (Path("/home/user/Thrifty-x"), Path.home()):
        if not root.is_dir():
            continue
        found = sorted(root.rglob("*.card"))
        if found:
            return found[0]
    return None


def _first_data_line(path: Path) -> tuple[str, str, bytes]:
    """Return ``(timestamp, block_idx, raw_bytes)`` for the first data block."""
    with path.open("r") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                raise ValueError(
                    f"Malformed .card line in {path}: {line[:80]!r}")
            timestamp, block_idx, raw_b64 = parts
            return timestamp, block_idx, base64.b64decode(raw_b64)
    raise ValueError(f"No data lines in {path}")


def _header_lines(path: Path) -> list[str]:
    out: list[str] = []
    with path.open("r") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line or line.startswith("#"):
                out.append(line)
                continue
            break
            # Falls through after first non-header line.
    return out


def analyse(path: Path) -> int:
    print(f"Analyzing: {path}")
    print(f"File size: {path.stat().st_size} bytes")
    print()

    headers = _header_lines(path)
    print("=== File Header ===")
    for i, line in enumerate(headers[:5]):
        print(f"  line {i}: {line[:120]}")
    if not headers:
        print("  (no header lines — assume v1 RTL-SDR uint8 format)")
    print()

    timestamp, block_idx, raw_bytes = _first_data_line(path)

    print("=== First Data Block ===")
    print(f"  timestamp: {timestamp}")
    print(f"  block_idx: {block_idx}")
    print(f"  raw bytes: {len(raw_bytes)}")
    print()

    # --- Interpretation 1: int16 ---
    as_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
    print("=== Interpreted as int16 (expected for 12-bit Airspy v2) ===")
    print(f"  count: {len(as_int16)} values "
          f"({len(as_int16)//2} complex samples)")
    print(f"  min:   {as_int16.min()}")
    print(f"  max:   {as_int16.max()}")
    print(f"  mean:  {as_int16.mean():.4f}")
    print(f"  std:   {as_int16.std():.4f}")
    print(f"  first 20 values: {as_int16[:20]}")
    i16_12bit = -2048 <= int(as_int16.min()) and int(as_int16.max()) <= 2047
    i16_full = (-32768 <= int(as_int16.min())
                and int(as_int16.max()) <= 32767)
    print(f"  12-bit raw range  (-2048 .. +2047):  "
          f"{'PASS' if i16_12bit else 'FAIL'}")
    print(f"  full int16 range  (-32768 .. +32767): "
          f"{'PASS' if i16_full else 'FAIL'}")
    print()

    # --- Interpretation 2: float32 ---
    f32_ok = False
    has_f32 = (len(raw_bytes) % 4) == 0
    if has_f32:
        as_float32 = np.frombuffer(raw_bytes, dtype=np.float32)
        has_nan = bool(np.any(np.isnan(as_float32)))
        has_inf = bool(np.any(np.isinf(as_float32)))
        in_range = (
            (not has_nan) and (not has_inf)
            and float(as_float32.min()) >= -2.0
            and float(as_float32.max()) <= 2.0
        )
        f32_ok = in_range
        print("=== Interpreted as float32 (FLOAT32_IQ format) ===")
        print(f"  count: {len(as_float32)} values "
              f"({len(as_float32)//2} complex samples)")
        print(f"  min:   {as_float32.min():.6f}")
        print(f"  max:   {as_float32.max():.6f}")
        print(f"  mean:  {as_float32.mean():.6f}")
        print(f"  std:   {as_float32.std():.6f}")
        print(f"  first 10 values: {as_float32[:10]}")
        print(f"  NaN present:  {has_nan}")
        print(f"  Inf present:  {has_inf}")
        print(f"  range check ([-2, +2]): "
              f"{'PASS' if in_range else 'FAIL'}")
    else:
        print("=== float32 해석 불가: byte count가 4의 배수가 아님 ===")
    print()

    # --- Interpretation 3: uint8 (RTL-SDR legacy) ---
    as_uint8 = np.frombuffer(raw_bytes, dtype=np.uint8)
    u8_centred = abs(float(as_uint8.mean()) - 127.4) < 30.0
    print("=== Interpreted as uint8 (legacy RTL-SDR v1) ===")
    print(f"  count: {len(as_uint8)} values "
          f"({len(as_uint8)//2} complex samples)")
    print(f"  min:   {as_uint8.min()}")
    print(f"  max:   {as_uint8.max()}")
    print(f"  mean:  {as_uint8.mean():.4f}")
    print(f"  centred near 127.4: {'PASS' if u8_centred else 'FAIL'}")
    print()

    # --- Verdict ---
    print("=" * 60)
    print("=== FORMAT VERDICT ===")
    print("=" * 60)
    block_size_hint = 65536
    bytes_per_complex = len(raw_bytes) / block_size_hint
    print(f"  (assuming block_size={block_size_hint}: "
          f"{bytes_per_complex:.2f} bytes/complex sample)")
    print("    2 → int16 IQ  |  4 → float32 IQ  |  1 → uint8 IQ")
    print()
    if f32_ok and not i16_full:
        print("  → Data is FLOAT32_IQ (values in [-2, +2], int16 "
              "overflows / out of plausible range)")
        return 0
    if i16_full and not f32_ok:
        if i16_12bit:
            print("  → Data is INT16_IQ packed in raw 12-bit range "
                  "(-2048 .. +2047). This means raw_to_complex's "
                  "/32768.0 scaling will underutilise the signal "
                  "(needs /2048.0 instead).")
        else:
            print("  → Data is INT16_IQ scaled to FULL int16 range. "
                  "block_data.raw_to_complex(/32768.0) matches.")
        return 0
    if f32_ok and i16_full:
        print("  → AMBIGUOUS: both int16 and float32 interpretations "
              "are within plausible ranges. Inspect bytes-per-sample "
              "ratio and FFT shape (Phase C) to disambiguate.")
        return 0
    if not f32_ok and not i16_full and u8_centred:
        print("  → Data is uint8 IQ (legacy RTL-SDR v1 .card).")
        return 0
    print("  → UNKNOWN format. Manual inspection required.")
    return 1


def main(argv: list[str]) -> int:
    explicit = argv[1] if len(argv) > 1 else None
    target = _find_card_file(explicit)
    if target is None:
        print("ERROR: no .card file found.")
        print("  Searched paths:")
        for d in CANDIDATE_DIRS:
            print(f"    - {d}")
        if explicit:
            print(f"    - explicit arg: {explicit}")
        print()
        print("  This means the R2 capture from the diagnostic prompt "
              "(gs_r2_161_3_20260513_153826) is not present on this "
              "system. Re-run after copying a capture into one of the "
              "candidate paths, or pass an explicit path:")
        print("    python diag/check_card_format.py /path/to/file.card")
        return 2
    return analyse(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
