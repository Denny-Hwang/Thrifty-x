#!/bin/bash
# Idempotent remote-update wrapper for a Thrifty-X RX node.
#
# Pulls the latest code, reinstalls the package, refreshes the installed
# systemd units and helper scripts, restarts the capture service, and
# verifies it stays up.  On any failure it rolls back to the previous
# git SHA (code, package, units and scripts) + restarts so the node is
# never left in a broken state.
#
# Safe to call repeatedly (no-op when already at remote tip).
#
# Typical use:
#   ssh rx0 'sudo /usr/local/bin/update_node.sh'
#
# Root is needed only for systemctl.  git and pip run as the owner of
# the clone (normally pi): run as root they would leave root-owned
# objects in .git and the venv, after which the owner's own `git pull` /
# `pip install` fail, and git's safe.directory check would reject the
# clone outright when root is not the owner.
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
LKG_FILE="${HOME_DIR}/.last_known_good_sha"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"

log() { echo "[update_node] $*"; }
die() { log "FATAL: $*"; exit 3; }

# systemctl and the unit/script refresh need root.  Checked first: as
# a normal user the pull and install would succeed and the restart
# fail, triggering a pointless rollback.
[ "$(id -u)" -eq 0 ] || die "run as root: sudo $0"
[ -d "${HOME_DIR}/.git" ] || die "not a git repo: ${HOME_DIR}"
[ -x "${HOME_DIR}/.venv/bin/pip" ] || die "venv missing: ${HOME_DIR}/.venv"

cd "${HOME_DIR}"
PIP="${HOME_DIR}/.venv/bin/pip"

# Reinstall with the extras the node already has: pyfftw (the `fft`
# extra) does not build everywhere, and nodes installed without it
# must not fail -- and roll back -- every update trying to build it.
if [ -z "${PIP_EXTRAS:-}" ]; then
    PIP_EXTRAS="analysis"
    if "${HOME_DIR}/.venv/bin/python" -c 'import pyfftw' 2>/dev/null; then
        PIP_EXTRAS="analysis,fft"
    fi
fi

# Run a command as the clone's owner (a no-op when we already are).
OWNER="$(stat -c %U "${HOME_DIR}")"
OWNER_HOME="$(getent passwd "${OWNER}" | cut -d: -f6)"
as_owner() {
    if [ "$(id -un)" = "${OWNER}" ]; then
        "$@"
    else
        runuser -u "${OWNER}" -- env HOME="${OWNER_HOME}" "$@"
    fi
}
GIT_BIN="$(command -v git)" || die "git not found"
git() { as_owner "${GIT_BIN}" "$@"; }

# Ensure clean tree — refuse to update on top of local changes.
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree dirty; refuse to update (commit or stash first)"
fi

OLD_SHA="$(git rev-parse HEAD)"
log "current sha = ${OLD_SHA}"

# Fetch
if ! git fetch --quiet origin "${BRANCH}"; then
    die "git fetch origin ${BRANCH} failed (network, DNS or credentials?" \
        "try: sudo -u ${OWNER} git -C ${HOME_DIR} fetch origin)"
fi
NEW_SHA="$(git rev-parse "origin/${BRANCH}")"

if [ "${OLD_SHA}" = "${NEW_SHA}" ]; then
    log "already up to date (${OLD_SHA}) — no-op"
    exit 0
fi

log "updating ${OLD_SHA} -> ${NEW_SHA}"

# Step 1: fast-forward checkout
if ! git merge --ff-only "origin/${BRANCH}"; then
    log "fast-forward failed: local ${BRANCH} has commits origin does not;" \
        "aborting (no service touch)"
    exit 1
fi

# Step 2: reinstall (deps may have changed)
do_install() {
    as_owner "${PIP}" install --quiet -e ".[${PIP_EXTRAS}]"
}

# Step 2b: refresh what was installed from the repo -- units under
# UNIT_DIR and helper scripts under BIN_DIR -- when a copy exists and
# differs.  Only files already installed are touched (a node without
# the heartbeat timer does not get one).  Scripts are replaced by
# rename, never rewritten in place: bash reads a running script (this
# one) incrementally from its open file.
refresh_file() {
    # Prints "changed" when it replaced DST; fails only on a write error.
    local src="$1" dst="$2" mode="$3"
    [ -e "${dst}" ] || return 0              # not installed on this node
    cmp -s "${src}" "${dst}" && return 0     # already current
    install -m "${mode}" "${src}" "${dst}.new" \
        && mv -f "${dst}.new" "${dst}" || return 1
    echo changed
}
do_refresh() {
    local f out units_changed=0
    for f in rpi/systemd/*.service rpi/systemd/*.timer; do
        out="$(refresh_file "${f}" "${UNIT_DIR}/${f##*/}" 644)" || return 1
        if [ -n "${out}" ]; then
            log "updated ${UNIT_DIR}/${f##*/}"
            units_changed=1
        fi
    done
    for f in update_node.sh cleanup_old_captures.sh; do
        out="$(refresh_file "rpi/${f}" "${BIN_DIR}/${f}" 755)" || return 1
        [ -z "${out}" ] || log "updated ${BIN_DIR}/${f}"
    done
    if [ "${units_changed}" -eq 1 ]; then
        log "systemd units changed; daemon-reload"
        systemctl daemon-reload || return 1
    fi
    return 0
}

# Step 3: restart + health check
do_restart() {
    systemctl restart "${SERVICE}"
}
# Healthy = active after HEALTH_WAIT_S *without having restarted*: with
# Restart=always a crash-looping capture is "active" for most of each
# 10 s cycle, so is-active alone passes a broken update.
do_healthy() {
    local pid0 restarts0
    pid0="$(systemctl show -p MainPID --value "${SERVICE}")"
    restarts0="$(systemctl show -p NRestarts --value "${SERVICE}")"
    sleep "${HEALTH_WAIT_S}"
    [ "$(systemctl is-active "${SERVICE}" 2>/dev/null)" = "active" ] \
        || { log "service is not active"; return 1; }
    [ "$(systemctl show -p NRestarts --value "${SERVICE}")" = "${restarts0}" ] \
        && [ "$(systemctl show -p MainPID --value "${SERVICE}")" = "${pid0}" ] \
        && [ "${pid0}" != "0" ] \
        || { log "service restarted during the health check"; return 1; }
}

rollback() {
    local target="$1"
    log "rolling back to ${target}"
    git reset --hard --quiet "${target}" || return 1
    do_install || return 1
    do_refresh || return 1
    do_restart || return 1
    do_healthy || return 1
    return 0
}

if ! do_install; then
    log "pip install failed on new sha; rolling back"
    if rollback "${OLD_SHA}"; then exit 1; else exit 2; fi
fi

if ! do_refresh; then
    log "refreshing units/scripts failed on new sha; rolling back"
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
as_owner sh -c 'echo "$1" > "$2"' _ "${NEW_SHA}" "${LKG_FILE}"
log "OK: now running ${NEW_SHA}"
exit 0
