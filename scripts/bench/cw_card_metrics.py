#!/usr/bin/env python3
"""Summarize a forced-all-block Thrifty-X .card capture of a CW tone.

Metric definitions:
- carrier power: FFT power integrated over +/-2 bins around the strongest
  bin inside the configured carrier search window; 0 dBFS corresponds to a
  normalized complex sinusoid with amplitude 1.
- local noise PSD: median periodogram-bin power (median/ln(2) correction)
  between 5 and 100 kHz from the detected carrier, excluding DC +/-5 kHz,
  divided by FFT-bin width.
- C/N0: carrier_dBFS - noise_psd_dBFS_per_Hz.

This is for controlled CW comparison only. It is not the Thrifty OOK carrier
noise estimator and is not used for Gold-code detection.
"""

import argparse
import csv
import math
import os
import statistics as st

import numpy as np

from thriftyx.block_data import card_reader, peek_card_header


def db10(x):
    return 10.0 * math.log10(max(float(x), 1e-300))


def mean_std(values):
    vals = [float(v) for v in values if np.isfinite(v)]
    if not vals:
        return float("nan"), float("nan")
    return float(np.mean(vals)), float(np.std(vals, ddof=0))


def analyze(path, center_hz, expected_hz, search_lo_hz, search_hi_hz,
            tone_half_bins=2, noise_near_hz=5_000.0,
            noise_far_hz=100_000.0):
    rows = []
    with open(path, "rb") as f:
        header, stream = peek_card_header(f)
        fs = float(header.get("sample_rate") or 0)
        if not fs:
            raise SystemExit(f"{path}: #v2 sample_rate is required")
        bit_depth = int(header.get("bit_depth", 8))
        block_size_header = int(header.get("block_size", 0) or 0)

        for _ts, block_idx, block in card_reader(stream, bit_depth=bit_depth):
            z = np.asarray(block, dtype=np.complex64)
            n = len(z)
            if n == 0:
                continue
            if block_size_header and n != block_size_header:
                raise SystemExit(
                    f"{path}: block {block_idx} has {n} samples, header says "
                    f"{block_size_header}")

            x = np.fft.fft(z)
            pbin = (np.abs(x) ** 2) / (n * n)
            freq = np.fft.fftfreq(n, d=1.0 / fs)
            search = np.where((freq >= search_lo_hz) &
                              (freq <= search_hi_hz))[0]
            if len(search) == 0:
                raise SystemExit("carrier search window contains no FFT bins")
            k = int(search[np.argmax(pbin[search])])
            df = fs / n

            lo = max(0, k - tone_half_bins)
            hi = min(n, k + tone_half_bins + 1)
            tone_power = float(np.sum(pbin[lo:hi]))

            carrier_off = float(freq[k])
            distance = np.abs(freq - carrier_off)
            noise_mask = ((distance >= noise_near_hz) &
                          (distance <= noise_far_hz) &
                          (np.abs(freq) >= 5_000.0))
            noise_mask[lo:hi] = False
            noise_bins = pbin[noise_mask]
            if len(noise_bins) < 20:
                raise SystemExit("too few local noise bins")
            noise_bin_power = float(np.median(noise_bins) / math.log(2.0))
            noise_psd = noise_bin_power / df

            carrier_dbfs = db10(tone_power)
            noise_dbfs_hz = db10(noise_psd)
            cn0_dbhz = carrier_dbfs - noise_dbfs_hz

            clip_fraction = float(np.mean(
                (np.abs(z.real) >= 0.98) | (np.abs(z.imag) >= 0.98)))

            rows.append({
                "block": int(block_idx),
                "carrier_bin": k,
                "carrier_offset_hz": carrier_off,
                "carrier_dbfs": carrier_dbfs,
                "noise_psd_dbfs_hz": noise_dbfs_hz,
                "cn0_dbhz": cn0_dbhz,
                "clip_fraction": clip_fraction,
                "df_hz": df,
                "fs_hz": fs,
                "n": n,
                "bit_depth": bit_depth,
            })

    if not rows:
        raise SystemExit(f"{path}: no data blocks")

    cmean, cstd = mean_std([r["carrier_dbfs"] for r in rows])
    nmean, nstd = mean_std([r["noise_psd_dbfs_hz"] for r in rows])
    qmean, qstd = mean_std([r["cn0_dbhz"] for r in rows])
    fmean, fstd = mean_std([r["carrier_offset_hz"] for r in rows])
    peak_bins = [r["carrier_bin"] for r in rows]
    mode_bin = st.mode(peak_bins)
    clip_max = max(r["clip_fraction"] for r in rows) * 100.0
    clip_mean = float(np.mean([r["clip_fraction"] for r in rows])) * 100.0
    expected_offset = expected_hz - center_hz

    return {
        "file": path,
        "blocks": len(rows),
        "bit_depth": rows[0]["bit_depth"],
        "sample_rate_hz": rows[0]["fs_hz"],
        "block_size": rows[0]["n"],
        "bin_width_hz": rows[0]["df_hz"],
        "mode_carrier_bin": mode_bin,
        "carrier_dbfs_mean": cmean,
        "carrier_dbfs_std": cstd,
        "noise_psd_dbfs_hz_mean": nmean,
        "noise_psd_dbfs_hz_std": nstd,
        "cn0_dbhz_mean": qmean,
        "cn0_dbhz_std": qstd,
        "carrier_offset_hz_mean": fmean,
        "carrier_offset_hz_std": fstd,
        "carrier_frequency_hz_mean": center_hz + fmean,
        "frequency_error_hz_mean": fmean - expected_offset,
        "clip_pct_mean": clip_mean,
        "clip_pct_max": clip_max,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card")
    ap.add_argument("--center-hz", type=float, required=True)
    ap.add_argument("--expected-hz", type=float, required=True)
    ap.add_argument("--search-lo-hz", type=float, default=10_000.0)
    ap.add_argument("--search-hi-hz", type=float, default=20_000.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--generator-dbm", type=float, default=float("nan"))
    ap.add_argument("--csv", help="append one summary row to this CSV")
    args = ap.parse_args()

    out = analyze(args.card, args.center_hz, args.expected_hz,
                  args.search_lo_hz, args.search_hi_hz)
    out = {"label": args.label, "generator_dbm": args.generator_dbm, **out}

    for key, value in out.items():
        if isinstance(value, float):
            print(f"{key}: {value:.6f}")
        else:
            print(f"{key}: {value}")

    if args.csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
        exists = os.path.exists(args.csv) and os.path.getsize(args.csv) > 0
        with open(args.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out.keys()))
            if not exists:
                w.writeheader()
            w.writerow(out)


if __name__ == "__main__":
    main()
