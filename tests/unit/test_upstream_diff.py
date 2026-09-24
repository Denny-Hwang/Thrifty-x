# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""scripts/upstream_diff.sh against a local upstream and fork.

The upstream is a scratch repository standing in for swkrueger/Thrifty;
the fork is cloned from it, as Thrifty-X is, so the pinned upstream
commit is an ancestor of the fork's history.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'upstream_diff.sh'

pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or shutil.which('git') is None,
    reason="needs bash and git")


def _git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    upstream = tmp_path / 'upstream'
    (upstream / 'thrifty').mkdir(parents=True)
    _git(upstream, 'init', '-q', '-b', 'master')
    for i in range(3):
        with open(upstream / 'thrifty' / 'a.py', 'a') as f:
            f.write('a{}\n'.format(i))
        (upstream / 'thrifty' / 'b.py').write_text('b{}\n'.format(i))
        _git(upstream, 'add', '-A')
        _git(upstream, 'commit', '-q', '-m', 'u{}'.format(i))
    commit = _git(upstream, 'rev-parse', 'HEAD')

    fork = tmp_path / 'fork'
    _git(tmp_path, 'clone', '-q', upstream.as_uri(), str(fork))
    (fork / 'thriftyx').mkdir()
    (fork / 'thriftyx' / 'a.py').write_text('a0\nA1\na2\nnew\n')
    _git(fork, 'add', '-A')
    _git(fork, 'commit', '-q', '-m', 'fork')

    env = dict(os.environ, THRIFTYX_UPSTREAM_URL=upstream.as_uri(),
               THRIFTYX_UPSTREAM_COMMIT=commit)

    def run(cwd, *args):
        return subprocess.run(['bash', str(SCRIPT), *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=60)
    return dict(run=run, fork=fork, upstream=upstream, tmp=tmp_path)


def test_summary(repos):
    result = repos['run'](repos['fork'])
    assert result.returncode == 0, result.stderr
    assert '| `a.py` | 2 | 1 |' in result.stdout
    assert '| `b.py` | _not in Thrifty-X_ | |' in result.stdout


def test_full_clone_stays_full(repos):
    """Regression: the --depth=1 fetch of a commit the clone already has
    wrote it to .git/shallow, cutting the fork's history off there."""
    fork = repos['fork']
    count = _git(fork, 'rev-list', '--count', 'HEAD')
    assert repos['run'](fork).returncode == 0
    assert _git(fork, 'rev-parse', '--is-shallow-repository') == 'false'
    assert _git(fork, 'rev-list', '--count', 'HEAD') == count


def test_shallow_checkout_fetches_the_commit(repos):
    """CI's depth=1 checkout lacks the upstream commit: it is fetched."""
    ci = repos['tmp'] / 'ci'
    _git(repos['tmp'], 'clone', '-q', '--depth=1', repos['fork'].as_uri(),
         str(ci))
    result = repos['run'](ci)
    assert result.returncode == 0, result.stderr
    assert result.stdout == repos['run'](repos['fork']).stdout


def test_failed_fetch_fails(repos):
    ci = repos['tmp'] / 'ci'
    _git(repos['tmp'], 'clone', '-q', '--depth=1', repos['fork'].as_uri(),
         str(ci))
    shutil.rmtree(repos['upstream'])
    result = repos['run'](ci)
    assert result.returncode != 0
    assert 'Drift' not in result.stdout
