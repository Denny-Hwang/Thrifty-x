#!/bin/bash
# Rotate Thrifty-X capture artifacts on a Raspberry Pi 5 RX node.
# Run hourly via cron. Override via env vars.

set -euo pipefail

ROOT="${THRIFTYX_OUT:-/var/lib/thriftyx}"
CARD_DAYS="${CARD_RETENTION_DAYS:-7}"
TOAD_DAYS="${TOAD_RETENTION_DAYS:-30}"
LOG_DAYS="${LOG_RETENTION_DAYS:-30}"
DISK_WARN_PCT="${DISK_WARN_PCT:-80}"
DISK_PURGE_PCT="${DISK_PURGE_PCT:-90}"

cd "${ROOT}"

[ -d card ] && find card -type f -name '*.card' -mtime "+${CARD_DAYS}" -delete
[ -d toad ] && find toad -type f -name '*.toad' -mtime "+${TOAD_DAYS}" -delete
[ -d log ]  && find log  -type f -mtime "+${LOG_DAYS}" -delete

USE_PCT="$(df --output=pcent "${ROOT}" | tail -1 | tr -dc '0-9')"
if [ "${USE_PCT}" -ge "${DISK_PURGE_PCT}" ]; then
    logger -t thriftyx-cleanup "disk ${USE_PCT}% >= ${DISK_PURGE_PCT}% — emergency purge oldest .card files"
    # Delete oldest .card files until below warn threshold
    while [ "$(df --output=pcent "${ROOT}" | tail -1 | tr -dc '0-9')" -ge "${DISK_WARN_PCT}" ]; do
        OLDEST="$(find card -type f -name '*.card' -printf '%T@ %p\n' 2>/dev/null \
                  | sort -n | head -1 | awk '{print $2}')"
        [ -z "${OLDEST}" ] && break
        rm -f "${OLDEST}"
    done
elif [ "${USE_PCT}" -ge "${DISK_WARN_PCT}" ]; then
    logger -t thriftyx-cleanup "disk ${USE_PCT}% >= ${DISK_WARN_PCT}% — warning"
fi
