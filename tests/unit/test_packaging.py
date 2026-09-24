# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Packaging metadata and what the sdist carries."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_license_is_an_spdx_expression():
    """Regression: `license = {text = ...}` is deprecated by setuptools
    (builds stop being supported after 2027-02-18); the PEP 639 SPDX
    string and license-files need setuptools >= 77."""
    tomllib = pytest.importorskip('tomllib')
    config = tomllib.loads((REPO / 'pyproject.toml').read_text())
    project = config['project']
    assert project['license'] == 'GPL-3.0-only'
    assert project['license-files'] == ['LICENSE.txt', 'NOTICE']
    for name in project['license-files']:
        assert (REPO / name).is_file(), name
    assert config['build-system']['requires'] == ['setuptools>=77']


def test_sdist_carries_the_tests_and_the_files_they_read():
    """Regression: the sdist held only tests/test_*.py -- no conftest,
    unit, integration or mocks -- so its test suite could not run.
    MANIFEST.in grafts tests/ and every top-level directory a test
    reads through the repository root."""
    manifest = (REPO / 'MANIFEST.in').read_text()
    grafted = set(re.findall(r'^graft\s+(\S+)$', manifest, re.M))
    included = set(re.findall(r'^include\s+(\S+)$', manifest, re.M))
    assert 'tests' in grafted
    read = set()
    for source in (REPO / 'tests').rglob('*.py'):
        read.update(re.findall(
            r"(?:parents\[2\]|REPO)\s*/\s*'([^']+)'(?:\s*/\s*'([^']+)')?",
            source.read_text()))
    assert read, "no test reads repository files?"
    for top, sub in read:
        path = top if not sub else '{}/{}'.format(top, sub)
        if top in ('README.md', 'pyproject.toml', 'MANIFEST.in',
                   'thriftyx'):
            continue            # in every sdist
        assert top in grafted or any(
            name == path or name.startswith(path + '/')
            for name in included), path
