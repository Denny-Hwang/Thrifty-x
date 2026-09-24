#!/bin/bash
# 24h soak test for a Thrifty-X RX node on Raspberry Pi 5.
#
# Runs `thriftyx capture` for SOAK_DURATION_S seconds, samples node
# health every SAMPLE_INTERVAL_S into a CSV, then auto-judges PASS/FAIL
# against documented thresholds.  Designed to be run unattended (nohup,
# tmux, or systemd-run --scope).  A soak that does not run its full
# duration -- the script interrupted (Ctrl-C, kill, the scope stopped,
# a hangup without nohup) or capture ending early, even with exit
# status 0 -- is a FAIL.
#
# Usage:
#   sudo systemctl stop thriftyx-capture@rx0
#   ./rpi/soak_test.sh                 # uses defaults
#   THRIFTYX_CONFIG=/path/to.cfg ./rpi/soak_test.sh
#
# Exit codes:
#   0   PASS
#   1   FAIL (see judgment section in summary.txt)
#   2   setup error (config missing, can't start capture, etc.)

set -uo pipefail

# ---------- Tunables ----------
SOAK_DURATION_S="${SOAK_DURATION_S:-86400}"           # 24 h
SAMPLE_INTERVAL_S="${SAMPLE_INTERVAL_S:-60}"
# Capture must run at least SOAK_DURATION_S - SOAK_TOLERANCE_S seconds.
SOAK_TOLERANCE_S="${SOAK_TOLERANCE_S:-60}"
RXID="${THRIFTYX_RXID:-0}"
HOME_DIR="${THRIFTYX_HOME:-$HOME/thrifty-x}"
OUT_ROOT="${THRIFTYX_OUT:-/var/lib/thriftyx}"
CONFIG="${THRIFTYX_CONFIG:-$OUT_ROOT/capture.cfg}"
VENV_PY="${HOME_DIR}/.venv/bin/python"

# Pass/fail thresholds
MAX_THROTTLED_BITS="${MAX_THROTTLED_BITS:-0x0}"        # only 0x0 passes
MAX_TEMP_C="${MAX_TEMP_C:-80}"                          # peak CPU temp
MAX_MEM_GROWTH_PCT="${MAX_MEM_GROWTH_PCT:-10}"          # RSS end vs early
MIN_DISK_FREE_PCT="${MIN_DISK_FREE_PCT:-10}"            # always >= 10% free
MAX_DISK_GROWTH_MB="${MAX_DISK_GROWTH_MB:-0}"           # 0 = no upper bound
# Detected blocks the card must hold: the soak runs with the site's
# transmitters on air, so none means the receive chain is not working.
# 0 soaks without transmitters.
MIN_CARD_BLOCKS="${MIN_CARD_BLOCKS:-1}"
# ------------------------------

if [ ! -f "${CONFIG}" ]; then
    echo "soak: config not found: ${CONFIG}" >&2
    exit 2
fi
if [ ! -x "${VENV_PY}" ]; then
    echo "soak: venv python not found at ${VENV_PY}" >&2
    exit 2
fi

STAMP="$(date +%Y%m%dT%H%M%S)"
RUN_DIR="${OUT_ROOT}/soak/${STAMP}"
mkdir -p "${RUN_DIR}"
CARD_FILE="${RUN_DIR}/capture.card"
STDOUT_LOG="${RUN_DIR}/stdout.log"
STDERR_LOG="${RUN_DIR}/stderr.log"
SAMPLES_CSV="${RUN_DIR}/samples.csv"
SUMMARY="${RUN_DIR}/summary.txt"

echo "soak: run dir = ${RUN_DIR}"
echo "soak: duration = ${SOAK_DURATION_S}s, sample = ${SAMPLE_INTERVAL_S}s"

# Seconds since boot: elapsed time immune to clock steps (chrony).
_now() { awk '{print int($1)}' /proc/uptime 2>/dev/null || date +%s; }

# If the script is interrupted, stop capture and judge the run a FAIL
# (capture exits 0 on SIGINT/SIGTERM, so its status does not show it).
INTERRUPTED=""
CAP_PID=""
# shellcheck disable=SC2329  # invoked by the traps below
on_signal() {
    INTERRUPTED="$1"
    kill -INT "${CAP_PID}" 2>/dev/null || true
}
trap 'on_signal SIGINT' INT
trap 'on_signal SIGTERM' TERM
trap 'on_signal SIGHUP' HUP

# Start capture in background
START_TS=$(_now)
"${HOME_DIR}/.venv/bin/thriftyx" capture "${CARD_FILE}" \
    --config "${CONFIG}" \
    --duration "${SOAK_DURATION_S}" \
    >"${STDOUT_LOG}" 2>"${STDERR_LOG}" &
CAP_PID=$!
echo "${CAP_PID}" > "${RUN_DIR}/pid"
echo "soak: capture pid = ${CAP_PID}"

# CSV header
echo "ts,uptime_s,rss_kb,cpu_temp_c,throttled,disk_used_pct,card_size_b" \
    > "${SAMPLES_CSV}"

_get_temp() {
    if command -v vcgencmd >/dev/null 2>&1; then
        vcgencmd measure_temp 2>/dev/null \
            | sed -E "s/temp=([0-9.]+).*/\1/" || echo ""
    elif [ -r /sys/class/thermal/thermal_zone0/temp ]; then
        awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp
    else
        echo ""
    fi
}
_get_throttled() {
    if command -v vcgencmd >/dev/null 2>&1; then
        vcgencmd get_throttled 2>/dev/null \
            | sed -E "s/^throttled=//" || echo ""
    else
        echo ""
    fi
}

# Sampling loop
END_TS=$((START_TS + SOAK_DURATION_S + 30))   # +30s grace

while [ "$(_now)" -lt "${END_TS}" ] && [ -z "${INTERRUPTED}" ]; do
    if ! kill -0 "${CAP_PID}" 2>/dev/null; then
        break    # capture exited
    fi
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    UP=$(awk '{print int($1)}' /proc/uptime)
    RSS=$(awk '/VmRSS/{print $2}' /proc/${CAP_PID}/status 2>/dev/null || echo "")
    TEMP=$(_get_temp)
    THR=$(_get_throttled)
    DUSE=$(df --output=pcent "${OUT_ROOT}" 2>/dev/null | tail -1 | tr -dc '0-9')
    CSIZE=$(stat -c%s "${CARD_FILE}" 2>/dev/null || echo 0)
    echo "${NOW},${UP},${RSS},${TEMP},${THR},${DUSE},${CSIZE}" >> "${SAMPLES_CSV}"
    # In the background: a signal's trap runs at once, not after sleep.
    sleep "${SAMPLE_INTERVAL_S}" &
    wait $!
done

# A trapped signal also cuts `wait` short (status > 128) while capture
# is still stopping: wait again for its real exit status.
while :; do
    wait "${CAP_PID}"
    CAP_RC=$?
    if [ "${CAP_RC}" -gt 128 ] && kill -0 "${CAP_PID}" 2>/dev/null; then
        continue
    fi
    break
done
ELAPSED=$(( $(_now) - START_TS ))
echo "soak: capture exit code = ${CAP_RC} after ${ELAPSED}s"

# ---------- Auto-judge ----------
fail=0
reasons=()

# 0. the soak ran its full duration
if [ -n "${INTERRUPTED}" ]; then
    fail=1; reasons+=("soak interrupted by ${INTERRUPTED} after ${ELAPSED}s")
fi
if [ "${ELAPSED}" -lt $((SOAK_DURATION_S - SOAK_TOLERANCE_S)) ]; then
    fail=1
    reasons+=("capture ran ${ELAPSED}s of the ${SOAK_DURATION_S}s soak")
fi

# 1. capture exit code
if [ "${CAP_RC}" -ne 0 ]; then
    fail=1; reasons+=("capture exit code = ${CAP_RC}")
fi

# 2. throttled — any non-zero across the run is a fail
if grep -E ',0x[0-9A-Fa-f]+,' "${SAMPLES_CSV}" \
        | awk -F, '$5 != "" && $5 != "'"${MAX_THROTTLED_BITS}"'" {found=1} END{exit !found}'; then
    fail=1; reasons+=("throttled flags observed (see samples.csv col 5)")
fi

# 3. peak temp
PEAK_TEMP=$(awk -F, 'NR>1 && $4!="" {if ($4+0 > m) m=$4+0} END{print m+0}' "${SAMPLES_CSV}")
if awk -v p="${PEAK_TEMP}" -v m="${MAX_TEMP_C}" 'BEGIN{exit !(p > m)}'; then
    fail=1; reasons+=("peak CPU temp ${PEAK_TEMP}°C > ${MAX_TEMP_C}°C")
fi

# 4. memory growth — compare median of first 10 samples vs last 10
# (n/a with fewer than 20 samples: not judged, and the summary says so)
MEM_GROWTH=$(awk -F, '
    NR==1 {next}
    $3!="" {n++; v[n]=$3+0}
    END {
        if (n < 20) {print "n/a"; exit}
        head=0; for (i=1;i<=10;i++) head+=v[i]; head/=10
        tail=0; for (i=n-9;i<=n;i++) tail+=v[i]; tail/=10
        if (head==0) {print "0"; exit}
        printf "%.2f", (tail-head)*100/head
    }' "${SAMPLES_CSV}")
if awk -v g="${MEM_GROWTH}" -v m="${MAX_MEM_GROWTH_PCT}" \
        'BEGIN{exit !(g+0 > m+0)}'; then
    fail=1; reasons+=("RSS growth ${MEM_GROWTH}% > ${MAX_MEM_GROWTH_PCT}%")
fi

# 5. disk free
WORST_USED=$(awk -F, 'NR>1 && $6!="" {if ($6+0 > m) m=$6+0} END{print m+0}' "${SAMPLES_CSV}")
WORST_FREE=$((100 - WORST_USED))
if [ "${WORST_FREE}" -lt "${MIN_DISK_FREE_PCT}" ]; then
    fail=1; reasons+=("worst disk free ${WORST_FREE}% < ${MIN_DISK_FREE_PCT}%")
fi

# 5b. capture output growth cap (0 = no upper bound)
CARD_MB=$(( $(stat -c%s "${CARD_FILE}" 2>/dev/null || echo 0) / 1048576 ))
if [ "${MAX_DISK_GROWTH_MB}" -gt 0 ] \
        && [ "${CARD_MB}" -gt "${MAX_DISK_GROWTH_MB}" ]; then
    fail=1; reasons+=("capture output ${CARD_MB} MB > ${MAX_DISK_GROWTH_MB} MB")
fi

# 6. card file integrity — header is "#v2" for Airspy or first line non-empty
if [ ! -s "${CARD_FILE}" ]; then
    fail=1; reasons+=("card file empty or missing: ${CARD_FILE}")
else
    HEAD=$(head -c 3 "${CARD_FILE}")
    if [ "${HEAD}" != "#v2" ] \
            && ! head -1 "${CARD_FILE}" | grep -qE '^[0-9.]+ [0-9]+ '; then
        fail=1; reasons+=("card file header looks corrupt")
    fi
fi

# 7. detections: a card holding only its header detected nothing
if [ "${MIN_CARD_BLOCKS}" -gt 0 ] && [ -s "${CARD_FILE}" ]; then
    CARD_BLOCKS=$(grep -c -m "${MIN_CARD_BLOCKS}" -v -e '^#' -e '^$' \
                  "${CARD_FILE}")
    if [ "${CARD_BLOCKS}" -lt "${MIN_CARD_BLOCKS}" ]; then
        fail=1
        r="card holds ${CARD_BLOCKS} detected block(s) < MIN_CARD_BLOCKS"
        reasons+=("${r}=${MIN_CARD_BLOCKS} (transmitters on air?)")
    fi
fi

# ---------- Summary ----------
# Written to the file first: a vanished terminal must not cut it short.
{
    echo "Thrifty-X 24h soak summary"
    echo "rxid=${RXID} run=${STAMP}"
    echo "duration_s=${SOAK_DURATION_S} sample_s=${SAMPLE_INTERVAL_S}"
    echo "elapsed_s=${ELAPSED}"
    [ -z "${INTERRUPTED}" ] || echo "interrupted_by=${INTERRUPTED}"
    echo "capture_exit_code=${CAP_RC}"
    if ! command -v vcgencmd >/dev/null 2>&1; then
        # Without vcgencmd the throttle check can never fail — say so
        # instead of letting an unmonitored signal look like a pass.
        echo "WARNING: vcgencmd not available — throttle flags were NOT monitored"
    fi
    if [ "${MEM_GROWTH}" = "n/a" ]; then
        echo "WARNING: fewer than 20 RSS samples — memory growth was NOT judged"
    fi
    echo "peak_cpu_temp_c=${PEAK_TEMP}"
    echo "rss_growth_pct=${MEM_GROWTH}"
    echo "worst_disk_used_pct=${WORST_USED}"
    echo "card_size_b=$(stat -c%s "${CARD_FILE}" 2>/dev/null || echo 0)"
    echo
    if [ "${fail}" -eq 0 ]; then
        echo "RESULT: PASS"
    else
        echo "RESULT: FAIL"
        for r in "${reasons[@]}"; do echo "  - ${r}"; done
    fi
} > "${SUMMARY}"
cat "${SUMMARY}"

exit "${fail}"
