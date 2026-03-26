# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Common Thrifty-X CLI interface.

A centralized interface for accessing Thrifty-X modules with CLI interfaces.
"""

import sys
import importlib


HELP = """usage: thriftyx <command> [<args>]

Thrifty-X is proof-of-concept SDR software for TDOA positioning using
Airspy Mini or Airspy R2 SDR hardware.

Thrifty-X is divided into several modules. Each module is accessible as a
command and has its own arguments.

Valid commands are:

    ~ Core functionality ~
    capture           Capture carrier detections from Airspy SDR
    detect            Detect presence of positioning signals and estimate SoA
    identify          Identify transmitter IDs and filter duplicate detections
    match             Match detections from multiple receivers
    tdoa              Estimate TDOA by synchronising with beacon transmissions
    pos               Estimate position from TDOA estimates

    ~ Analysis tools ~
    scope             Live time-domain and frequency-domain plots (matplotlib)
    analyze_toads     Calculate statistics on data in a .toads file
    analyze_detect    Like 'detect', but plot signals for analysis
    analyze_beacon    Analyze the difference in SOA of a beacon between two RXs
    analyze_tdoa      Calculate stats from slices of the TDOA data

    ~ Utilities ~
    template_generate Generate a new (ideal) template
    template_extract  Extract a new template from captured data

Use 'thriftyx help <command>' for information about the command's arguments."""


MODULES = {
    'capture': 'thriftyx.airspy_capture',
    'detect': 'thriftyx.detect',
    'identify': 'thriftyx.identify',
    'match': 'thriftyx.matchmaker',
    'tdoa': 'thriftyx.tdoa_est',
    'pos': 'thriftyx.pos_est',
    'analyze_toads': 'thriftyx.toads_analysis',
    'analyze_detect': 'thriftyx.detect_analysis',
    'analyze_beacon': 'thriftyx.beacon_analysis',
    'analyze_tdoa': 'thriftyx.tdoa_analysis',
    'template_generate': 'thriftyx.template_generate',
    'template_extract': 'thriftyx.template_extract',
    'scope': 'thriftyx.scope',
}


def _print_help():
    print(HELP)


def _main():
    if len(sys.argv) == 1:
        _print_help()
        sys.exit(1)

    command = sys.argv.pop(1)

    if command == 'help' or command == '--help':
        if len(sys.argv) == 2:
            command = sys.argv.pop(1)
            sys.argv.append('--help')
        else:
            _print_help()
            sys.exit(0)

    if command in MODULES:
        # pylint: disable=protected-access
        sys.argv[0] += ' ' + command
        module_name = MODULES[command]
        module = importlib.import_module(module_name)
        module._main()
    else:
        print("thriftyx: {} is not a thriftyx command. See 'thriftyx --help'."
              .format(command), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
