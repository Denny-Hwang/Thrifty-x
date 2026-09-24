#!/bin/bash
# Rotate Thrifty-X capture artifacts on a Raspberry Pi 5 RX node.
# Run hourly via cron.
#
# Settings come from /etc/default/thriftyx-cleanup (see
# rpi/systemd/thriftyx-cleanup.env.example), because cron passes no
# environment: THRIFTYX_OUT must match the capture unit's, and the
# *_RETENTION_DAYS / DISK_*_PCT values set the policy.  Anything unset
# falls back to the defaults below.
#
# Cards under card/ and soak/<run>/ (rpi/soak_test.sh) expire after
# CARD_RETENTION_DAYS and are both candidates for the disk-full purge;
# .toad files under toad/ and .log files under log/ and soak/<run>/
# expire after their own retention.

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
# Retention is a whole number of days from 1 to 36500: 0, a stray value
# or one so large that N x 1440 minutes overflows must not become
# "expire everything", the card being written included.  A bad value
# skips only that expiry: the rest, the emergency purge included, still
# runs, and the exit status is 2.
STATUS=0
check_days() {
    [[ "$2" =~ ^0*([0-9]{1,5})$ ]] && [ "${BASH_REMATCH[1]}" -ge 1 ] \
        && [ "${BASH_REMATCH[1]}" -le 36500 ] && return 0
    say "$1='$2' is not a whole number of days from 1 to 36500;" \
        "not expiring those files"
    STATUS=2
    return 1
}
check_days CARD_RETENTION_DAYS "${CARD_DAYS}" || CARD_DAYS=
check_days TOAD_RETENTION_DAYS "${TOAD_DAYS}" || TOAD_DAYS=
check_days LOG_RETENTION_DAYS "${LOG_DAYS}" || LOG_DAYS=

# Disk thresholds are whole percentages from 1 to 100, warn <= purge.
# A bad value ("90%" say) made the `[` comparisons fail inside `if`,
# which silently turned the purge off.  It is logged, the default is
# used instead, and the exit status is 2.
check_pct() {
    [[ "$2" =~ ^0*([0-9]{1,3})$ ]] && [ "${BASH_REMATCH[1]}" -ge 1 ] \
        && [ "${BASH_REMATCH[1]}" -le 100 ] && return 0
    say "$1='$2' is not a whole percentage from 1 to 100; using $3"
    STATUS=2
    return 1
}
check_pct DISK_WARN_PCT "${DISK_WARN_PCT}" 80 || DISK_WARN_PCT=80
check_pct DISK_PURGE_PCT "${DISK_PURGE_PCT}" 90 || DISK_PURGE_PCT=90
DISK_WARN_PCT=$((10#${DISK_WARN_PCT}))
DISK_PURGE_PCT=$((10#${DISK_PURGE_PCT}))
if [ "${DISK_WARN_PCT}" -gt "${DISK_PURGE_PCT}" ]; then
    say "DISK_WARN_PCT=${DISK_WARN_PCT} is above" \
        "DISK_PURGE_PCT=${DISK_PURGE_PCT}; using 80 and 90"
    DISK_WARN_PCT=80
    DISK_PURGE_PCT=90
    STATUS=2
fi

cd "${ROOT}"

# expire DIR PATTERN DAYS: delete matching files older than DAYS (none
# when DAYS is empty) and print how many.  Ages are compared in minutes:
# `-mtime +N` rounds an age down to whole days and so kept files for
# N+1 days.
expire() {
    [ -n "$3" ] && [ -d "$1" ] || { echo 0; return 0; }
    find "$1" -type f -name "$2" -mmin "+$((10#$3 * 1440))" -print -delete \
        | wc -l
}
N_CARD=$(( $(expire card '*.card' "${CARD_DAYS}") \
            + $(expire soak '*.card' "${CARD_DAYS}") ))
N_TOAD="$(expire toad '*.toad' "${TOAD_DAYS}")"
N_LOG=$(( $(expire log '*.log' "${LOG_DAYS}") \
           + $(expire soak '*.log' "${LOG_DAYS}") ))
N_PURGED=0

disk_pct() { df --output=pcent "${ROOT}" | tail -1 | tr -dc '0-9'; }
USE_PCT="$(disk_pct)"
if [ "${USE_PCT}" -ge "${DISK_PURGE_PCT}" ]; then
    say "disk ${USE_PCT}% >= ${DISK_PURGE_PCT}% — emergency purge oldest .card files"
    PURGE_DIRS=()
    for dir in card soak; do
        if [ -d "${dir}" ]; then PURGE_DIRS+=("${dir}"); fi
    done
    # Delete the oldest cards, capture and soak alike, until below the
    # warn threshold.  NUL-terminated "mtime<TAB>path" records keep any
    # file name whole (`awk '{print $2}'` cut a name at its first space;
    # rm -f of the cut name "succeeded", nothing was freed and the loop
    # never ended).  The list is walked once, so a card that cannot be
    # deleted, or whose deletion frees nothing, cannot loop forever.
    if [ "${#PURGE_DIRS[@]}" -gt 0 ]; then
        while IFS=$'\t' read -r -d '' _ path; do
            [ "$(disk_pct)" -ge "${DISK_WARN_PCT}" ] || break
            rm -f -- "${path}" || true
            if [ -e "${path}" ]; then
                say "cannot delete ${path}"
                STATUS=2
                continue
            fi
            N_PURGED=$((N_PURGED + 1))
        done < <(find "${PURGE_DIRS[@]}" -type f -name '*.card' \
                      -mmin "+${ACTIVE_GRACE_MIN}" -printf '%T@\t%p\0' \
                      2>/dev/null | LC_ALL=C sort -z -n)
    fi
elif [ "${USE_PCT}" -ge "${DISK_WARN_PCT}" ]; then
    say "disk ${USE_PCT}% >= ${DISK_WARN_PCT}% — warning"
fi

if [ $((N_CARD + N_TOAD + N_LOG + N_PURGED)) -gt 0 ]; then
    say "deleted ${N_CARD} card, ${N_TOAD} toad, ${N_LOG} log file(s)" \
        "past retention; purged ${N_PURGED} card file(s) for space;" \
        "disk now $(df --output=pcent "${ROOT}" | tail -1 | tr -d ' ')"
fi
exit "${STATUS}"
