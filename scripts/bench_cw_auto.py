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
    p.add_argument(
        '--stop-airspy-between-levels', action='store_true',
        help='diagnostic only: stop Airspy RX before each N9310A USB '
             'transaction; persistent streaming is preferred at 10 MSPS')
    p.add_argument('--receiver-warmup', type=float, default=0.0,
                   help='seconds of receiver data to run/discard before sweep')
    p.add_argument('--retry-dropped', type=int, default=0,
                   help='retry a measurement this many times when samples drop')
    p.add_argument('--retry-delay', type=float, default=0.5,
                   help='seconds between dropped-sample retry attempts')
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
    if gen.receiver_warmup < 0:
        raise SystemExit('--receiver-warmup must be >= 0')
    if gen.retry_dropped < 0:
        raise SystemExit('--retry-dropped must be >= 0')
    if gen.retry_delay < 0:
        raise SystemExit('--retry-delay must be >= 0')

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
            if gen.receiver_warmup and hasattr(receiver, 'warmup'):
                print(
                    f"Receiver warmup: {gen.receiver_warmup:g} s "
                    "(discarded, RF OFF)",
                    flush=True,
                )
                receiver.warmup(settings[0], gen.receiver_warmup)
            for i, tx in enumerate(levels, 1):
                if (gen.stop_airspy_between_levels
                        and hasattr(receiver, 'stop_stream')):
                    receiver.stop_stream()
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
                    attempt = 0
                    while True:
                        row = bench.measure_one(receiver, args, setting, tx)
                        dropped = row.get('dropped')
                        try:
                            dropped_count = (int(float(dropped))
                                             if dropped not in ('', None)
                                             else 0)
                        except (TypeError, ValueError):
                            dropped_count = 0
                        if dropped_count:
                            row['notes'] = _append_note(
                                row.get('notes', ''),
                                f'dropped_retry_attempt={attempt + 1}')
                        bench._print_row(row)
                        bench.append_row(args.out, row)
                        if dropped_count and attempt < gen.retry_dropped:
                            attempt += 1
                            print(
                                f"  RETRY: dropped {dropped_count} samples; "
                                f"attempt {attempt}/{gen.retry_dropped}",
                                flush=True,
                            )
                            if gen.retry_delay:
                                time.sleep(gen.retry_delay)
                            continue
                        break
                    time.sleep(0.1)
        except BaseException:
            # Preserve the original failure. After a timed-out VISA query,
            # another verified query in cleanup can trigger SCPI -410 and
            # obscure the root cause. Write RF OFF only; the outer runner
            # performs a fresh-session OFF again on exit.
            if hasattr(sg, 'best_effort_rf_off'):
                sg.best_effort_rf_off()
            else:
                try:
                    sg.set_rf_output(False, verify=False)
                except Exception:
                    pass
            raise
        else:
            if (gen.stop_airspy_between_levels and receiver is not None
                    and hasattr(receiver, 'stop_stream')):
                receiver.stop_stream()
            sg.set_rf_output(False)
            print('\nN9310A RF OFF (safe state).', flush=True)
        finally:
            if receiver is not None:
                receiver.close()

    print(f"Done: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
