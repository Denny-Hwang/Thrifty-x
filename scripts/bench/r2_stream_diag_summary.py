#!/usr/bin/env python3
# ruff: noqa: E501
"""Summarize the repeated Airspy R2 stream diagnostic."""

import argparse
import csv
import math
import re
import statistics
from pathlib import Path

import numpy as np

NOISE_JUMP_DB = 3.0
RMS_JUMP_DB = 6.0
STEM = re.compile(r"^rep(?P<rep>\d+)_(?P<condition>.+)$")


def num(row, key):
    value = row.get(key, "")
    try:
        return float(value) if value not in ("", None) else float("nan")
    except ValueError:
        return float("nan")


def fmt(value, n=2):
    return "nan" if not math.isfinite(value) else f"{value:.{n}f}"


def mean(values):
    values = [x for x in values if math.isfinite(x)]
    return statistics.fmean(values) if values else float("nan")


def pstdev(values):
    values = [x for x in values if math.isfinite(x)]
    return statistics.pstdev(values) if len(values) > 1 else (0.0 if values else float("nan"))


def load(root):
    rows = []
    for path in sorted(root.glob("rep*_*.csv")):
        match = STEM.match(path.stem)
        if not match:
            continue
        with path.open(newline="") as f:
            for index, row in enumerate(csv.DictReader(f), 1):
                row["_rep"] = int(match.group("rep"))
                row["_condition"] = match.group("condition")
                row["_source"] = path.name
                row["_row"] = index
                rows.append(row)
    if not rows:
        raise SystemExit(f"no rep*_*.csv files found under {root}")
    return rows


def classify(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["_condition"], []).append(row)

    baselines = {}
    for condition, items in groups.items():
        active = [r for r in items if math.isfinite(num(r, "tx_dbm"))]
        preferred = [r for r in active
                     if num(r, "dropped") == 0 and num(r, "near_fs_frac") == 0]
        pool = preferred or active
        noise = [num(r, "noise_dbfs_hz") for r in pool
                 if math.isfinite(num(r, "noise_dbfs_hz"))]
        rms = [num(r, "rms_dbfs") for r in pool
               if math.isfinite(num(r, "rms_dbfs"))]
        baselines[condition] = (
            float(np.percentile(noise, 25)) if noise else float("nan"),
            float(np.percentile(rms, 25)) if rms else float("nan"),
        )

    for row in rows:
        noise0, rms0 = baselines[row["_condition"]]
        reasons = []
        if num(row, "dropped") > 0:
            reasons.append("drop")
        if num(row, "near_fs_frac") > 0:
            reasons.append("near_fs")
        if math.isfinite(noise0) and num(row, "noise_dbfs_hz") > noise0 + NOISE_JUMP_DB:
            reasons.append("noise_jump")
        if math.isfinite(rms0) and num(row, "rms_dbfs") > rms0 + RMS_JUMP_DB:
            reasons.append("rms_jump")
        row["_bad"] = bool(reasons)
        row["_reason"] = "+".join(reasons) if reasons else "clean"
    return groups, baselines


def write_combined(root, rows):
    out = root / "combined.csv"
    base_fields = list(csv.DictReader(open(root / rows[0]["_source"])).fieldnames)
    fields = ["rep", "condition", "source", "source_row", "bad", "reason"] + base_fields
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "rep": row["_rep"], "condition": row["_condition"],
                "source": row["_source"], "source_row": row["_row"],
                "bad": int(row["_bad"]), "reason": row["_reason"],
                **{k: row.get(k, "") for k in base_fields},
            })


def condition_stats(items):
    active = [r for r in items if math.isfinite(num(r, "tx_dbm"))]
    clean = [r for r in active if not r["_bad"]]
    result = {
        "active": len(active),
        "bad": sum(r["_bad"] for r in active),
        "drops": sum(num(r, "dropped") > 0 for r in active),
        "near": sum(num(r, "near_fs_frac") > 0 for r in active),
        "noise_jumps": sum("noise_jump" in r["_reason"] for r in active),
        "rms_jumps": sum("rms_jump" in r["_reason"] for r in active),
    }
    for tx in sorted({num(r, "tx_dbm") for r in clean}):
        s = [r for r in clean if num(r, "tx_dbm") == tx]
        result[tx] = {
            "n": len(s),
            "carrier": mean([num(r, "carrier_dbfs") for r in s]),
            "carrier_std": pstdev([num(r, "carrier_dbfs") for r in s]),
            "noise": mean([num(r, "noise_dbfs_hz") for r in s]),
            "noise_std": pstdev([num(r, "noise_dbfs_hz") for r in s]),
            "cn0": mean([num(r, "cn0_dbhz") for r in s]),
            "cn0_std": pstdev([num(r, "cn0_dbhz") for r in s]),
            "rms": mean([num(r, "rms_dbfs") for r in s]),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory
    rows = load(root)
    groups, baselines = classify(rows)
    write_combined(root, rows)

    print("# R2 stream diagnostic summary\n")
    print(f"Directory: {root}\n")
    print("Bad row = any drop, any near-full-scale sample, noise floor jump > "
          f"{NOISE_JUMP_DB:g} dB, or RMS jump > {RMS_JUMP_DB:g} dB.\n")

    stats = {}
    print("## Condition health\n")
    print("| condition | active | bad | bad % | drop | near-FS | noise jump | RMS jump | noise baseline | RMS baseline |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for condition in sorted(groups):
        st = condition_stats(groups[condition])
        stats[condition] = st
        noise0, rms0 = baselines[condition]
        pct = 100 * st["bad"] / st["active"] if st["active"] else float("nan")
        print(f"| {condition} | {st['active']} | {st['bad']} | {fmt(pct,1)} | "
              f"{st['drops']} | {st['near']} | {st['noise_jumps']} | "
              f"{st['rms_jumps']} | {fmt(noise0)} | {fmt(rms0)} |")

    print("\n## Clean RF-ON statistics\n")
    print("| condition | TX | n | carrier | sigma | noise/Hz | sigma | C/N0 | sigma | RMS |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for condition in sorted(stats):
        st = stats[condition]
        for tx in sorted(k for k in st if isinstance(k, float)):
            x = st[tx]
            print(f"| {condition} | {tx:g} | {x['n']} | {fmt(x['carrier'])} | "
                  f"{fmt(x['carrier_std'],3)} | {fmt(x['noise'])} | "
                  f"{fmt(x['noise_std'],3)} | {fmt(x['cn0'])} | "
                  f"{fmt(x['cn0_std'],3)} | {fmt(x['rms'])} |")

    print("\n## Clean -100 to -90 response\n")
    print("| condition | delta carrier | delta C/N0 | delta noise |")
    print("| --- | ---: | ---: | ---: |")
    for condition in sorted(stats):
        lo, hi = stats[condition].get(-100.0), stats[condition].get(-90.0)
        if lo and hi:
            print(f"| {condition} | {fmt(hi['carrier']-lo['carrier'],3)} | "
                  f"{fmt(hi['cn0']-lo['cn0'],3)} | "
                  f"{fmt(hi['noise']-lo['noise'],3)} |")

    print("\n## Interpretation\n")
    a = stats.get("A_2p5_reg_on")
    b = stats.get("B_10m_reg_on")
    c = stats.get("C_10m_reg_off")
    if a and b and c:
        ar = a["bad"] / a["active"] if a["active"] else 1
        br = b["bad"] / b["active"] if b["active"] else 1
        cr = c["bad"] / c["active"] if c["active"] else 1
        if ar <= .05 and br >= .20 and cr <= .05:
            print("- Result supports the register-read/control-transfer hypothesis.")
        elif ar <= .05 and br >= .20 and cr >= .20:
            print("- Both 10 MSPS conditions are disturbed: focus on 10 MSPS streaming/USB/libairspy/usbipd.")
        elif ar >= .20:
            print("- Disturbance is not confined to 10 MSPS: inspect bias power/RF chain and USB health.")
        elif br <= .05 and cr <= .05:
            print("- 10 MSPS disturbance was not reproduced; increase REPEATS before assigning a cause.")
        else:
            print("- Mixed result; inspect combined.csv and repeat the matrix.")
    print("\nEvery raw row plus diagnostic flags is in combined.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
