# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""The `>>>` examples in thriftyx docstrings run and hold.

testpaths is tests/, so pytest never collected them (carrier_detect,
setting_parsers and detect_analysis had 8 docstrings with examples that
no run checked).  Every module whose source holds an example is found
here and its examples run through doctest.
"""

import doctest
import importlib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / 'thriftyx'


def _modules_with_examples():
    for path in sorted(PACKAGE.rglob('*.py')):
        if '>>>' not in path.read_text(encoding='utf-8'):
            continue
        parts = path.relative_to(PACKAGE.parent).with_suffix('').parts
        if parts[-1] == '__init__':
            parts = parts[:-1]
        yield '.'.join(parts)


MODULES = list(_modules_with_examples())


def test_known_examples_are_found():
    assert {'thriftyx.carrier_detect', 'thriftyx.setting_parsers',
            'thriftyx.detect_analysis'} <= set(MODULES)


@pytest.mark.parametrize('name', MODULES)
def test_docstring_examples(name, monkeypatch):
    # detect_analysis needs matplotlib (the `analysis` extra), never a
    # display: it imports the Agg canvas, and pyplot only when plotting.
    monkeypatch.setenv('MPLBACKEND', 'Agg')
    if name == 'thriftyx.detect_analysis':
        pytest.importorskip('matplotlib')
    module = importlib.import_module(name)
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS)
    report = []
    for test in doctest.DocTestFinder().find(module):
        runner.run(test, out=report.append)
    assert runner.tries > 0, "no examples found in {}".format(name)
    assert runner.failures == 0, ''.join(report)
