#!/usr/bin/env python3
"""Summarize a Thrifty-X detect.log without changing detector behavior."""

import argparse
import csv
import math
import os
import re

import numpy as np


def stats(x):
    if not x:
        return (float("nan"), float("nan"))
    return (float(np.mean(x)), float(np.std(x, ddof=0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--label", required=True)
    ap.add_argument("--sample-rate", type=float, required=True)
    ap.add_argument("--block-size", type=int, required=True)
    ap.add_argument("--history", type=int, required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--expected-offset-hz", type=float, required=True)
    ap.add_argument("--card-blocks", type=int, required=True)
    ap.add_argument("--dropped-pairs", type=int, default=0)
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    corr_blocks = []
    carrier_offsets_khz = []
    carrier_snr = []
    corr_snr = []
    redetect_carrier_yes = 0

    blk_re = re.compile(r"blk=(\d+)")
    off_re = re.compile(r"carrier:\s*yes\s*@\s*([+-]?[0-9.]+)\s*kHz")
    snr_re = re.compile(r"=\s*([+-]?[0-9.]+)\s*dB")

    with open(args.log, "r", errors="ignore") as f:
        for line in f:
            if "carrier: yes" not in line:
                continue
            redetect_carrier_yes += 1
            b = blk_re.search(line)
            off = off_re.search(line)
            if off:
                carrier_offsets_khz.append(float(off.group(1)))
            if "; corr:" in line:
                cp, rp = line.split("; corr:", 1)
            else:
                cp, rp = line, ""
            cm = snr_re.findall(cp)
            if cm:
                carrier_snr.append(float(cm[-1]))
            if rp.lstrip().startswith("yes"):
                if b:
                    corr_blocks.append(int(b.group(1)))
                rm = snr_re.findall(rp)
                if rm:
                    corr_snr.append(float(rm[-1]))

    groups = []
    for b in sorted(set(corr_blocks)):
        if not groups or b - groups[-1][-1] > 1:
            groups.append([b])
        else:
            groups[-1].append(b)
    starts = [g[0] for g in groups]
    stride = args.block_size - args.history
    intervals = [(b-a) * stride / args.sample_rate
                 for a, b in zip(starts[:-1], starts[1:], strict=True)]

    carr_mean, carr_std = stats(carrier_snr)
    corr_mean, corr_std = stats(corr_snr)
    off_mean, off_std = stats(carrier_offsets_khz)
    median_interval = float(np.median(intervals)) if intervals else float("nan")
    max_interval = max(intervals) if intervals else float("nan")
    expected_khz = args.expected_offset_hz / 1000.0

    out = {
        "label": args.label,
        "sample_rate_hz": args.sample_rate,
        "block_size": args.block_size,
        "block_history": args.history,
        "stride_samples": stride,
        "card_carrier_blocks": args.card_blocks,
        "redetect_carrier_yes": redetect_carrier_yes,
        "corr_yes_blocks": len(corr_blocks),
        "corr_per_card_block": (len(corr_blocks) / args.card_blocks
                               if args.card_blocks else float("nan")),
        "unique_ping_groups": len(groups),
        "ping_groups_per_s": (len(groups) / args.duration
                              if args.duration else float("nan")),
        "median_ping_interval_s": median_interval,
        "max_ping_interval_s": max_interval,
        "carrier_snr_db_mean": carr_mean,
        "carrier_snr_db_std": carr_std,
        "corr_snr_db_mean": corr_mean,
        "corr_snr_db_std": corr_std,
        "carrier_offset_khz_mean": off_mean,
        "carrier_offset_khz_std": off_std,
        "freq_error_hz_mean": ((off_mean - expected_khz) * 1000.0
                               if math.isfinite(off_mean) else float("nan")),
        "dropped_sample_pairs": args.dropped_pairs,
    }

    for k, v in out.items():
        print(f"{k}: {v}")

    exists = os.path.exists(args.csv) and os.path.getsize(args.csv) > 0
    with open(args.csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out.keys()))
        if not exists:
            w.writeheader()
        w.writerow(out)


if __name__ == "__main__":
    main()
