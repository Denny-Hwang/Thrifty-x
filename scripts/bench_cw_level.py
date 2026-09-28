#!/usr/bin/env python3
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""
Bench-measure the level of a CW tone on an RTL-SDR and an Airspy.

For the RTL-SDR vs Airspy gain comparison (docs/bench_rtl_vs_r2_cw.md):
a signal generator feeds an unmodulated tone, optionally through an
external amplifier, into one receiver at a time; each capture is
reduced to rate-independent figures and appended to a CSV.

Per capture (Welch PSD, Hann window, ~150 Hz resolution at every rate):
  carrier_dbfs     tone power, dB relative to ADC full scale (a
                   full-scale tone, |z| = 1 after thriftyx's
                   raw_to_complex scaling, is 0 dBFS on both devices)
  noise_dbfs_hz    noise density next to the tone (median PSD over the
                   noise band, corrected to the mean)
  cn0_dbhz         carrier_dbfs - noise_dbfs_hz: the figure to compare
                   across devices and sample rates
  near_fs_frac     fraction of samples with |I| or |Q| >= 0.98 of full
                   scale (clipping)
The Airspy's R820T2 registers are read and stored with each row; the RTL
gain is set in *manual* mode (rtl_sdr -g 0 would be AGC), like fastcard.

Subcommands:
  measure   one capture -> one CSV row
  sweep     interactive level sweep: prompts for each generator level
            (set by hand on the front panel), then measures every gain
            setting at it
  report    tables (and optionally a plot) from the CSV, with each
            setting's difference to a reference setting

Examples:
  bench_cw_level.py sweep --unit RTL --device rtlsdr --rtl-gains 0 \\
      --levels off,-110:-60:5 --amp-gain 20 --out bench/results.csv
  bench_cw_level.py sweep --unit R2-A --device airspy_r2 \\
      --airspy-serial 0x637862DC2E602DD7 \\
      --stages 0/0/0,0/0/8,0/0/10,0/0/11 \\
      --levels off,-110:-60:5 --amp-gain 20 --out bench/results.csv
  bench_cw_level.py report bench/results.csv --ref RTL:g0 --plot bench/cw.png
"""

import argparse
import csv
import datetime
import math
import os
import subprocess
import sys
import time

import numpy as np
from scipy.special import gammaincinv

from thriftyx.block_data import raw_to_complex

TARGET_RBW_HZ = 150.0
# Noise band: offsets from the tuned centre (both sides), clear of the
# RTL-SDR's DC spike, the tone near centre and its IQ image.
NOISE_BAND_HZ = (50e3, 300e3)
# Tone power is integrated over the peak bin +/- this many bins.
TONE_HALF_WIDTH_BINS = 4
TONE_SEARCH_HZ = 3e3
NEAR_FULL_SCALE = 0.98
# Refuse sweep steps that would put more than this into the receiver.
MAX_SDR_INPUT_DBM = -20.0

CSV_FIELDS = (
    'time', 'unit', 'device', 'serial', 'setting', 'rtl_gain_db', 'lna',
    'mixer', 'vga', 'rate', 'center_hz', 'tone_hz', 'tx_dbm',
    'amp_gain_db', 'loss_db', 'sdr_input_dbm', 'seconds', 'segments',
    'dropped', 'carrier_dbfs', 'noise_dbfs_hz', 'cn0_dbhz', 'detected',
    'tone_offset_hz', 'freq_error_ppm', 'rms_dbfs', 'near_fs_frac',
    'registers', 'notes')


# --------------------------------------------------------------------
# Spectrum accumulation and figures (pure, testable)
# --------------------------------------------------------------------

def fft_size(rate, rbw=TARGET_RBW_HZ):
    """Power-of-two FFT length giving about *rbw* Hz bins at *rate*."""
    return 1 << max(8, int(math.ceil(math.log2(rate / rbw))))


class Accumulator:
    """Welch PSD (non-overlapping Hann segments) and sample statistics."""

    def __init__(self, nfft):
        self.nfft = nfft
        self.window = np.hanning(nfft).astype(np.float32)
        self.power = np.zeros(nfft)
        self.segments = 0
        self.samples = 0
        self.energy = 0.0
        self.near_fs = 0

    def add(self, z):
        """Add complex samples (a multiple of nfft; the rest is ignored)."""
        n = (len(z) // self.nfft) * self.nfft
        if n == 0:
            return
        segs = np.asarray(z[:n]).reshape(-1, self.nfft)
        spec = np.fft.fft(segs * self.window, axis=1)
        self.power += np.sum(spec.real ** 2 + spec.imag ** 2, axis=0)
        self.segments += segs.shape[0]
        flat = segs.ravel()
        self.samples += n
        self.energy += float(np.sum(flat.real.astype(np.float64) ** 2
                                    + flat.imag.astype(np.float64) ** 2))
        self.near_fs += int(np.count_nonzero(
            (np.abs(flat.real) >= NEAR_FULL_SCALE)
            | (np.abs(flat.imag) >= NEAR_FULL_SCALE)))


def figures(acc, rate, tone_offset_hz, center_hz=None,
            noise_band=NOISE_BAND_HZ, half_width=TONE_HALF_WIDTH_BINS,
            search_hz=TONE_SEARCH_HZ):
    """Reduce an :class:`Accumulator` to the CSV figures.

    PSD units are full-scale power per Hz, so a tone of amplitude A
    integrates to A**2 whatever the rate or FFT length.
    """
    if acc.segments == 0:
        raise ValueError("no complete FFT segment was captured")
    nfft, k = acc.nfft, acc.segments
    df = rate / nfft
    psd = np.fft.fftshift(acc.power / k) / (rate * float(np.sum(
        acc.window.astype(np.float64) ** 2)))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))

    band = (np.abs(freqs) >= noise_band[0]) & (np.abs(freqs) <= noise_band[1])
    if not band.any():
        raise ValueError("noise band lies outside the captured spectrum")
    # A bin averaged over k segments is Gamma(k, mean/k): scale the
    # (spur-robust) median to the mean.
    n0 = float(np.median(psd[band])) * k / gammaincinv(k, 0.5)

    search = np.abs(freqs - tone_offset_hz) <= search_hz
    idx = np.flatnonzero(search)
    peak = int(idx[np.argmax(psd[idx])])
    lo, hi = max(peak - half_width, 0), min(peak + half_width + 1, nfft)
    bins = hi - lo
    excess = float(np.sum(psd[lo:hi]) * df - n0 * bins * df)
    sigma = n0 * df * math.sqrt(bins / k)
    detected = excess > 5 * sigma

    # Parabolic peak interpolation on the log spectrum.
    offset = float(freqs[peak])
    if 0 < peak < nfft - 1 and detected:
        a, b, c = np.log(psd[peak - 1:peak + 2])
        denom = a - 2 * b + c
        if denom < 0:
            offset += 0.5 * (a - c) / denom * df

    carrier = 10 * math.log10(excess) if detected else float('nan')
    noise = 10 * math.log10(n0)
    return {
        'segments': k,
        'carrier_dbfs': carrier,
        'noise_dbfs_hz': noise,
        'cn0_dbhz': carrier - noise if detected else float('nan'),
        'detected': int(detected),
        'tone_offset_hz': offset,
        'freq_error_ppm': ((offset - tone_offset_hz) / center_hz * 1e6
                           if center_hz and detected else float('nan')),
        'rms_dbfs': 10 * math.log10(max(acc.energy / acc.samples, 1e-30)),
        'near_fs_frac': acc.near_fs / acc.samples,
    }


# --------------------------------------------------------------------
# Receivers
# --------------------------------------------------------------------

class RtlSdr:
    """RTL-SDR through ``rtl_sdr`` (osmocom tools), manual gain."""

    def __init__(self, args):
        self.args = args
        self.serial = f"index {args.device_index}"

    @staticmethod
    def gain_argument(gain_db):
        """``rtl_sdr -g`` value for a *manual* gain of *gain_db*.

        rtl_sdr treats a requested 0 as AGC, but snaps any other value
        to the nearest supported gain in manual mode: 0.1 dB therefore
        gives manual 0 dB (LNA 0 / Mixer 0 / VGA 8), fastcard's gain 0.
        """
        return f"{max(float(gain_db), 0.1):g}"

    def capture(self, setting, acc, seconds, settle):
        args = self.args
        rate = int(args.rate)
        total = int((settle + seconds) * rate)
        call = [args.rtl_sdr, '-f', str(int(args.freq)), '-s', str(rate),
                '-g', self.gain_argument(setting['rtl_gain_db']),
                '-d', str(args.device_index), '-n', str(total), '-']
        proc = subprocess.Popen(call, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        skip = int(settle * rate) * 2
        chunk = acc.nfft * 2 * 16
        pending = b''
        try:
            while True:
                data = proc.stdout.read(chunk)
                if not data:
                    break
                if skip:
                    cut = min(skip, len(data))
                    data, skip = data[cut:], skip - cut
                pending += data
                usable = (len(pending) // (acc.nfft * 2)) * acc.nfft * 2
                if usable:
                    raw = np.frombuffer(pending[:usable], dtype=np.uint8)
                    acc.add(raw_to_complex(raw, bit_depth=8))
                    pending = pending[usable:]
        finally:
            _, err = proc.communicate()
        err = err.decode(errors='replace')
        if proc.returncode != 0:
            raise RuntimeError(f"rtl_sdr failed ({proc.returncode}):\n{err}")
        notes = []
        for line in err.splitlines():
            if line.startswith('Tuner gain set to'):
                notes.append(line.strip())
            if 'auto' in line.lower() and 'gain' in line.lower():
                raise RuntimeError(f"rtl_sdr ran with AGC: {line}")
        if not notes:
            raise RuntimeError("rtl_sdr did not report a manual gain:\n"
                               + err)
        return {'dropped': '', 'registers': '', 'notes': '; '.join(notes)}

    def close(self):
        pass


class Airspy:
    """Airspy through the thriftyx HAL (the capture path), manual gain."""

    def __init__(self, args):
        from thriftyx.hal.device_factory import create_device
        kwargs = {}
        if args.airspy_serial:
            kwargs['serial'] = args.airspy_serial
        elif args.device_index:
            kwargs['device_index'] = args.device_index
        self.device = create_device(args.device, **kwargs)
        self.device.open()
        self.device.set_sample_rate(int(args.rate))
        if args.packing:
            self.device.set_packing(True)
        self.device.set_center_freq(int(args.freq))
        self.device.set_bias_tee(bool(args.bias_tee))
        serial = self.device.get_info().serial
        self.serial = (args.airspy_serial
                       or (f"0x{serial[-16:]}" if serial != 'unknown'
                           else serial))
        self.rate = int(args.rate)

    def capture(self, setting, acc, seconds, settle):
        dev = self.device
        dev.apply_gain_mode('manual', lna=setting['lna'],
                            mixer=setting['mixer'], vga=setting['vga'],
                            lna_agc=False, mixer_agc=False)
        dev.discard_buffered()
        dev.read_sync(int(settle * self.rate))       # settle, discard
        registers = ''
        try:
            regs = dev.read_tuner_registers()
            registers = ' '.join(f"0x{r:02X}=0x{v:02X}"
                                 for r, v in sorted(regs.items()))
        except Exception as exc:
            registers = f'unavailable: {exc}'
        dropped = 0
        remaining = int(seconds * self.rate)
        seg = acc.nfft * 8
        while remaining > 0:
            n = min(seg, remaining)
            before = dev.dropped_samples
            raw = dev.read_sync(n)
            remaining -= n
            lost = dev.dropped_samples - before
            if lost:
                # Zero-filled gaps would bias the power low: drop them.
                dropped += lost
                continue
            acc.add(raw_to_complex(raw, bit_depth=12))
        return {'dropped': dropped, 'registers': registers, 'notes': ''}

    def close(self):
        try:
            self.device.close()
        except Exception:
            pass


def open_receiver(args):
    if args.device == 'rtlsdr':
        return RtlSdr(args)
    return Airspy(args)


# --------------------------------------------------------------------
# Measurement bookkeeping
# --------------------------------------------------------------------

def parse_stages(text):
    """``'0/0/8,0/0/10'`` -> list of settings dicts."""
    settings = []
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue
        parts = item.split('/')
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(
                f"stage setting {item!r} is not LNA/MIXER/VGA")
        lna, mixer, vga = (int(p) for p in parts)
        if not (0 <= lna <= 14 and 0 <= mixer <= 15 and 0 <= vga <= 15):
            raise argparse.ArgumentTypeError(
                f"stage setting {item!r} out of range (14/15/15)")
        settings.append({'setting': f'{lna}/{mixer}/{vga}', 'lna': lna,
                         'mixer': mixer, 'vga': vga, 'rtl_gain_db': ''})
    return settings


def parse_rtl_gains(text):
    return [{'setting': f'g{float(g):g}', 'rtl_gain_db': float(g),
             'lna': '', 'mixer': '', 'vga': ''}
            for g in text.split(',') if g.strip()]


def parse_levels(text):
    """``'off,-110:-60:5,-55'`` -> [None, -110.0, ..., -60.0, -55.0]."""
    levels = []
    for item in text.split(','):
        item = item.strip().lower()
        if not item:
            continue
        if item == 'off':
            levels.append(None)
        elif ':' in item:
            start, stop, step = (float(v) for v in item.split(':'))
            if step == 0 or (stop - start) * step < 0:
                raise argparse.ArgumentTypeError(f"bad range {item!r}")
            count = int(round((stop - start) / step)) + 1
            levels.extend(round(start + i * step, 3) for i in range(count))
        else:
            levels.append(float(item))
    return levels


def sdr_input_dbm(tx_dbm, amp_gain, loss):
    return None if tx_dbm is None else tx_dbm + amp_gain - loss


def measure_one(receiver, args, setting, tx_dbm):
    rate = int(args.rate)
    acc = Accumulator(fft_size(rate))
    extra = receiver.capture(setting, acc, args.seconds, args.settle)
    tone_offset = args.tone - args.freq
    row = figures(acc, rate, tone_offset, center_hz=args.freq)
    row.update(extra)
    row.update({
        'time': datetime.datetime.now().isoformat(timespec='seconds'),
        'unit': args.unit, 'device': args.device,
        'serial': receiver.serial, 'rate': rate,
        'center_hz': int(args.freq), 'tone_hz': int(args.tone),
        'tx_dbm': '' if tx_dbm is None else tx_dbm,
        'amp_gain_db': args.amp_gain, 'loss_db': args.loss,
        'sdr_input_dbm': ('' if tx_dbm is None else
                          sdr_input_dbm(tx_dbm, args.amp_gain, args.loss)),
        'seconds': args.seconds,
    })
    row.update({k: setting[k] for k in
                ('setting', 'rtl_gain_db', 'lna', 'mixer', 'vga')})
    if args.notes:
        row['notes'] = '; '.join(filter(None, [row.get('notes'),
                                               args.notes]))
    return row


def append_row(path, row):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS,
                                extrasaction='ignore')
        if new:
            writer.writeheader()
        writer.writerow({k: _fmt(row.get(k, '')) for k in CSV_FIELDS})


def _fmt(value):
    if isinstance(value, float):
        return '' if math.isnan(value) else f'{value:.3f}'
    return value


def _print_row(row):
    cn0 = row['cn0_dbhz']
    print(f"  {row['setting']:>8}  carrier {row['carrier_dbfs']:7.2f} dBFS"
          f"  noise {row['noise_dbfs_hz']:7.2f} dBFS/Hz"
          f"  C/N0 {cn0:6.2f} dB-Hz  clip {row['near_fs_frac']:.1e}"
          + (f"  dropped {row['dropped']}" if row.get('dropped') else ''),
          flush=True)
    if row['near_fs_frac'] > 1e-4:
        print("  WARNING: samples near full scale -- the receiver is "
              "clipping at this level", flush=True)


def _settings(args):
    if args.device == 'rtlsdr':
        return parse_rtl_gains(args.rtl_gains)
    return parse_stages(args.stages)


def cmd_measure(args):
    receiver = open_receiver(args)
    try:
        for setting in _settings(args):
            row = measure_one(receiver, args, setting, args.tx_dbm)
            _print_row(row)
            append_row(args.out, row)
    finally:
        receiver.close()
    return 0


def cmd_sweep(args):
    levels = parse_levels(args.levels)
    settings = _settings(args)
    for tx in levels:
        pin = sdr_input_dbm(tx, args.amp_gain, args.loss)
        if pin is not None and pin > MAX_SDR_INPUT_DBM and not args.force:
            raise SystemExit(
                f"TX {tx} dBm puts ~{pin:.1f} dBm into the receiver (limit "
                f"{MAX_SDR_INPUT_DBM} dBm); lower the levels or --force")
    print(f"{args.unit}: {len(levels)} levels x {len(settings)} settings, "
          f"{args.seconds:g} s each -> {args.out}")
    receiver = open_receiver(args)
    try:
        for i, tx in enumerate(levels, 1):
            if tx is None:
                prompt = "N9310A: RF OFF (keep the cable connected)"
            else:
                prompt = (f"N9310A: AMPTD {tx:g} dBm, RF ON (receiver "
                          f"input ~{sdr_input_dbm(tx, args.amp_gain, args.loss):g}"
                          " dBm)")
            if not args.no_prompt:
                answer = input(f"\n[{i}/{len(levels)}] {prompt}\n"
                               "  Enter = measure, s = skip, q = quit: ")
                if answer.strip().lower() == 'q':
                    break
                if answer.strip().lower() == 's':
                    continue
            else:
                print(f"\n[{i}/{len(levels)}] {prompt}")
            for setting in settings:
                row = measure_one(receiver, args, setting, tx)
                _print_row(row)
                append_row(args.out, row)
                time.sleep(0.1)
    finally:
        receiver.close()
    print(f"\nDone: {args.out}")
    return 0


# --------------------------------------------------------------------
# Report
# --------------------------------------------------------------------

def load_rows(path):
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ('tx_dbm', 'carrier_dbfs', 'noise_dbfs_hz', 'cn0_dbhz',
                    'near_fs_frac', 'sdr_input_dbm'):
            row[key] = float(row[key]) if row.get(key) else float('nan')
        row['detected'] = row.get('detected') == '1'
    return rows


def summarize(rows):
    """{(unit, setting): {tx or None: averaged figures}}."""
    groups = {}
    for row in rows:
        key = (row['unit'], row['setting'])
        tx = row['tx_dbm']
        if tx is None or math.isnan(tx):
            tx = None
        groups.setdefault(key, {}).setdefault(tx, []).append(row)
    table = {}
    for key, by_tx in groups.items():
        table[key] = {}
        for tx, items in by_tx.items():
            det = [r for r in items if r['detected']]
            table[key][tx] = {
                'n': len(items),
                'carrier_dbfs': _mean_db([r['carrier_dbfs'] for r in det]),
                'cn0_dbhz': _mean_db([r['cn0_dbhz'] for r in det]),
                'noise_dbfs_hz': _mean_db([r['noise_dbfs_hz']
                                           for r in items]),
                'near_fs_frac': max(r['near_fs_frac'] for r in items),
            }
    return table


def _mean_db(values):
    values = [v for v in values if not math.isnan(v)]
    return float(np.mean(values)) if values else float('nan')


def _clean(cell, max_clip):
    return (not math.isnan(cell['cn0_dbhz'])
            and cell['near_fs_frac'] <= max_clip)


def deltas(table, ref, max_clip=1e-5, band=None):
    """Mean C/N0 and carrier differences of each setting to *ref*.

    Only levels where both are detected and unclipped (and inside
    *band*, a (low, high) TX range) count.
    """
    out = {}
    ref_cells = table[ref]
    for key, cells in table.items():
        d_cn0, d_car, used = [], [], []
        for tx, cell in cells.items():
            if tx is None or tx not in ref_cells:
                continue
            if band and not band[0] <= tx <= band[1]:
                continue
            rc = ref_cells[tx]
            if _clean(cell, max_clip) and _clean(rc, max_clip):
                d_cn0.append(cell['cn0_dbhz'] - rc['cn0_dbhz'])
                d_car.append(cell['carrier_dbfs'] - rc['carrier_dbfs'])
                used.append(tx)
        out[key] = {
            'd_cn0': float(np.mean(d_cn0)) if d_cn0 else float('nan'),
            'd_cn0_std': float(np.std(d_cn0)) if d_cn0 else float('nan'),
            'd_carrier': float(np.mean(d_car)) if d_car else float('nan'),
            'levels': used,
        }
    return out


def slope(cells, max_clip=1e-5):
    """Fitted d(carrier_dbfs)/d(tx) over the clean levels (1.0 = linear)."""
    pts = [(tx, c['carrier_dbfs']) for tx, c in cells.items()
           if tx is not None and _clean(c, max_clip)]
    if len(pts) < 3:
        return float('nan')
    x, y = np.array(pts).T
    return float(np.polyfit(x, y, 1)[0])


def cmd_report(args):
    rows = load_rows(args.csv)
    if not rows:
        raise SystemExit("no rows")
    table = summarize(rows)
    keys = sorted(table)
    ref_unit, _, ref_setting = args.ref.partition(':')
    ref = (ref_unit, ref_setting)
    if ref not in table:
        raise SystemExit(f"reference {args.ref} not in the CSV; have "
                         + ', '.join(f'{u}:{s}' for u, s in keys))
    band = tuple(float(v) for v in args.band.split(':')) if args.band else None
    levels = sorted({tx for cells in table.values() for tx in cells
                     if tx is not None})

    def line(cells_fmt):
        return '| ' + ' | '.join(cells_fmt) + ' |'

    for field, title in (('cn0_dbhz', 'C/N0 (dB-Hz)'),
                         ('carrier_dbfs', 'Carrier (dBFS)')):
        print(f"\n### {title}\n")
        print(line(['unit:setting'] + [f'{tx:g}' for tx in levels]))
        print(line(['---'] * (len(levels) + 1)))
        for key in keys:
            cells = table[key]
            vals = []
            for tx in levels:
                cell = cells.get(tx)
                if cell is None or math.isnan(cell[field]):
                    vals.append('—')
                else:
                    mark = '*' if cell['near_fs_frac'] > args.max_clip else ''
                    vals.append(f"{cell[field]:.1f}{mark}")
            print(line([f'{key[0]}:{key[1]}'] + vals))
    print("\n\\* samples near full scale (clipping) — excluded from deltas.")

    d = deltas(table, ref, args.max_clip, band)
    print(f"\n### Against {args.ref}" + (f" (TX {args.band} dBm)"
                                          if band else '') + "\n")
    print(line(['unit:setting', 'ΔC/N0 (dB)', 'σ', 'Δcarrier (dB)',
                'noise, RF off (dBFS/Hz)', 'slope', 'levels']))
    print(line(['---'] * 7))
    for key in keys:
        r = d[key]
        off = table[key].get(None, {}).get('noise_dbfs_hz', float('nan'))
        print(line([f'{key[0]}:{key[1]}', f"{r['d_cn0']:+.2f}",
                    f"{r['d_cn0_std']:.2f}", f"{r['d_carrier']:+.2f}",
                    f"{off:.1f}", f"{slope(table[key], args.max_clip):.3f}",
                    str(len(r['levels']))]))
    if args.plot:
        _plot(table, keys, levels, args.plot, args.max_clip)
    return 0


def _plot(table, keys, levels, path, max_clip):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for key in keys:
        cells = table[key]
        xs = [tx for tx in levels if tx in cells]
        for ax, field in zip(axes, ('cn0_dbhz', 'carrier_dbfs'), strict=True):
            ax.plot(xs, [cells[tx][field] for tx in xs], marker='o',
                    label=f'{key[0]} {key[1]}')
    axes[0].set_ylabel('C/N0 (dB-Hz)')
    axes[1].set_ylabel('Carrier (dBFS)')
    for ax in axes:
        ax.set_xlabel('Generator level (dBm)')
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f"\nplot: {path}")


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------

def _metric(text):
    text = text.strip()
    scale = {'k': 1e3, 'M': 1e6, 'G': 1e9}.get(text[-1:], 1)
    return float(text[:-1] if scale != 1 else text) * scale


def _add_capture_args(p):
    p.add_argument('--unit', required=True,
                   help="label of this receiver, e.g. RTL, R2-A, R2-B")
    p.add_argument('--device', required=True,
                   choices=('rtlsdr', 'airspy_r2', 'airspy_mini'))
    p.add_argument('-f', '--freq', type=_metric, default=161.3e6,
                   help="tuned centre frequency [161.3M]")
    p.add_argument('--tone', type=_metric, default=None,
                   help="generator frequency [centre + 15k]")
    p.add_argument('-s', '--rate', type=_metric, default=None,
                   help="sample rate [RTL 2.4M, Airspy R2 10M, Mini 6M]")
    p.add_argument('--rtl-gains', default='0',
                   help="RTL-SDR manual gains in dB, comma separated [0]")
    p.add_argument('--stages', default='0/0/0,0/0/8',
                   help="Airspy LNA/Mixer/VGA settings, comma separated")
    p.add_argument('--airspy-serial', default=None)
    p.add_argument('-d', '--device-index', type=int, default=0)
    p.add_argument('--packing', action='store_true',
                   help="Airspy 12-bit USB packing (helps 10 Msps on WSL)")
    p.add_argument('--bias-tee', action='store_true',
                   help="Airspy bias tee on (an amplifier powered through "
                        "the coax); never with a generator on the port "
                        "without a DC block")
    p.add_argument('--rtl-sdr', default='rtl_sdr', help="rtl_sdr binary")
    p.add_argument('--seconds', type=float, default=5.0)
    p.add_argument('--settle', type=float, default=0.5)
    p.add_argument('--amp-gain', type=float, default=0.0,
                   help="external amplifier gain (dB), for the log")
    p.add_argument('--loss', type=float, default=0.0,
                   help="cable/attenuator loss to the receiver (dB)")
    p.add_argument('--notes', default='')
    p.add_argument('--out', default='bench/results.csv')


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('measure', help="one capture per setting")
    _add_capture_args(m)
    m.add_argument('--tx-dbm', type=lambda s: None if s.lower() == 'off'
                   else float(s), default=None,
                   help="generator level for the log ('off' = RF off)")
    s = sub.add_parser('sweep', help="interactive level sweep")
    _add_capture_args(s)
    s.add_argument('--levels', required=True,
                   help="e.g. 'off,-110:-60:5' (dBm at the generator)")
    s.add_argument('--no-prompt', action='store_true',
                   help="do not wait for Enter (generator already set)")
    s.add_argument('--force', action='store_true',
                   help=f"allow receiver input above {MAX_SDR_INPUT_DBM} dBm")
    r = sub.add_parser('report', help="tables / plot from the CSV")
    r.add_argument('csv')
    r.add_argument('--ref', default='RTL:g0',
                   help="reference unit:setting [RTL:g0]")
    r.add_argument('--band', default=None,
                   help="TX range for the deltas, e.g. -100:-70")
    r.add_argument('--max-clip', type=float, default=1e-5)
    r.add_argument('--plot', default=None, help="write a PNG plot here")
    return parser


def _defaults(args):
    if getattr(args, 'rate', 'x') is None:
        args.rate = {'rtlsdr': 2.4e6, 'airspy_r2': 10e6,
                     'airspy_mini': 6e6}[args.device]
    if hasattr(args, 'tone') and args.tone is None:
        args.tone = args.freq + 15e3


def main(argv=None):
    args = build_parser().parse_args(argv)
    _defaults(args)
    return {'measure': cmd_measure, 'sweep': cmd_sweep,
            'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
