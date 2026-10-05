#!/usr/bin/env python3
# ruff: noqa: E501
"""Compare paired RTL-SDR and Airspy R2 RF-equivalence qualification runs.

The runners store raw device-relative measurements. This script preserves that
raw difference, then builds a held-out input-referred calibration so the claim
"the same controlled input is measured the same" can be tested without
pretending dBFS has a common physical reference across the two receivers.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

PRIMARY = "primary"
GAINMAP = "gainmap"


def fnum(row, key):
    value = row.get(key, "")
    try:
        return float(value) if value not in ("", None) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def finite(x):
    return math.isfinite(x)


def mean(xs):
    xs = [x for x in xs if finite(x)]
    return float(np.mean(xs)) if xs else float("nan")


def std(xs):
    xs = [x for x in xs if finite(x)]
    return float(np.std(xs, ddof=1)) if len(xs) > 1 else (0.0 if xs else float("nan"))


def rms(xs):
    xs = [x for x in xs if finite(x)]
    return float(np.sqrt(np.mean(np.square(xs)))) if xs else float("nan")


def fmt(x, n=3):
    return "nan" if not finite(x) else f"{x:.{n}f}"


def phase_from_name(name):
    # Current device-prefixed publication filenames.
    if "_phaseA_" in name:
        return "primary"
    if "_phaseB_" in name:
        return "gainmap"
    if "_phaseC_" in name:
        return "stage"
    if "_phaseD_" in name:
        return "register"
    # Backward compatibility for earlier unprefixed files.
    if name.startswith("primary_"):
        return "primary"
    if name.startswith("gainmap_"):
        return "gainmap"
    if name.startswith("stage_sensitivity_"):
        return "stage"
    return "other"


def load(root):
    rows = []
    # Current publication layout uses device-labelled directories. Keep the
    # older rtl/r2 names as read-only compatibility for earlier bench runs.
    directories = ("RTL", "R2-A", "R2-B", "rtl", "r2")
    for directory_name in directories:
        directory = root / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*_phase*.csv")):
            # Do not re-ingest generated combined files.
            if "_combined_" in path.name:
                continue
            phase = phase_from_name(path.name)
            with path.open(newline="") as handle:
                for source_row, row in enumerate(csv.DictReader(handle), 1):
                    item = dict(row)
                    unit = item.get("unit") or directory_name
                    item["_device_label"] = unit
                    item["_side"] = (
                        "rtl" if str(unit).upper().startswith("RTL") else "r2"
                    )
                    item["_phase"] = phase
                    item["_source"] = path.name
                    item["_source_key"] = f"{directory_name}/{path.name}"
                    item["_source_row"] = source_row
                    center = fnum(item, "center_hz")
                    tone = fnum(item, "tone_hz")
                    item["_tone_offset_hz"] = tone - center if finite(center) and finite(tone) else float("nan")
                    item["_tx"] = fnum(item, "tx_dbm")
                    item["_carrier"] = fnum(item, "carrier_dbfs")
                    item["_cn0"] = fnum(item, "cn0_dbhz")
                    item["_noise"] = fnum(item, "noise_dbfs_hz")
                    item["_rate"] = int(round(fnum(item, "rate"))) if finite(fnum(item, "rate")) else 0
                    item["_dropped"] = fnum(item, "dropped")
                    item["_near_fs"] = fnum(item, "near_fs_frac")
                    item["_detected"] = int(float(item.get("detected") or 0))
                    item["_valid"] = (
                        finite(item["_tx"]) and finite(item["_carrier"])
                        and item["_detected"] == 1
                        and (not finite(item["_dropped"]) or item["_dropped"] == 0)
                        and (not finite(item["_near_fs"]) or item["_near_fs"] == 0)
                    )
                    rows.append(item)

    # If the detector sees the test tone while the generator is explicitly
    # RF OFF, that whole sweep is contaminated by an ambient/interfering spur
    # at the tone frequency. Keep every raw row for QC, but do not let that
    # sweep contribute to transfer-function fits or held-out calibration.
    contaminated = {
        r["_source_key"]
        for r in rows
        if not finite(r["_tx"]) and r["_detected"] == 1
    }
    for r in rows:
        r["_rf_off_contaminated"] = r["_source_key"] in contaminated
        if r["_rf_off_contaminated"]:
            r["_valid"] = False
    return rows


def config_name(row):
    rate_m = row["_rate"] / 1e6
    label = row.get("_device_label") or (
        "RTL" if row["_side"] == "rtl" else "R2"
    )
    return f"{label}:{row.get('setting','')}@{rate_m:g}M"


def linear_fit(rows):
    x = np.array([r["_tx"] for r in rows], dtype=float)
    y = np.array([r["_carrier"] for r in rows], dtype=float)
    if len(x) < 2 or len(np.unique(x)) < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    residual = y - pred
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": r2,
        "resid_rms": rms(residual.tolist()),
        "resid_max": float(np.max(np.abs(residual))),
        "n": int(len(x)),
    }


def calibration_fit(rows):
    """Fit on alternating input levels, validate on held-out levels."""
    good = [r for r in rows if r["_valid"]]
    levels = sorted({r["_tx"] for r in good})
    cal_levels = set(levels[::2])
    val_levels = set(levels[1::2])
    cal = [r for r in good if r["_tx"] in cal_levels]
    val = [r for r in good if r["_tx"] in val_levels]
    fit = linear_fit(cal)
    if not fit or not val or abs(fit["slope"]) < 1e-12:
        return None
    errors = []
    predictions = []
    for r in val:
        pred_tx = (r["_carrier"] - fit["intercept"]) / fit["slope"]
        err = pred_tx - r["_tx"]
        errors.append(err)
        predictions.append({
            "tone_offset_hz": r["_tone_offset_hz"],
            "tx_dbm": r["_tx"],
            "pred_tx_dbm": pred_tx,
            "error_db": err,
            "source": r["_source"],
            "source_row": r["_source_row"],
        })
    return {
        **fit,
        "cal_levels": sorted(cal_levels),
        "validation_levels": sorted(val_levels),
        "validation_n": len(errors),
        "validation_bias": mean(errors),
        "validation_mae": mean([abs(x) for x in errors]),
        "validation_rmse": rms(errors),
        "validation_max_abs": max(abs(x) for x in errors),
        "predictions": predictions,
    }


def primary_by_config(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["_phase"] == PRIMARY:
            groups[config_name(r)].append(r)
    return groups


def averaged_points(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["_phase"] != PRIMARY or not r["_valid"]:
            continue
        key = (config_name(r), r["_tone_offset_hz"], r["_tx"])
        groups[key].append(r)
    out = {}
    for key, items in groups.items():
        out[key] = {
            "carrier": mean([r["_carrier"] for r in items]),
            "carrier_std": std([r["_carrier"] for r in items]),
            "cn0": mean([r["_cn0"] for r in items]),
            "noise": mean([r["_noise"] for r in items]),
            "n": len(items),
        }
    return out


def identify_primary_configs(groups):
    rtl = [k for k in groups if k.startswith("RTL:")]
    r2 = [
        k for k in groups
        if k.startswith("R2-") and ":0/0/8@" in k
    ]
    # Backward compatibility for older single-R2 directories.
    r2.extend(
        k for k in groups
        if k.startswith("R2:0/0/8@") and k not in r2
    )
    return (rtl[0] if len(rtl) == 1 else None, sorted(r2))


def paired_raw(points, rtl_cfg, r2_cfg):
    pairs = []
    for (cfg, tone, tx), rv in points.items():
        if cfg != rtl_cfg:
            continue
        other = points.get((r2_cfg, tone, tx))
        if other is None:
            continue
        pairs.append({
            "tone_offset_hz": tone,
            "tx_dbm": tx,
            "rtl_carrier": rv["carrier"],
            "r2_carrier": other["carrier"],
            "delta_carrier": other["carrier"] - rv["carrier"],
            "rtl_cn0": rv["cn0"],
            "r2_cn0": other["cn0"],
            "delta_cn0": other["cn0"] - rv["cn0"],
            "rtl_noise": rv["noise"],
            "r2_noise": other["noise"],
            "delta_noise": other["noise"] - rv["noise"],
        })
    return pairs


def prediction_means(cal):
    groups = defaultdict(list)
    for p in cal["predictions"]:
        groups[(p["tone_offset_hz"], p["tx_dbm"])].append(p["pred_tx_dbm"])
    return {k: mean(v) for k, v in groups.items()}


def agreement(rtl_cal, r2_cal):
    a = prediction_means(rtl_cal)
    b = prediction_means(r2_cal)
    diffs = []
    points = []
    for key in sorted(set(a) & set(b)):
        d = b[key] - a[key]
        diffs.append(d)
        points.append((key[0], key[1], a[key], b[key], d))
    bias = mean(diffs)
    s = std(diffs)
    return {
        "n": len(diffs),
        "bias": bias,
        "sd": s,
        "loa_low": bias - 1.96 * s if finite(s) else float("nan"),
        "loa_high": bias + 1.96 * s if finite(s) else float("nan"),
        "max_abs": max((abs(x) for x in diffs), default=float("nan")),
        "points": points,
    }


def quality_counts(rows):
    primary = [r for r in rows if r["_phase"] == PRIMARY and finite(r["_tx"])]
    return {
        "active_rows": len(primary),
        "valid_rows": sum(r["_valid"] for r in primary),
        "drop_rows": sum(
            finite(r["_dropped"]) and r["_dropped"] > 0 for r in primary
        ),
        "near_fs_rows": sum(
            finite(r["_near_fs"]) and r["_near_fs"] > 0 for r in primary
        ),
        "nondetect_rows": sum(r["_detected"] == 0 for r in primary),
        "rf_off_contaminated_rows": sum(
            r.get("_rf_off_contaminated", False) for r in primary
        ),
        "rf_off_contaminated_sweeps": len({
            r["_source_key"] for r in primary
            if r.get("_rf_off_contaminated", False)
        }),
    }


GAIN_MATCH = {
    0.0: "0/0/8",
    12.5: "4/3/8",
    20.7: "6/6/8",
    29.7: "8/8/8",
    40.2: "11/11/8",
}


def gainmap_summary(rows):
    rtl = [
        r for r in rows
        if r["_phase"] == GAINMAP and r["_side"] == "rtl" and r["_valid"]
    ]
    r2 = [
        r for r in rows
        if r["_phase"] == GAINMAP and r["_side"] == "r2" and r["_valid"]
    ]
    result = []
    for gain, stage in GAIN_MATCH.items():
        rr = [
            r for r in rtl
            if finite(fnum(r, "rtl_gain_db"))
            and abs(fnum(r, "rtl_gain_db") - gain) < 0.05
        ]
        if not rr:
            continue
        units = sorted({r.get("_device_label", "R2") for r in r2})
        for unit in units:
            unit_rows = [r for r in r2 if r.get("_device_label") == unit]
            for rate in sorted({r["_rate"] for r in unit_rows}):
                aa = [
                    r for r in unit_rows
                    if r["_rate"] == rate and r.get("setting") == stage
                ]
                if not aa:
                    continue
                rf = linear_fit(rr)
                af = linear_fit(aa)
                # Paired mean offsets at common powers.
                rtl_by = defaultdict(list)
                r2_by = defaultdict(list)
                for x in rr:
                    rtl_by[x["_tx"]].append(x)
                for x in aa:
                    r2_by[x["_tx"]].append(x)
                dc, dn = [], []
                for tx in sorted(set(rtl_by) & set(r2_by)):
                    rc = mean([x["_carrier"] for x in rtl_by[tx]])
                    ac = mean([x["_carrier"] for x in r2_by[tx]])
                    rn = mean([x["_cn0"] for x in rtl_by[tx]])
                    an = mean([x["_cn0"] for x in r2_by[tx]])
                    dc.append(ac - rc)
                    dn.append(an - rn)
                result.append({
                    "r2_unit": unit,
                    "rtl_gain_db": gain,
                    "r2_stage": stage,
                    "r2_rate": rate,
                    "rtl_slope": rf["slope"] if rf else float("nan"),
                    "r2_slope": af["slope"] if af else float("nan"),
                    "delta_carrier_mean": mean(dc),
                    "delta_carrier_std": std(dc),
                    "delta_cn0_mean": mean(dn),
                    "n_levels": len(dc),
                })
    return result


def write_points(path, raw_pairs, agreements):
    fields = ["kind", "r2_config", "tone_offset_hz", "tx_dbm",
              "rtl_value", "r2_value", "delta_db"]
    with path.open("w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=fields)
        w.writeheader()
        for cfg, pairs in raw_pairs.items():
            for p in pairs:
                w.writerow({
                    "kind": "raw_carrier",
                    "r2_config": cfg,
                    "tone_offset_hz": p["tone_offset_hz"],
                    "tx_dbm": p["tx_dbm"],
                    "rtl_value": p["rtl_carrier"],
                    "r2_value": p["r2_carrier"],
                    "delta_db": p["delta_carrier"],
                })
                w.writerow({
                    "kind": "cn0",
                    "r2_config": cfg,
                    "tone_offset_hz": p["tone_offset_hz"],
                    "tx_dbm": p["tx_dbm"],
                    "rtl_value": p["rtl_cn0"],
                    "r2_value": p["r2_cn0"],
                    "delta_db": p["delta_cn0"],
                })
        for cfg, a in agreements.items():
            for tone, tx, rtl_v, r2_v, d in a["points"]:
                w.writerow({
                    "kind": "calibrated_input",
                    "r2_config": cfg,
                    "tone_offset_hz": tone,
                    "tx_dbm": tx,
                    "rtl_value": rtl_v,
                    "r2_value": r2_v,
                    "delta_db": d,
                })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    root = args.run_directory
    rows = load(root)
    if not rows:
        raise SystemExit(f"no validation CSV files found under {root}/rtl or {root}/r2")

    groups = primary_by_config(rows)
    rtl_cfg, r2_cfgs = identify_primary_configs(groups)
    if rtl_cfg is None:
        raise SystemExit("expected exactly one RTL primary configuration")
    if not r2_cfgs:
        raise SystemExit("no R2 0/0/8 primary configurations found")

    calibrations = {}
    fits = {}
    for cfg, items in groups.items():
        valid = [r for r in items if r["_valid"]]
        fits[cfg] = linear_fit(valid)
        calibrations[cfg] = calibration_fit(items)

    points = averaged_points(rows)
    raw_pairs = {cfg: paired_raw(points, rtl_cfg, cfg) for cfg in r2_cfgs}

    agreements = {}
    for cfg in r2_cfgs:
        if calibrations.get(rtl_cfg) and calibrations.get(cfg):
            agreements[cfg] = agreement(calibrations[rtl_cfg], calibrations[cfg])

    gainmap = gainmap_summary(rows)
    quality = {cfg: quality_counts(items) for cfg, items in groups.items()}

    serialisable_cal = {}
    for cfg, cal in calibrations.items():
        if cal:
            serialisable_cal[cfg] = {k: v for k, v in cal.items() if k != "predictions"}
    payload = {
        "run_directory": str(root),
        "primary_fits": fits,
        "held_out_calibration": serialisable_cal,
        "agreement_vs_rtl": {k: {kk: vv for kk, vv in v.items() if kk != "points"}
                             for k, v in agreements.items()},
        "gainmap": gainmap,
        "quality": quality,
    }
    (root / "calibration.json").write_text(json.dumps(payload, indent=2) + "\n")
    write_points(root / "comparison_points.csv", raw_pairs, agreements)

    lines = []
    lines += ["# RTL-SDR vs Airspy R2 equivalence report", ""]
    lines += [f"Run: `{root}`", ""]
    lines += ["## Primary transfer functions", ""]
    lines += ["| configuration | n | slope dB/dB | intercept dB | R^2 | residual RMS dB | max residual dB |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for cfg in sorted(fits):
        x = fits[cfg]
        if not x:
            continue
        lines.append(f"| {cfg} | {x['n']} | {fmt(x['slope'],4)} | {fmt(x['intercept'])} | "
                     f"{fmt(x['r2'],6)} | {fmt(x['resid_rms'])} | {fmt(x['resid_max'])} |")

    lines += ["", "## Data quality", "",
              "| configuration | active | valid | drops | near-FS | non-detect | RF-off contaminated rows | RF-off contaminated sweeps |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for cfg in sorted(quality):
        q = quality[cfg]
        lines.append(
            f"| {cfg} | {q['active_rows']} | {q['valid_rows']} | "
            f"{q['drop_rows']} | {q['near_fs_rows']} | "
            f"{q['nondetect_rows']} | {q['rf_off_contaminated_rows']} | "
            f"{q['rf_off_contaminated_sweeps']} |"
        )

    lines += ["", "## Held-out input-referred calibration", "",
              "Alternating generator levels are used for fitting; the remaining levels are held out.",
              "",
              "| configuration | slope | calibration levels | validation levels | bias dB | MAE dB | RMSE dB | max abs dB |",
              "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |"]
    for cfg in sorted(calibrations):
        c = calibrations[cfg]
        if not c:
            continue
        lines.append(f"| {cfg} | {fmt(c['slope'],4)} | {c['cal_levels']} | {c['validation_levels']} | "
                     f"{fmt(c['validation_bias'])} | {fmt(c['validation_mae'])} | "
                     f"{fmt(c['validation_rmse'])} | {fmt(c['validation_max_abs'])} |")

    lines += ["", "## Raw R2 - RTL agreement at common input points", "",
              "| R2 configuration | paired points | mean delta carrier dB | sigma dB | mean delta C/N0 dB | sigma dB |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for cfg in r2_cfgs:
        pairs = raw_pairs[cfg]
        dc = [p["delta_carrier"] for p in pairs]
        dn = [p["delta_cn0"] for p in pairs]
        lines.append(f"| {cfg} | {len(pairs)} | {fmt(mean(dc))} | {fmt(std(dc))} | "
                     f"{fmt(mean(dn))} | {fmt(std(dn))} |")

    lines += ["", "## Calibrated R2 - RTL input agreement (Bland-Altman)", "",
              "| R2 configuration | n | bias dB | SD dB | 95% LoA low | 95% LoA high | max abs dB |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for cfg in r2_cfgs:
        a = agreements.get(cfg)
        if not a:
            continue
        lines.append(f"| {cfg} | {a['n']} | {fmt(a['bias'])} | {fmt(a['sd'])} | "
                     f"{fmt(a['loa_low'])} | {fmt(a['loa_high'])} | {fmt(a['max_abs'])} |")

    if gainmap:
        lines += ["", "## Matched R820T gain-code map", "",
                  "| R2 unit | RTL gain dB | R2 stage | R2 rate | RTL slope | R2 slope | mean delta carrier dB | sigma dB | mean delta C/N0 dB | levels |",
                  "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for g in gainmap:
            lines.append(f"| {g.get('r2_unit','R2')} | {g['rtl_gain_db']:g} | {g['r2_stage']} | {g['r2_rate']/1e6:g}M | "
                         f"{fmt(g['rtl_slope'],4)} | {fmt(g['r2_slope'],4)} | "
                         f"{fmt(g['delta_carrier_mean'])} | {fmt(g['delta_carrier_std'])} | "
                         f"{fmt(g['delta_cn0_mean'])} | {g['n_levels']} |")

    lines += ["", "## Qualification notes", ""]
    for cfg, fit in sorted(fits.items()):
        if not fit:
            continue
        verdict = "PASS" if 0.98 <= fit["slope"] <= 1.02 and fit["resid_rms"] <= 0.5 else "CHECK"
        lines.append(f"- {cfg}: linearity {verdict} (slope {fit['slope']:.4f}, residual RMS {fit['resid_rms']:.3f} dB).")
    for cfg, cal in sorted(calibrations.items()):
        if not cal:
            continue
        verdict = "PASS" if abs(cal["validation_bias"]) <= 0.5 and cal["validation_mae"] <= 0.5 and cal["validation_max_abs"] <= 1.0 else "CHECK"
        lines.append(f"- {cfg}: held-out input reconstruction {verdict} "
                     f"(bias {cal['validation_bias']:+.3f} dB, MAE {cal['validation_mae']:.3f} dB, "
                     f"max {cal['validation_max_abs']:.3f} dB).")
    lines += ["",
              "Raw dBFS equality is not an acceptance criterion. Device-relative dBFS offsets are reported;",
              "the held-out input-referred result is the test of whether equal controlled inputs can be",
              "reconstructed consistently. C/N0 differences remain visible as receiver-performance differences.",
              "",
              "See `docs/bench/rtl_r2_equivalence_validation.md` for the full methodology."]

    report = "\n".join(lines) + "\n"
    (root / "comparison.md").write_text(report)
    print(report)
    print(f"Wrote: {root / 'comparison.md'}")
    print(f"Wrote: {root / 'comparison_points.csv'}")
    print(f"Wrote: {root / 'calibration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
