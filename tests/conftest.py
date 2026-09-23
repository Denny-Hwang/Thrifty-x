import sys
from pathlib import Path


# Ensure the repository root is on the import path so ``thriftyx`` can be
# imported without needing an editable install when running the test suite.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure the tests/ directory is on the import path so that ``tests.mocks``
# is importable as a package (e.g. from integration tests).
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_default_config(tmp_path_factory, monkeypatch):
    """Point the implicit ``./detector.cfg`` lookup at an empty directory.

    Every command reads ``detector.cfg`` from the working directory when
    no ``-c`` is given.  Without this, a developer's own detector.cfg in
    the checkout (the README tells users to create one there) would
    silently change what the tests exercise.  A test that needs a default
    config writes it to ``settings.DEFAULT_CONFIG_PATH``.
    """
    from thriftyx import settings
    empty = tmp_path_factory.mktemp('no-default-config')
    monkeypatch.setattr(settings, 'DEFAULT_CONFIG_PATH',
                        str(empty / 'detector.cfg'))
