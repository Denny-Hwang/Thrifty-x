#!/usr/bin/env bash
# Compare the thriftyx/ package with the upstream Thrifty commit it forks.
#
# Thrifty-X is a fork of https://github.com/swkrueger/Thrifty at the
# commit pinned below (also recorded in README.md).  The original Python
# sources are not kept in this repository; this script fetches them on
# demand, so the comparison is always against the real upstream code.
#
# Usage:
#   scripts/upstream_diff.sh                 # per-module drift summary
#   scripts/upstream_diff.sh carrier_sync.py # full diff of one module
#
# The summary is Markdown (CI appends it to the job summary).
set -euo pipefail

UPSTREAM_URL=https://github.com/swkrueger/Thrifty
UPSTREAM_COMMIT=2ad9775753a8712a61c81cc78fb0bc75a921d50b

cd "$(git rev-parse --show-toplevel)"
git fetch --quiet --depth=1 "$UPSTREAM_URL" "$UPSTREAM_COMMIT"

# Unified diff of one module: upstream thrifty/<m> vs working-tree thriftyx/<m>.
module_diff() {
    diff -u --label "upstream/thrifty/$1" --label "thriftyx/$1" \
        <(git show "$UPSTREAM_COMMIT:thrifty/$1") "thriftyx/$1" || true
}

if [ $# -gt 0 ]; then
    for module in "$@"; do
        module_diff "$module"
    done
    exit 0
fi

echo "## Drift from upstream Thrifty (${UPSTREAM_COMMIT:0:7})"
echo
echo "| module | lines added | lines removed |"
echo "|---|---:|---:|"
git ls-tree -r --name-only "$UPSTREAM_COMMIT" thrifty/ | grep '\.py$' |
while read -r path; do
    module=${path#thrifty/}
    if [ ! -f "thriftyx/$module" ]; then
        echo "| \`$module\` | _not in Thrifty-X_ | |"
        continue
    fi
    body=$(module_diff "$module" | tail -n +3)
    added=$(grep -c '^+' <<<"$body" || true)
    removed=$(grep -c '^-' <<<"$body" || true)
    echo "| \`$module\` | $added | $removed |"
done
