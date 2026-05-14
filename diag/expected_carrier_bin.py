#!/usr/bin/env python3
"""Calculate the expected carrier bin for the R2 test setup described in
the diagnostic prompt (TX = 161.3 MHz).

Prints two scenarios:
  - center_freq = 161.0 MHz   → expected carrier offset = +300 kHz
  - center_freq = 161.3 MHz   → expected carrier offset = 0 Hz (DC bin)

Also greps any .cfg files found in the diag candidate paths for the
actual configured tuner_freq, so the operator can sanity-check the
match between the observed bin (~20) and the expected bin from config.
"""
from __future__ import annotations

import os
from pathlib import Path


CANDIDATE_DIRS = [
    Path.home() / "github" / "Thrifty-x" / "example" /
        "gs_r2_161_3_20260513_153826",
    Path("/home/user/Thrifty-x/example"),
    Path("/home/user/Thrifty-x/diag"),
    Path.home() / "Thrifty-x" / "example",
]


def main() -> int:
    sample_rate = 10_000_000
    block_size = 65_536
    tx_freq = 161_300_000

    bin_res = sample_rate / block_size

    print(f"R2 sample rate:    {sample_rate / 1e6:.3f} MSPS")
    print(f"Block size:        {block_size}")
    print(f"Bin resolution:    {bin_res:.4f} Hz/bin")
    print(f"TX frequency:      {tx_freq / 1e6:.3f} MHz")
    print()

    for label, center in [
        ("center=161.0 MHz", 161_000_000),
        ("center=161.3 MHz", 161_300_000),
        ("center=166.0 MHz (detector_r2.cfg)", 166_000_000),
    ]:
        offset = tx_freq - center
        expected = offset / bin_res
        print(f"  {label}: offset = {offset:>+12d} Hz  "
              f"→ expected_bin = {expected:>+10.2f}")
    print()

    # 관측치 해석
    observed_bins = (20, 72)
    print("Observed bins (from prompt):")
    for b in observed_bins:
        print(f"  bin {b:>3d}  ≈  {b * bin_res / 1000:>6.2f} kHz")
    print()
    print(f"  Δbin = {observed_bins[1] - observed_bins[0]} → "
          f"Δf = {(observed_bins[1] - observed_bins[0]) * bin_res / 1000:.2f} "
          "kHz between the two peaks")
    print()

    # cfg 파일 sniff
    print("Searching .cfg files for tuner_freq / sample_rate ...")
    found = False
    for d in CANDIDATE_DIRS:
        if not d.is_dir():
            continue
        for cf in sorted(d.glob("*.cfg")):
            print(f"  === {cf} ===")
            for raw_line in cf.read_text().splitlines():
                line = raw_line.strip()
                low = line.lower()
                if any(k in low for k in ("freq", "rate", "center",
                                          "tuner", "carrier_window")):
                    print(f"    {line}")
            found = True
            print()
    if not found:
        print("  (no .cfg files found in candidate directories)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
