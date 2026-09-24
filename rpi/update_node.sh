#!/bin/bash
# Idempotent remote-update wrapper for a Thrifty-X RX node.
#
# Pulls the latest code, reinstalls the package, refreshes the installed
# systemd units and helper scripts, restarts the capture service, and
# verifies it stays up.  On any failure it rolls back to the last known
# good git SHA (code, package, units and scripts) + restarts so the node
# is never left in a broken state.  A failed install goes back to the
# old SHA without a restart: nothing has touched the service yet.
#
# Safe to call repeatedly: a no-op when the node runs the remote tip and
# that tip passed the health check here (recorded in
# .last_known_good_sha).  A run cut short (power loss, kill, Ctrl-C)
# leaves HEAD different from that record, or .update_pending behind; the
# next run finishes the job -- install, refresh, restart, health check,
# rollback on failure -- instead of calling the half-installed node up to
# date.  On a node without the record (never updated by this script) the
# first run verifies the checked-out release the same way, restarting
# capture once.
#
# The node only moves forward along origin's branch.  A clone with
# commits origin/<branch> does not have -- a local commit, or origin
# rewound by a force push -- is refused (exit 1, service untouched).
# Roll the fleet back by pushing a revert commit, not by a force push.
#
# Typical use:
#   ssh rx0 'sudo /usr/local/bin/update_node.sh'
#
# A dropped SSH session does not stop the run: it ignores SIGHUP and
# SIGPIPE, and its output also goes to the journal
# (journalctl -t update_node).  To detach it from the session entirely:
#   ssh rx0 'sudo systemd-run --collect --unit=thriftyx-update /usr/local/bin/update_node.sh'
#   ssh rx0 'journalctl -u thriftyx-update -f'
#
# The capture instance restarted is the one this node has an
# /etc/default/thriftyx-capture@<rxid> file for (thriftyx-capture@rx1 on
# rx1).  With none or several, name it on sudo's command line (sudo
# drops variables the caller exported; systemd-run takes -E NAME=VALUE):
#   ssh rx1 'sudo THRIFTYX_RXID=1 /usr/local/bin/update_node.sh'
#   ssh rx1 'sudo THRIFTYX_SERVICE=thriftyx-capture@rx1.service /usr/local/bin/update_node.sh'
#
# Root is needed only for systemctl.  git and pip run as the owner of
# the clone (normally pi): run as root they would leave root-owned
# objects in .git and the venv, after which the owner's own `git pull` /
# `pip install` fail, and git's safe.directory check would reject the
# clone outright when root is not the owner.
#
# Exit codes:
#   0   already up to date OR updated (an unfinished update included)
#   1   update failed, node back on the last known good SHA (service
#       running it; a failed install never restarts a service that
#       already ran the old SHA), or refused because the clone is ahead
#       of or diverged from origin (service untouched)
#   2   update failed AND rollback also failed, or there was no other
#       known good SHA to roll back to (service may be down — page!)
#   3   setup error (paths missing, not a git repo, capture instance
#       unknown, etc.)

set -uo pipefail

# An SSH drop must not kill the run between `git merge` and the health
# check: the node would be left on new code, half installed, its units
# replaced but not reloaded, and never restarted.  The hangup and writes
# to the vanished terminal are ignored (children inherit that), and the
# output is copied to the journal through `tee -p`, which keeps feeding
# logger once the terminal is gone.  Under systemd (systemd-run) stdout
# already is the journal.
trap '' HUP PIPE
if [ -z "${JOURNAL_STREAM:-}" ] && command -v logger >/dev/null 2>&1 \
        && tee -p </dev/null >/dev/null 2>&1; then
    exec > >(tee -p >(logger -t update_node 2>/dev/null)) 2>&1
fi

HOME_DIR="${THRIFTYX_HOME:-/home/pi/thrifty-x}"
BRANCH="${THRIFTYX_BRANCH:-master}"
HEALTH_WAIT_S="${HEALTH_WAIT_S:-30}"
# The SHA whose install last passed the health check here, and the
# marker of an update (or rollback) in progress.
LKG_FILE="${HOME_DIR}/.last_known_good_sha"
PENDING_FILE="${HOME_DIR}/.update_pending"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"
ENV_DIR="${ENV_DIR:-/etc/default}"

log() { echo "[update_node] $*"; }
die() { log "FATAL: $*"; exit 3; }

# systemctl and the unit/script refresh need root.  Checked first: as
# a normal user the pull and install would succeed and the restart
# fail, triggering a pointless rollback.
[ "$(id -u)" -eq 0 ] || die "run as root: sudo $0"
[ -d "${HOME_DIR}/.git" ] || die "not a git repo: ${HOME_DIR}"
[ -x "${HOME_DIR}/.venv/bin/pip" ] || die "venv missing: ${HOME_DIR}/.venv"

# The capture instance to restart.  Unless named, it is the one set up on
# this node: the unit's EnvironmentFile= is required, so only an
# instance with an ENV_DIR/thriftyx-capture@<rxid> file can start here.
# (A fixed rx0 default restarted a unit that fails on every other node.)
if [ -n "${THRIFTYX_SERVICE:-}" ]; then
    SERVICE="${THRIFTYX_SERVICE}"
elif [ -n "${THRIFTYX_RXID:-}" ]; then
    SERVICE="thriftyx-capture@rx${THRIFTYX_RXID}.service"
else
    shopt -s nullglob
    envs=("${ENV_DIR}"/thriftyx-capture@*)
    shopt -u nullglob
    [ "${#envs[@]}" -eq 1 ] \
        || die "need exactly one ${ENV_DIR}/thriftyx-capture@<rxid> file" \
               "to tell which capture instance runs here, found" \
               "${#envs[@]}; name it: sudo" \
               "THRIFTYX_SERVICE=thriftyx-capture@rxN.service $0"
    SERVICE="thriftyx-capture@${envs[0]##*/thriftyx-capture@}.service"
fi
# Checked before the pull: a wrong instance would otherwise surface only
# as a failed restart, whose rollback fails the same way (exit 2).
case "${SERVICE}" in
    thriftyx-capture@*)
        instance="${SERVICE#thriftyx-capture@}"
        instance="${instance%.service}"
        [ -e "${ENV_DIR}/thriftyx-capture@${instance}" ] \
            || die "${SERVICE} is not set up on this node" \
                   "(no ${ENV_DIR}/thriftyx-capture@${instance})"
        ;;
esac

cd "${HOME_DIR}" || die "cannot cd to ${HOME_DIR}"
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
# write_owned TEXT FILE: FILE (in the clone) holds TEXT, owned by OWNER.
write_owned() {
    # shellcheck disable=SC2016  # expanded by the inner sh
    as_owner sh -c 'echo "$1" > "$2"' _ "$1" "$2"
}

# Ensure clean tree — refuse to update on top of local changes.
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree dirty; refuse to update (commit or stash first)"
fi

OLD_SHA="$(git rev-parse HEAD)"
LKG=""
if [ -e "${LKG_FILE}" ]; then
    read -r LKG < "${LKG_FILE}" || true
    if ! [[ "${LKG}" =~ ^[0-9a-f]{40,64}$ ]] \
            || ! git cat-file -e "${LKG}^{commit}" 2>/dev/null; then
        log "ignoring ${LKG_FILE}: '${LKG}' is not a commit of this clone"
        LKG=""
    fi
fi
log "current sha = ${OLD_SHA}; last known good = ${LKG:-none};" \
    "capture unit = ${SERVICE}"

# Fetch
if ! git fetch --quiet origin "${BRANCH}"; then
    die "git fetch origin ${BRANCH} failed (network, DNS or credentials?" \
        "try: sudo -u ${OWNER} git -C ${HOME_DIR} fetch origin)"
fi
NEW_SHA="$(git rev-parse "origin/${BRANCH}")"

# Only fast-forwards.  A HEAD ahead of origin (a local commit) or
# diverged from it (origin rewound by a force push) would otherwise be
# "updated" to itself -- `merge --ff-only` says "Already up to date" --
# restarting capture on every run and recording origin's SHA as good
# while HEAD is something else.
git merge-base --is-ancestor HEAD "origin/${BRANCH}"
case $? in
    0) ;;
    1)
        log "refusing to update: HEAD ${OLD_SHA} is not an ancestor of" \
            "origin/${BRANCH} ${NEW_SHA} -- the clone has commits origin" \
            "does not (a local commit, or origin was rewound by a force" \
            "push).  Service untouched.  To follow origin, as ${OWNER}:" \
            "git -C ${HOME_DIR} log origin/${BRANCH}..HEAD to see what" \
            "would be dropped, git -C ${HOME_DIR} reset --hard" \
            "origin/${BRANCH}, then re-run this script."
        exit 1 ;;
    *) die "git merge-base failed in ${HOME_DIR}" ;;
esac

# Is HEAD known to be installed, running and healthy?  A run cut short
# leaves the pending marker, or HEAD past the last known good SHA.
UNFINISHED=""
if [ -e "${PENDING_FILE}" ]; then
    UNFINISHED="an earlier update or rollback did not finish"
elif [ -n "${LKG}" ] && [ "${LKG}" != "${OLD_SHA}" ]; then
    UNFINISHED="HEAD is not the last known good sha (an earlier"
    UNFINISHED+=" update did not finish, or HEAD was moved by hand)"
fi

if [ -z "${UNFINISHED}" ] && [ -n "${LKG}" ] \
        && [ "${OLD_SHA}" = "${NEW_SHA}" ]; then
    log "already up to date (${OLD_SHA}) — no-op"
    exit 0
fi

# Where a failure goes back to: the last known good SHA or, with no
# record, the SHA checked out before this run.
ROLLBACK_SHA="${LKG:-${OLD_SHA}}"
if [ -n "${UNFINISHED}" ]; then
    log "${UNFINISHED}: finishing it (rollback target ${ROLLBACK_SHA})"
elif [ -z "${LKG}" ]; then
    log "no ${LKG_FILE}: nothing shows the checked-out release was" \
        "installed and health-checked; doing it now"
fi
if [ "${OLD_SHA}" = "${NEW_SHA}" ]; then
    log "installing and verifying ${NEW_SHA}"
else
    log "updating ${OLD_SHA} -> ${NEW_SHA}"
fi

# From here until the node is verified, the marker says so.
write_owned "${ROLLBACK_SHA} ${NEW_SHA}" "${PENDING_FILE}" \
    || die "cannot write ${PENDING_FILE}"

# The node runs $1, installed and health-checked.  Without the record
# the pending marker stays and the next run verifies again.
record_good() {
    if write_owned "$1" "${LKG_FILE}"; then
        rm -f "${PENDING_FILE}"
    else
        log "WARNING: could not write ${LKG_FILE}; the next run" \
            "verifies $1 again"
    fi
}

# Step 1: fast-forward checkout
if [ "${OLD_SHA}" != "${NEW_SHA}" ] \
        && ! git merge --ff-only "origin/${BRANCH}"; then
    log "fast-forward to ${NEW_SHA} failed; aborting (no service touch)"
    [ -n "${UNFINISHED}" ] || rm -f "${PENDING_FILE}"
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
    record_good "${target}"
    return 0
}

# A step after the install failed: back to ROLLBACK_SHA, in full.
fail_back() {
    if [ "${ROLLBACK_SHA}" = "${NEW_SHA}" ]; then
        log "no other known good sha to roll back to;" \
            "${SERVICE} may be down"
        exit 2
    fi
    if rollback "${ROLLBACK_SHA}"; then exit 1; else exit 2; fi
}

if ! do_install; then
    if [ -n "${UNFINISHED}" ]; then
        # The service, units or venv may already hold the unfinished
        # release: restore the old one completely.
        log "pip install failed on ${NEW_SHA}; rolling back"
        fail_back
    fi
    # Nothing has touched the service or its units yet, and the install
    # is editable: the reset alone gives the service back its code.  The
    # reinstall undoes whatever pip upgraded before failing; when it
    # fails too (package index unreachable, the usual cause), the node
    # is still running the old release -- exit 1, not a page -- and the
    # pending marker makes the next run redo the install.
    log "pip install failed on new sha; rolling back (service untouched)"
    git reset --hard --quiet "${OLD_SHA}" \
        || { log "git reset to ${OLD_SHA} failed"; exit 2; }
    if do_install; then
        rm -f "${PENDING_FILE}"
    else
        log "WARNING: reinstalling ${OLD_SHA} failed too (package index" \
            "unreachable?); the service was not restarted and still runs" \
            "it; re-run update_node.sh once pip works"
    fi
    exit 1
fi

if ! do_refresh; then
    log "refreshing units/scripts failed on new sha; rolling back"
    fail_back
fi

if ! do_restart; then
    log "systemctl restart failed on new sha; rolling back"
    fail_back
fi

if ! do_healthy; then
    log "service not active after ${HEALTH_WAIT_S}s; rolling back"
    fail_back
fi

# Success — record LKG
record_good "${NEW_SHA}"
log "OK: now running ${NEW_SHA}"
exit 0
