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

import importlib
import os
import sys

from thriftyx.exceptions import EXIT_CONFIG, ConfigError, ThriftyXError


HELP = """usage: thriftyx <command> [<args>]

Thrifty-X is proof-of-concept SDR software for TDOA positioning using
RTL-SDR, Airspy Mini, or Airspy R2 SDR hardware.

Thrifty-X is divided into several modules. Each module is accessible as a
command and has its own arguments.

Valid commands are:

    ~ Core functionality ~
    capture           Capture positioning signals from SDR (RTL-SDR/Airspy)
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
    gold              Print or analyze a Gold code sequence

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
    'gold': 'thriftyx.gold',
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
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            print(
                "thriftyx: failed to load command '{}': {}\n"
                "Hint: ensure optional dependencies are installed and that "
                "your environment is activated.\n"
                "Try: pip install -e \".[all]\"".format(command, exc),
                file=sys.stderr
            )
            sys.exit(2)
        try:
            module._main()
        except KeyboardInterrupt:
            print("thriftyx {}: interrupted".format(command),
                  file=sys.stderr)
            sys.exit(130)
        except FileNotFoundError as exc:
            # Missing template.npy, .card input, config file, ... — a
            # routine operator error; no traceback needed.
            print("thriftyx {}: file not found: {}".format(
                command, exc.filename or exc), file=sys.stderr)
            sys.exit(1)
        except BrokenPipeError:
            # `thriftyx detect ... | head`: the reader left early.  Point
            # stdout at /dev/null so the interpreter's final flush does
            # not raise again.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            sys.exit(1)
        except OSError as exc:
            # A directory where a file was expected, no permission, ...
            detail = exc.strerror or str(exc)
            if exc.filename:
                detail = "{}: {}".format(exc.filename, detail)
            print("thriftyx {}: {}".format(command, detail), file=sys.stderr)
            sys.exit(1)
        except ConfigError as exc:
            # Retrying cannot fix a bad configuration; a distinct status
            # lets systemd stop restarting (RestartPreventExitStatus=).
            print("thriftyx {}: {}".format(command, exc), file=sys.stderr)
            sys.exit(EXIT_CONFIG)
        except ThriftyXError as exc:
            # Typed project errors (config syntax, device errors, ...)
            # carry a user-facing message already.
            print("thriftyx {}: {}".format(command, exc), file=sys.stderr)
            sys.exit(1)
    else:
        print("thriftyx: {} is not a thriftyx command. See 'thriftyx --help'."
              .format(command), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
