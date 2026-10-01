#!/usr/bin/env python3
"""Run bench_cw_level.py sweep with an N9310A controlled automatically.

This is intentionally a thin orchestration layer around bench_cw_level.py:
the receiver capture, gain handling, PSD metrics, CSV format and report remain
owned by the existing benchmark.  Only the per-level operator prompt is
replaced by readback-verified N9310A SCPI control.
"""

import argparse
import importlib.util
from pathlib import Path
import sys
import time

from thriftyx.instruments.n9310a import N9310A


def _load_bench_module():
    path = Path(__file__).with_name('bench_cw_level.py')
    spec = importlib.util.spec_from_file_location('thriftyx_bench_cw_level', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _generator_parser():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument('--generator-resource', default='auto',
                   help="N9310A VISA resource or 'auto' [auto]")
    p.add_argument('--generator-timeout-ms', type=int, default=5000)
    p.add_argument('--generator-settle', type=float, default=0.25,
                   help='seconds to wait after verified N9310A level change')
    return p


def _append_note(existing, note):
    return '; '.join(x for x in (existing, note) if x)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    gen, remaining = _generator_parser().parse_known_args(argv)
    bench = _load_bench_module()
    args = bench.build_parser().parse_args(['sweep'] + remaining)
    bench._defaults(args)

    levels = bench.parse_levels(args.levels)
    settings = bench._settings(args)
    bench.check_input_levels(levels, args)
    if gen.generator_settle < 0:
        raise SystemExit('--generator-settle must be >= 0')

    print(f"{args.unit}: {len(levels)} levels x {len(settings)} settings, "
          f"{args.seconds:g} s each -> {args.out}")
    print(f"N9310A: resource={gen.generator_resource}, tone={args.tone:g} Hz")

    receiver = None
    with N9310A(gen.generator_resource, gen.generator_timeout_ms) as sg:
        print(f"N9310A IDN: {sg.idn}")
        args.notes = _append_note(
            args.notes, f'N9310A {sg.idn}; VISA {sg.resource}')
        sg.set_rf_output(False)
        try:
            receiver = bench.open_receiver(args)
            for i, tx in enumerate(levels, 1):
                state = sg.prepare_level(args.tone, tx)
                if gen.generator_settle:
                    time.sleep(gen.generator_settle)
                level_text = 'RF OFF' if tx is None else f'{tx:g} dBm, RF ON'
                print(
                    f"\n[{i}/{len(levels)}] N9310A {level_text}; "
                    f"readback {state.frequency_hz / 1e6:.6f} MHz, "
                    f"{state.power_dbm:.1f} dBm, "
                    f"RF {'ON' if state.rf_on else 'OFF'}",
                    flush=True,
                )
                for setting in settings:
                    row = bench.measure_one(receiver, args, setting, tx)
                    bench._print_row(row)
                    bench.append_row(args.out, row)
                    time.sleep(0.1)
        finally:
            try:
                sg.set_rf_output(False)
                print('\nN9310A RF OFF (safe state).', flush=True)
            finally:
                if receiver is not None:
                    receiver.close()

    print(f"Done: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
