# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty).
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Smoke tests for thriftyx.cli — dispatcher correctness only.

These tests pin the contract that:
  * Every command advertised in the README is present in MODULES.
  * Every entry in MODULES resolves to an importable module that
    exposes the `_main` entry point expected by the dispatcher.
  * The dispatcher prints help when invoked without arguments.
  * Unknown commands exit non-zero with a message on stderr.

They do NOT exercise any subcommand's argparse — that belongs to each
command's own test module.
"""

import importlib
import sys

import pytest

from thriftyx import cli


# The full set of commands the README and CLI HELP banner promise.
EXPECTED_COMMANDS = frozenset({
    'capture', 'detect', 'identify', 'match', 'tdoa', 'pos',
    'scope', 'analyze_toads', 'analyze_detect', 'analyze_beacon',
    'analyze_tdoa',
    'template_generate', 'template_extract', 'gold',
})


def test_modules_table_matches_documented_commands():
    """MODULES has every documented command, nothing extra."""
    assert set(cli.MODULES) == EXPECTED_COMMANDS


def test_help_banner_lists_every_command():
    """HELP banner mentions every command name in MODULES."""
    for cmd in cli.MODULES:
        assert cmd in cli.HELP, (
            f"Command '{cmd}' is in MODULES but missing from HELP banner.")


# Commands whose modules pull in optional extras (matplotlib, PyQt, …).
# When the corresponding extra is missing in the runtime environment,
# the import-vs-_main check is skipped rather than failed — CI is
# expected to install the relevant extra to keep the coverage live.
_COMMANDS_NEEDING_MATPLOTLIB = frozenset({
    'analyze_beacon', 'analyze_detect', 'analyze_tdoa',
    'analyze_toads', 'scope',
})


@pytest.mark.parametrize('command', sorted(EXPECTED_COMMANDS))
def test_module_for_command_is_importable_and_has_main(command):
    """Each MODULES target imports and exposes a callable _main()."""
    if command in _COMMANDS_NEEDING_MATPLOTLIB:
        pytest.importorskip(
            'matplotlib',
            reason=f"'{command}' needs the [analysis] or [gui] extra")
    module = importlib.import_module(cli.MODULES[command])
    assert hasattr(module, '_main'), (
        f"Module {cli.MODULES[command]} backs '{command}' but has no _main().")
    assert callable(getattr(module, '_main'))


def test_no_args_prints_help_and_exits_nonzero(capsys, monkeypatch):
    """Bare `thriftyx` shows help and exits 1."""
    monkeypatch.setattr(sys, 'argv', ['thriftyx'])
    with pytest.raises(SystemExit) as excinfo:
        cli._main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert 'usage' in captured.out.lower()


def test_unknown_command_exits_nonzero_with_stderr(capsys, monkeypatch):
    """Unknown subcommand goes to stderr and exits 1."""
    monkeypatch.setattr(sys, 'argv', ['thriftyx', 'no_such_command'])
    with pytest.raises(SystemExit) as excinfo:
        cli._main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert 'no_such_command' in captured.err


def test_help_subcommand_without_target_prints_help(capsys, monkeypatch):
    """`thriftyx help` alone prints the banner and exits 0."""
    monkeypatch.setattr(sys, 'argv', ['thriftyx', 'help'])
    with pytest.raises(SystemExit) as excinfo:
        cli._main()
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert 'thriftyx' in captured.out.lower()
