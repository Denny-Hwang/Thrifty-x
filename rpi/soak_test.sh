#!/bin/bash
# 24h soak test for a Thrifty-X RX node on Raspberry Pi 5.
#
# Runs `thriftyx capture` for SOAK_DURATION_S seconds, samples node
# health every SAMPLE_INTERVAL_S into a CSV, then auto-judges PASS/FAIL
# against documented thresholds.  Designed to be run unattended (nohup,
# tmux, or systemd-run --scope).
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

# Start capture in background
"${HOME_DIR}/.venv/bin/thriftyx" capture "${CARD_FILE}" \
    --config "${CONFIG}" \
    --duration "${SOAK_DURATION_S}" \
    >"${STDOUT_LOG}" 2>"${STDERR_LOG}" &
CAP_PID=$!
echo "${CAP_PID}" > "${RUN_DIR}/pid"
echo "soak: capture pid = ${CAP_PID}"

# Trap: if soak.sh itself is killed, kill capture too
cleanup() {
    if kill -0 "${CAP_PID}" 2>/dev/null; then
        kill -INT "${CAP_PID}" 2>/dev/null || true
        wait "${CAP_PID}" 2>/dev/null || true
    fi
}
trap cleanup INT TERM

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
START_TS=$(date +%s)
END_TS=$((START_TS + SOAK_DURATION_S + 30))   # +30s grace

while [ "$(date +%s)" -lt "${END_TS}" ]; do
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
    sleep "${SAMPLE_INTERVAL_S}"
done

wait "${CAP_PID}"
CAP_RC=$?
echo "soak: capture exit code = ${CAP_RC}"

# ---------- Auto-judge ----------
fail=0
reasons=()

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
MEM_GROWTH=$(awk -F, '
    NR==1 {next}
    $3!="" {n++; v[n]=$3+0}
    END {
        if (n < 20) {print "0"; exit}
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

# ---------- Summary ----------
{
    echo "Thrifty-X 24h soak summary"
    echo "rxid=${RXID} run=${STAMP}"
    echo "duration_s=${SOAK_DURATION_S} sample_s=${SAMPLE_INTERVAL_S}"
    echo "capture_exit_code=${CAP_RC}"
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
} | tee "${SUMMARY}"

exit "${fail}"
