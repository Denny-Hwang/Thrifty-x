import sys
from pathlib import Path


# Ensure the repository root is on the import path so ``thriftyx`` can be
# imported without needing an editable install when running the test suite.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
