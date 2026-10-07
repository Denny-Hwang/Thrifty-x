#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Repeated Airspy R2 stream diagnostic.
#
# Purpose:
#   Isolate whether intermittent 10 MSPS broadband bursts / drops are caused
#   by R820T2 register control reads during an otherwise continuous Airspy RX
#   stream, or by the 10 MSPS USB/libairspy/usbipd/WSL path itself.
#
# Default conditions (packing ON, same RF chain):
#   A  2.5 MSPS, register reads ON   -- low-bandwidth control
#   B 10.0 MSPS, register reads ON   -- problem condition
#   C 10.0 MSPS, register reads OFF  -- key isolation condition
#
# Each condition is restarted as an independent receiver session. Condition
# order rotates between repetitions to reduce time/order bias.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

RUN=${RUN:-r2_stream_diag_$(date +%Y%m%d_%H%M%S)}
OUTDIR="bench/${RUN}"
REPEATS=${REPEATS:-3}
PAIRS=${PAIRS:-3}
LOW_DBM=${LOW_DBM:--100}
HIGH_DBM=${HIGH_DBM:--90}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-3}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-1}
RX_SETTLE=${RX_SETTLE:-0.5}
FREQ=${FREQ:-161.300M}
TONE=${TONE:-161.315M}
R2_SERIAL=${R2_SERIAL:-0x637862DC2E602DD7}
R2_STAGES=${R2_STAGES:-0/0/8}
R2_BIAS_TEE=${R2_BIAS_TEE:-1}
PACKING=${PACKING:-1}
AMP_GAIN=${AMP_GAIN:-20}
AMP_POWER=${AMP_POWER:-receiver bias tee}
LOSS=${LOSS:-0}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

if (( REPEATS < 1 || PAIRS < 1 )); then
    echo "REPEATS and PAIRS must be >= 1" >&2
    exit 2
fi
case "${R2_BIAS_TEE}" in 0|1) ;; *) echo "R2_BIAS_TEE must be 0 or 1" >&2; exit 2 ;; esac
case "${PACKING}" in 0|1) ;; *) echo "PACKING must be 0 or 1" >&2; exit 2 ;; esac

levels="off"
for ((i=0; i<PAIRS; i++)); do
    levels+=",${LOW_DBM},${HIGH_DBM}"
done
levels+=",off"

mkdir -p "${OUTDIR}"

generator_off() {
    python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" off         >/dev/null 2>&1 || true
}
trap generator_off EXIT

echo "== N9310A preflight =="
python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status
generator_off

echo
echo "=================================================================="
echo " R2 STREAM DIAGNOSTIC"
echo " run             : ${RUN}"
echo " serial          : ${R2_SERIAL}"
echo " center / tone   : ${FREQ} / ${TONE}"
echo " gain stages     : ${R2_STAGES}"
echo " levels          : ${levels} dBm"
echo " repeats         : ${REPEATS}"
echo " pairs/condition : ${PAIRS}"
echo " capture         : ${CAPTURE_SECONDS} s"
echo " generator settle: ${GENERATOR_SETTLE} s"
echo " RX settle       : ${RX_SETTLE} s"
echo " packing         : ${PACKING}"
echo " bias tee        : ${R2_BIAS_TEE}"
echo " amp gain/power  : ${AMP_GAIN} dB / ${AMP_POWER}"
echo
echo " A:  2.5M + register reads"
echo " B: 10.0M + register reads"
echo " C: 10.0M + NO register reads"
echo "=================================================================="

if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "R2-A and the fixed RF chain are connected. Enter=start, q=quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

{
    echo "run=${RUN}"
    echo "date=$(date -Is)"
    echo "host=$(hostname)"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "r2_serial=${R2_SERIAL}"
    echo "center_frequency=${FREQ}"
    echo "tone_frequency=${TONE}"
    echo "stages=${R2_STAGES}"
    echo "levels=${levels}"
    echo "repeats=${REPEATS}"
    echo "pairs_per_condition=${PAIRS}"
    echo "capture_seconds=${CAPTURE_SECONDS}"
    echo "generator_settle_s=${GENERATOR_SETTLE}"
    echo "rx_settle_s=${RX_SETTLE}"
    echo "packing=${PACKING}"
    echo "bias_tee=${R2_BIAS_TEE}"
    echo "amp_gain_db=${AMP_GAIN}"
    echo "amp_power=${AMP_POWER}"
    echo "loss_db=${LOSS}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    echo "airspy_info:"
    airspy_info 2>&1 | sed 's/^/  /' || true
} > "${OUTDIR}/run_info.txt"

packing=()
[[ "${PACKING}" == 1 ]] && packing=(--packing)
bias_tee=()
[[ "${R2_BIAS_TEE}" == 1 ]] && bias_tee=(--bias-tee)

run_condition() {
    local rep=$1
    local label=$2
    local rate=$3
    local skip=$4
    local out="${OUTDIR}/rep${rep}_${label}.csv"
    local log="${OUTDIR}/rep${rep}_${label}.log"
    local skip_arg=()
    [[ "${skip}" == 1 ]] && skip_arg=(--skip-registers)

    echo
    echo "=================================================================="
    echo " REP ${rep}/${REPEATS}  CONDITION ${label}"
    echo " rate=${rate}, register_reads=$([[ "${skip}" == 1 ]] && echo OFF || echo ON)"
    echo " output=${out}"
    echo "=================================================================="

    python scripts/bench_cw_auto.py \
        --generator-resource "${N9310A_RESOURCE}" \
        --generator-settle "${GENERATOR_SETTLE}" \
        --unit R2-A \
        --device airspy_r2 \
        --airspy-serial "${R2_SERIAL}" \
        --rate "${rate}" \
        --freq "${FREQ}" \
        --tone "${TONE}" \
        "--levels=${levels}" \
        --stages "${R2_STAGES}" \
        "${packing[@]}" \
        "${bias_tee[@]}" \
        "${skip_arg[@]}" \
        --seconds "${CAPTURE_SECONDS}" \
        --settle "${RX_SETTLE}" \
        --amp-gain "${AMP_GAIN}" \
        --loss "${LOSS}" \
        --notes "stream_diag rep=${rep} condition=${label}; amp_power=${AMP_POWER}" \
        --out "${out}" 2>&1 | tee "${log}"
}

# Three-condition balanced rotation. Every three repetitions each condition
# occupies first/middle/last position once.
for ((rep=1; rep<=REPEATS; rep++)); do
    case $(((rep - 1) % 3)) in
        0) order=(A_2p5_reg_on B_10m_reg_on C_10m_reg_off) ;;
        1) order=(C_10m_reg_off A_2p5_reg_on B_10m_reg_on) ;;
        2) order=(B_10m_reg_on C_10m_reg_off A_2p5_reg_on) ;;
    esac
    for label in "${order[@]}"; do
        case "${label}" in
            A_2p5_reg_on)  run_condition "${rep}" "${label}" 2.5M 0 ;;
            B_10m_reg_on)  run_condition "${rep}" "${label}" 10M  0 ;;
            C_10m_reg_off) run_condition "${rep}" "${label}" 10M  1 ;;
        esac
    done
done

generator_off

python scripts/bench/r2_stream_diag_summary.py "${OUTDIR}" \
    | tee "${OUTDIR}/summary.md"

echo
echo "DONE"
echo "Run info : ${OUTDIR}/run_info.txt"
echo "CSV/logs : ${OUTDIR}/rep*_*.csv, rep*_*.log"
echo "Combined : ${OUTDIR}/combined.csv"
echo "Summary  : ${OUTDIR}/summary.md"
