#!/bin/bash
# Idempotent remote-update wrapper for a Thrifty-X RX node.
#
# Pulls the latest code, reinstalls the package, restarts the capture
# service, and verifies it stays up.  On any failure it rolls back to
# the previous git SHA + reinstalls + restarts so the node is never
# left in a broken state.
#
# Safe to call repeatedly (no-op when already at remote tip).
#
# Typical use:
#   ssh rx0 'sudo /usr/local/bin/update_node.sh'
#
# Exit codes:
#   0   already up to date OR updated successfully
#   1   update failed AND rollback succeeded (service running on old SHA)
#   2   update failed AND rollback also failed (service may be down — page!)
#   3   setup error (paths missing, not a git repo, etc.)

set -uo pipefail

HOME_DIR="${THRIFTYX_HOME:-/home/pi/thrifty-x}"
RXID="${THRIFTYX_RXID:-0}"
SERVICE="${THRIFTYX_SERVICE:-thriftyx-capture@rx${RXID}.service}"
BRANCH="${THRIFTYX_BRANCH:-master}"
HEALTH_WAIT_S="${HEALTH_WAIT_S:-30}"
PIP_EXTRAS="${PIP_EXTRAS:-analysis,fft}"
LKG_FILE="${HOME_DIR}/.last_known_good_sha"

log() { echo "[update_node] $*"; }
die() { log "FATAL: $*"; exit 3; }

[ -d "${HOME_DIR}/.git" ] || die "not a git repo: ${HOME_DIR}"
[ -x "${HOME_DIR}/.venv/bin/pip" ] || die "venv missing: ${HOME_DIR}/.venv"

cd "${HOME_DIR}"
PIP="${HOME_DIR}/.venv/bin/pip"

# Ensure clean tree — refuse to update on top of local changes.
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree dirty; refuse to update (commit or stash first)"
fi

OLD_SHA="$(git rev-parse HEAD)"
log "current sha = ${OLD_SHA}"

# Fetch
if ! git fetch --quiet origin "${BRANCH}"; then
    die "git fetch failed"
fi
NEW_SHA="$(git rev-parse "origin/${BRANCH}")"

if [ "${OLD_SHA}" = "${NEW_SHA}" ]; then
    log "already up to date (${OLD_SHA}) — no-op"
    exit 0
fi

log "updating ${OLD_SHA} -> ${NEW_SHA}"

# Step 1: fast-forward checkout
if ! git merge --ff-only "origin/${BRANCH}"; then
    log "fast-forward failed; aborting (no service touch)"
    exit 1
fi

# Step 2: reinstall (deps may have changed)
do_install() {
    "${PIP}" install --quiet -e ".[${PIP_EXTRAS}]"
}

# Step 3: restart + health check
do_restart() {
    systemctl restart "${SERVICE}"
}
do_healthy() {
    sleep "${HEALTH_WAIT_S}"
    [ "$(systemctl is-active "${SERVICE}" 2>/dev/null)" = "active" ]
}

rollback() {
    local target="$1"
    log "rolling back to ${target}"
    git reset --hard --quiet "${target}" || return 1
    do_install || return 1
    do_restart || return 1
    do_healthy || return 1
    return 0
}

if ! do_install; then
    log "pip install failed on new sha; rolling back"
    if rollback "${OLD_SHA}"; then exit 1; else exit 2; fi
fi

if ! do_restart; then
    log "systemctl restart failed on new sha; rolling back"
    if rollback "${OLD_SHA}"; then exit 1; else exit 2; fi
fi

if ! do_healthy; then
    log "service not active after ${HEALTH_WAIT_S}s; rolling back"
    if rollback "${OLD_SHA}"; then exit 1; else exit 2; fi
fi

# Success — record LKG
echo "${NEW_SHA}" > "${LKG_FILE}"
log "OK: now running ${NEW_SHA}"
exit 0
