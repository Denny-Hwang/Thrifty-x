# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Properties of the CI workflow that a green run cannot show."""

import re
from pathlib import Path

CI = Path(__file__).resolve().parents[2] / '.github' / 'workflows' / 'ci.yml'


def test_run_steps_use_pipefail():
    """Regression: with no `shell:`, GitHub runs a step as `bash -e {0}`
    (no pipefail), so `scripts/upstream_diff.sh | tee -a ...` passed with
    an empty drift summary when the upstream fetch failed.  `shell: bash`
    runs `bash --noprofile --norc -eo pipefail {0}`."""
    text = CI.read_text()
    assert re.search(r'^defaults:\n  run:\n    shell: bash$', text, re.M)
    # No step opts back out to a shell without pipefail.
    shells = re.findall(r'^\s+shell:\s*(.+)$', text, re.M)
    assert shells == ['bash']
