#!/bin/bash
# Rotate Thrifty-X capture artifacts on a Raspberry Pi 5 RX node.
# Run hourly via cron.
#
# Settings come from /etc/default/thriftyx-cleanup (see
# rpi/systemd/thriftyx-cleanup.env.example), because cron passes no
# environment: THRIFTYX_OUT must match the capture unit's, and the
# *_RETENTION_DAYS / DISK_*_PCT values set the policy.  Anything unset
# falls back to the defaults below.

set -euo pipefail

# cron discards output unless mail is set up: report to syslog/journald
# (journalctl -t thriftyx-cleanup) instead, including a failure.
say() { logger -t thriftyx-cleanup "$*" 2>/dev/null || echo "$*" >&2; }
trap 'say "failed (exit $?) at line ${LINENO}"' ERR

CONFIG="${THRIFTYX_CLEANUP_CONFIG:-/etc/default/thriftyx-cleanup}"
if [ -r "${CONFIG}" ]; then
    # shellcheck source=/dev/null
    . "${CONFIG}"
fi

ROOT="${THRIFTYX_OUT:-/var/lib/thriftyx}"
CARD_DAYS="${CARD_RETENTION_DAYS:-7}"
TOAD_DAYS="${TOAD_RETENTION_DAYS:-30}"
LOG_DAYS="${LOG_RETENTION_DAYS:-30}"
DISK_WARN_PCT="${DISK_WARN_PCT:-80}"
DISK_PURGE_PCT="${DISK_PURGE_PCT:-90}"
# Emergency purge never touches files modified in the last N minutes:
# the capture service's current output is often the only/oldest card
# file, and unlinking it loses live data without freeing space (the
# writer keeps its fd open).
ACTIVE_GRACE_MIN="${ACTIVE_GRACE_MIN:-10}"

# Defensive: refuse to operate on an empty or root path even if a
# misconfigured environment file sets THRIFTYX_OUT="" or "/".  The
# `find ... -delete` below would otherwise become catastrophic.
if [ -z "${ROOT}" ] || [ "${ROOT}" = "/" ]; then
    say "refusing to operate on ROOT='${ROOT}'"
    exit 2
fi
if [ ! -d "${ROOT}" ]; then
    say "ROOT '${ROOT}' is not a directory (data disk not mounted?)"
    exit 2
fi
# Retention is a whole number of days, at least 1: 0 or a stray value
# must not become "expire everything", the card being written included.
check_days() {
    [[ "$2" =~ ^[0-9]+$ ]] && [ "$((10#$2))" -ge 1 ] && return 0
    say "$1='$2' is not a whole number of days (at least 1)"
    exit 2
}
check_days CARD_RETENTION_DAYS "${CARD_DAYS}"
check_days TOAD_RETENTION_DAYS "${TOAD_DAYS}"
check_days LOG_RETENTION_DAYS "${LOG_DAYS}"

cd "${ROOT}"

# expire DIR PATTERN DAYS: delete matching files older than DAYS and
# print how many.  Ages are compared in minutes: `-mtime +N` rounds an
# age down to whole days and so kept files for N+1 days.
expire() {
    [ -d "$1" ] || { echo 0; return 0; }
    find "$1" -type f -name "$2" -mmin "+$((10#$3 * 1440))" -print -delete \
        | wc -l
}
N_CARD="$(expire card '*.card' "${CARD_DAYS}")"
N_TOAD="$(expire toad '*.toad' "${TOAD_DAYS}")"
N_LOG="$(expire log '*.log' "${LOG_DAYS}")"
N_PURGED=0

USE_PCT="$(df --output=pcent "${ROOT}" | tail -1 | tr -dc '0-9')"
if [ "${USE_PCT}" -ge "${DISK_PURGE_PCT}" ]; then
    say "disk ${USE_PCT}% >= ${DISK_PURGE_PCT}% — emergency purge oldest .card files"
    # Delete oldest .card files until below warn threshold
    while [ "$(df --output=pcent "${ROOT}" | tail -1 | tr -dc '0-9')" -ge "${DISK_WARN_PCT}" ]; do
        [ -d card ] || break
        # awk 'NR==1' (not `head -1`) so the whole stream is consumed:
        # with `set -o pipefail`, head exiting early would kill sort
        # with SIGPIPE (status 141) and `set -e` would abort the purge
        # exactly when the disk is full of card files (>~1500 entries).
        # `|| true` also tolerates a find error mid-loop (e.g. the
        # directory disappearing) instead of aborting the script.
        OLDEST="$(find card -type f -name '*.card' -mmin "+${ACTIVE_GRACE_MIN}" \
                  -printf '%T@ %p\n' 2>/dev/null \
                  | sort -n | awk 'NR==1 {print $2}')" || true
        [ -z "${OLDEST}" ] && break
        rm -f "${OLDEST}"
        N_PURGED=$((N_PURGED + 1))
    done
elif [ "${USE_PCT}" -ge "${DISK_WARN_PCT}" ]; then
    say "disk ${USE_PCT}% >= ${DISK_WARN_PCT}% — warning"
fi

if [ $((N_CARD + N_TOAD + N_LOG + N_PURGED)) -gt 0 ]; then
    say "deleted ${N_CARD} card, ${N_TOAD} toad, ${N_LOG} log file(s)" \
        "past retention; purged ${N_PURGED} card file(s) for space;" \
        "disk now $(df --output=pcent "${ROOT}" | tail -1 | tr -d ' ')"
fi
