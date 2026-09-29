#!/usr/bin/env python3
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""
Cross-device statistics from bench_cw_level.py CSVs (RTL-SDR vs Airspy).

Recomputes, from the raw rows, the figures the dBFS audit
(docs/rtl_vs_airspy_dbfs_audit.md) relies on.  Files may be passed in any
number and order; every row is classified by its own columns (``unit``,
``device``, ``rate``, ``tone_hz``, ``setting``), never by its file name.

RF-ON rows (``tx_dbm`` set) and RF-OFF rows (``tx_dbm`` empty) are kept
apart: carrier, noise and C/N0 statistics use RF-ON rows with
``detected == 1`` only, and RF-OFF rows only feed the RF-OFF noise and
false-detection counts.  Rows of one group at the same level are
averaged before pairing.

Printed:
  groups    per unit/rate/tone: rows, carrier slope vs generator level,
            mean frequency error (ppm), dropped-sample incidence,
            RF-OFF detections and noise
  vs ref    every non-reference unit/rate against the reference device
            (default: the rtlsdr rows), paired by tone and level:
            mean and sigma of the carrier, noise-density and C/N0
            differences
  rates     per unit, each rate against the unit's lowest rate
  units     per rate, each unit against the first unit (name order)

Sigma is printed both as the population (ddof=0) and the sample
(ddof=1) standard deviation of the paired differences.

Example:
  bench_cw_audit.py bench/verify3freq/*.csv --setting g0,0/0/8
"""

import argparse
import csv
import math
import sys
from collections import defaultdict

import numpy as np


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return float('nan')


def load(paths):
    """All rows of all *paths*, numeric columns parsed."""
    rows = []
    for path in paths:
        with open(path, newline='') as f:
            for row in csv.DictReader(f):
                row['_file'] = path
                row['tx'] = (None if not row.get('tx_dbm', '').strip()
                             else float(row['tx_dbm']))
                for key in ('rate', 'tone_hz', 'center_hz', 'carrier_dbfs',
                            'noise_dbfs_hz', 'cn0_dbhz', 'freq_error_ppm',
                            'near_fs_frac'):
                    row[key] = _float(row.get(key))
                row['detected'] = str(row.get('detected', '')).strip() == '1'
                dropped = str(row.get('dropped', '')).strip()
                row['dropped'] = int(float(dropped)) if dropped else 0
                rows.append(row)
    return rows


def group_key(row):
    return (row['unit'], row['device'], int(row['rate']),
            int(row['tone_hz']), row['setting'])


def _mean(values):
    values = [v for v in values if not math.isnan(v)]
    return float(np.mean(values)) if values else float('nan')


def _stats(values):
    """(n, mean, sigma ddof=0, sigma ddof=1) of *values*."""
    a = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    if a.size == 0:
        return 0, float('nan'), float('nan'), float('nan')
    return (a.size, float(a.mean()), float(a.std()),
            float(a.std(ddof=1)) if a.size > 1 else float('nan'))


def levels(rows, max_clip):
    """{tx: averaged RF-ON figures} of one group's rows."""
    by_tx = defaultdict(list)
    for row in rows:
        if (row['tx'] is not None and row['detected']
                and not row['near_fs_frac'] > max_clip):
            by_tx[row['tx']].append(row)
    return {tx: {k: _mean([r[k] for r in items])
                 for k in ('carrier_dbfs', 'noise_dbfs_hz', 'cn0_dbhz',
                           'freq_error_ppm')}
            for tx, items in by_tx.items()}


def slope(cells):
    """Fitted d(carrier_dbfs)/d(tx) (1.0 = linear)."""
    pts = sorted((tx, c['carrier_dbfs']) for tx, c in cells.items())
    if len(pts) < 2:
        return float('nan')
    x, y = np.array(pts).T
    return float(np.polyfit(x, y, 1)[0])


def paired(a_cells, b_cells):
    """{field: [b - a]} over the levels both have."""
    out = defaultdict(list)
    for tx in sorted(set(a_cells) & set(b_cells)):
        for field in ('carrier_dbfs', 'noise_dbfs_hz', 'cn0_dbhz'):
            out[field].append(b_cells[tx][field] - a_cells[tx][field])
    return out


def _fmt_stats(values):
    n, mean, s0, s1 = _stats(values)
    return f"{mean:+8.3f} {s0:6.3f} {s1:6.3f}", n


def compare(groups, cells, left, right):
    """Pooled ``right - left`` over every tone both unit/rate sets have.

    *left* and *right* are (unit, rate) pairs; tones are paired by
    ``tone_hz`` and levels by ``tx``.
    """
    pooled = defaultdict(list)
    for key in groups:
        unit, _, rate, tone, setting = key
        if (unit, rate) != right:
            continue
        for other in groups:
            if (other[0], other[2]) == left and other[3] == tone:
                for field, values in paired(cells[other],
                                            cells[key]).items():
                    pooled[field].extend(values)
    return pooled


def _print_compare(title, pairs, groups, cells):
    print(f"\n### {title}\n")
    print("| comparison | Δcarrier dB (mean σ0 σ1) | Δnoise dB/Hz "
          "(mean σ0 σ1) | ΔC/N0 dB (mean σ0 σ1) | points |")
    print("|---|---|---|---|---|")
    for left, right in pairs:
        pooled = compare(groups, cells, left, right)
        if not pooled:
            continue
        c, n = _fmt_stats(pooled['carrier_dbfs'])
        nz, _ = _fmt_stats(pooled['noise_dbfs_hz'])
        cn, _ = _fmt_stats(pooled['cn0_dbhz'])
        name = (f"{right[0]} {right[1] / 1e6:g}M − "
                f"{left[0]} {left[1] / 1e6:g}M")
        print(f"| {name} | {c} | {nz} | {cn} | {n} |")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('csv', nargs='+')
    parser.add_argument('--setting', default=None,
                        help="keep only these settings, comma separated "
                             "(e.g. g0,0/0/8)")
    parser.add_argument('--ref-device', default='rtlsdr',
                        help="device of the reference rows [rtlsdr]")
    parser.add_argument('--max-clip', type=float, default=1e-5,
                        help="exclude RF-ON rows with near_fs_frac above "
                             "this [1e-5]")
    args = parser.parse_args(argv)

    rows = load(args.csv)
    if args.setting:
        keep = {s.strip() for s in args.setting.split(',')}
        rows = [r for r in rows if r['setting'] in keep]
    if not rows:
        raise SystemExit("no rows")

    groups = defaultdict(list)
    for row in rows:
        groups[group_key(row)].append(row)
    cells = {key: levels(items, args.max_clip)
             for key, items in groups.items()}

    print("### Groups\n")
    print("| unit | device | rate | tone | setting | RF-ON | RF-OFF | "
          "slope | freq err ppm (mean σ0) | dropped rows | dropped "
          "samples | RF-OFF detected | RF-OFF noise dB/Hz |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key in sorted(groups):
        items = groups[key]
        on = [r for r in items if r['tx'] is not None]
        off = [r for r in items if r['tx'] is None]
        ppm = [r['freq_error_ppm'] for r in on if r['detected']]
        _, ppm_mean, ppm_s0, _ = _stats(ppm)
        unit, device, rate, tone, setting = key
        print(f"| {unit} | {device} | {rate / 1e6:g}M | "
              f"{tone / 1e6:.3f} | {setting} | {len(on)} | {len(off)} | "
              f"{slope(cells[key]):.3f} | {ppm_mean:+.3f} {ppm_s0:.3f} | "
              f"{sum(1 for r in items if r['dropped'])}/{len(items)} | "
              f"{sum(r['dropped'] for r in items)} | "
              f"{sum(1 for r in off if r['detected'])}/{len(off)} | "
              f"{_mean([r['noise_dbfs_hz'] for r in off]):.2f} |")

    unit_rates = sorted({(k[0], k[2]) for k in groups})
    devices = {(k[0], k[2]): k[1] for k in groups}
    refs = [ur for ur in unit_rates if devices[ur] == args.ref_device]
    others = [ur for ur in unit_rates if devices[ur] != args.ref_device]
    if refs:
        _print_compare(f"Against {args.ref_device}",
                       [(ref, ur) for ref in refs for ur in others],
                       groups, cells)
    by_unit = defaultdict(list)
    for unit, rate in unit_rates:
        by_unit[unit].append(rate)
    _print_compare("Rates (each unit against its lowest rate)",
                   [((u, rs[0]), (u, r)) for u, rs in sorted(by_unit.items())
                    for r in rs[1:]], groups, cells)
    by_rate = defaultdict(list)
    for unit, rate in others:
        by_rate[rate].append(unit)
    _print_compare("Units (each unit against the first, same rate)",
                   [((us[0], r), (u, r)) for r, us in sorted(by_rate.items())
                    for u in us[1:]], groups, cells)
    return 0


if __name__ == '__main__':
    sys.exit(main())
