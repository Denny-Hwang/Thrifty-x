#!/usr/bin/env python3
"""Discover, inspect, and control an Agilent/Keysight N9310A over USB."""

import argparse
import json
import sys

from thriftyx.instruments.n9310a import N9310A, discover_n9310a


def _metric(text):
    text = text.strip()
    scale = {'k': 1e3, 'K': 1e3, 'm': 1e6, 'M': 1e6,
             'g': 1e9, 'G': 1e9}.get(text[-1:], 1.0)
    return float(text[:-1] if scale != 1.0 else text) * scale


def _print_status(status, as_json=False):
    data = {
        'resource': status.resource,
        'idn': status.idn,
        'frequency_hz': status.frequency_hz,
        'frequency_mhz': status.frequency_hz / 1e6,
        'power_dbm': status.power_dbm,
        'rf_output': 'ON' if status.rf_on else 'OFF',
    }
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Resource   : {data['resource']}")
        print(f"IDN        : {data['idn']}")
        print(f"Frequency  : {data['frequency_mhz']:.6f} MHz")
        print(f"Power      : {data['power_dbm']:.1f} dBm")
        print(f"RF Output  : {data['rf_output']}")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resource', default='auto',
                   help="VISA resource or 'auto' [auto]")
    p.add_argument('--timeout-ms', type=int, default=5000)
    p.add_argument('--json', action='store_true')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('discover')
    sub.add_parser('status')
    s = sub.add_parser('set', help='set CW frequency/power with readback')
    s.add_argument('--frequency', type=_metric, required=True,
                   help='e.g. 161.315M or 161315000')
    s.add_argument('--power', type=float, required=True, help='dBm')
    s.add_argument('--rf-on', action='store_true',
                   help='enable RF after successful configuration')
    sub.add_parser('on')
    sub.add_parser('off')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'discover':
        print(discover_n9310a() if args.resource == 'auto' else args.resource)
        return 0

    with N9310A(args.resource, args.timeout_ms) as sg:
        if args.command == 'status':
            status = sg.status()
        elif args.command == 'set':
            status = sg.configure_cw(args.frequency, args.power,
                                     rf_on=args.rf_on)
        elif args.command == 'on':
            sg.set_rf_output(True)
            status = sg.status()
        elif args.command == 'off':
            sg.set_rf_output(False)
            status = sg.status()
        else:  # pragma: no cover
            raise AssertionError(args.command)
        _print_status(status, args.json)
        print(f"SCPI error : {sg.system_error()}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
