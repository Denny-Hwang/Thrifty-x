#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# End-to-end RTL-SDR vs Airspy R2 CW bench. In the default automatic
# mode the N9310A is controlled over USB, so the operator only reviews
# the plan and swaps the receiver at the fixed RF-chain endpoint.
#
# Main environment settings:
#   GENERATOR_MODE [auto]     auto | manual
#   N9310A_RESOURCE [auto]    PyVISA resource or auto discovery
#   TONE [161.315M]           N9310A CW frequency
#   LEVELS [off,-125:-55:5]   generator levels, dBm
#   AMP_GAIN [20]             external amplifier gain, dB (logged)
#   LOSS [0]                  cable/attenuator loss, dB (logged)
#   CAPTURE_SECONDS [5]
#   FREQ [161.3M]             receiver tuned centre
#   UNITS [RTL R2-A R2-B]     subset/order; swap hardware only at prompts
#   R2_RATES [10M]            one or more rates, e.g. "2.5M 10M"
#   R2_STAGES [0/0/0,0/0/8,0/0/10,0/0/11]
#   PACKING [1]
#   R2_BIAS_TEE [0]
#   RUN [run1]
#   CONFIRM_SETTINGS [1]      0 for unattended/scripted use
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

AMP_GAIN=${AMP_GAIN:-20}
LOSS=${LOSS:-0}
LEVELS=${LEVELS:-off,-125:-55:5}
SECONDS_PER=${CAPTURE_SECONDS:-5}
FREQ=${FREQ:-161.3M}
TONE=${TONE:-161.315M}
R2_A_SERIAL=${R2_A_SERIAL:-0x637862DC2E602DD7}
R2_B_SERIAL=${R2_B_SERIAL:-0xB01861DC393A891F}
R2_STAGES=${R2_STAGES:-0/0/0,0/0/8,0/0/10,0/0/11}
R2_RATES=${R2_RATES:-${R2_RATE:-10M}}
PACKING=${PACKING:-1}
R2_BIAS_TEE=${R2_BIAS_TEE:-0}
AMP_POWER=${AMP_POWER:-external}
RUN=${RUN:-run1}
UNITS=${UNITS:-RTL R2-A R2-B}
GENERATOR_MODE=${GENERATOR_MODE:-auto}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-0.25}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

case "${GENERATOR_MODE}" in
    auto|manual) ;;
    *) echo "GENERATOR_MODE must be auto or manual" >&2; exit 2 ;;
esac

if [[ -z "${REF:-}" ]]; then
    case " ${UNITS} " in
        *" RTL "*) REF=RTL:g0 ;;
        *) first=(${UNITS}); REF="${first[0]}:${R2_STAGES%%,*}" ;;
    esac
fi
OUT="bench/${RUN}/results.csv"

for cmd in python git; do
    command -v "${cmd}" >/dev/null || {
        echo "ERROR: ${cmd} not found; activate the Thrifty-x venv" >&2
        exit 2
    }
done

packing=()
[[ "${PACKING}" == 1 ]] && packing=(--packing)
bias_tee=()
if [[ "${R2_BIAS_TEE}" == 1 ]]; then
    bias_tee=(--bias-tee)
    echo "WARNING: Airspy bias tee ENABLED; verify the RF chain is DC-safe."
elif [[ "${R2_BIAS_TEE}" != 0 ]]; then
    echo "R2_BIAS_TEE must be 0 or 1" >&2
    exit 2
fi

generator_off() {
    if [[ "${GENERATOR_MODE}" == auto ]]; then
        python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" off \
            >/dev/null 2>&1 || true
    fi
}
cleanup() {
    generator_off
}
trap cleanup EXIT

if [[ "${GENERATOR_MODE}" == auto ]]; then
    echo "== N9310A preflight =="
    python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status
    generator_off
fi

echo
echo "=================================================================="
echo " CW BENCH PLAN"
echo " generator mode : ${GENERATOR_MODE}"
echo " N9310A resource: ${N9310A_RESOURCE}"
echo " tone            : ${TONE}"
echo " RX centre       : ${FREQ}"
echo " levels          : ${LEVELS} dBm"
echo " units/order     : ${UNITS}"
echo " R2 rates        : ${R2_RATES}"
echo " R2 stages       : ${R2_STAGES}"
echo " capture/setting : ${SECONDS_PER} s"
echo " amp gain / loss : ${AMP_GAIN} / ${LOSS} dB"
echo " output          : ${OUT}"
echo "=================================================================="
if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "Review the settings above. Enter = start, q = quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

mkdir -p "bench/${RUN}"
{
    echo "run=${RUN}"
    echo "date=$(date -Is)"
    echo "host=$(hostname)"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "receiver_frequency=${FREQ}"
    echo "tone_frequency=${TONE}"
    echo "generator_mode=${GENERATOR_MODE}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    echo "generator_levels=${LEVELS}"
    echo "generator_settle_s=${GENERATOR_SETTLE}"
    echo "capture_seconds=${SECONDS_PER}"
    echo "external_amplifier_nominal_gain_db=${AMP_GAIN}"
    echo "external_amplifier_power=${AMP_POWER}"
    echo "loss_db=${LOSS}"
    echo "airspy_sample_rates=${R2_RATES}"
    echo "airspy_packing=${PACKING}"
    echo "airspy_bias_tee=${R2_BIAS_TEE}"
    echo "r2_gain_stages=${R2_STAGES}"
    echo "r2_a_serial=${R2_A_SERIAL}"
    echo "r2_b_serial=${R2_B_SERIAL}"
    echo "rtl_bias_tee=off (not controlled by this script)"
    echo "librtlsdr=$(ldconfig -p 2>/dev/null | grep -m1 'librtlsdr.so' | awk '{print $NF}')"
    if [[ "${GENERATOR_MODE}" == auto ]]; then
        python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status \
            | sed 's/^/n9310a_preflight: /'
    fi
} | tee "bench/${RUN}/run_info.txt"

common=(--freq "${FREQ}" --tone "${TONE}" --levels "${LEVELS}"
        --amp-gain "${AMP_GAIN}" --loss "${LOSS}"
        --seconds "${SECONDS_PER}" --out "${OUT}")

auto_generator=(--generator-resource "${N9310A_RESOURCE}"
                --generator-settle "${GENERATOR_SETTLE}")

run_sweep() {
    if [[ "${GENERATOR_MODE}" == auto ]]; then
        python scripts/bench_cw_auto.py "${auto_generator[@]}" "$@"
    else
        python scripts/bench_cw_level.py sweep "$@"
    fi
}

for unit in ${UNITS}; do
    generator_off
    echo
    echo "=================================================================="
    echo " RECEIVER SWAP: connect ${unit} to the SAME fixed RF chain."
    echo " N9310A RF is OFF. Do not move the antenna/preamp/cables."
    echo "=================================================================="
    read -r -p "Enter when ${unit} is connected (s = skip, q = quit): " answer
    case "${answer,,}" in
        s) continue ;;
        q) exit 0 ;;
    esac

    case "${unit}" in
        RTL)
            run_sweep --unit RTL --device rtlsdr --rtl-gains 0 \
                "${common[@]}"
            ;;
        R2-A|R2-B)
            serial=${R2_A_SERIAL}
            [[ "${unit}" == R2-B ]] && serial=${R2_B_SERIAL}
            [[ -z "${serial}" ]] && {
                echo "No serial configured for ${unit}; skipped"
                continue
            }
            for rate in ${R2_RATES}; do
                echo "-- ${unit}: automatic sweep at ${rate} --"
                run_sweep --unit "${unit}" --device airspy_r2 \
                    --airspy-serial "${serial}" --rate "${rate}" \
                    --stages "${R2_STAGES}" "${packing[@]}" \
                    "${bias_tee[@]}" "${common[@]}"
            done
            ;;
        *)
            echo "Unknown unit ${unit}" >&2
            exit 2
            ;;
    esac
done

generator_off
echo
python scripts/bench_cw_level.py report "${OUT}" --ref "${REF}" \
    --plot "bench/${RUN}/cw_levels.png" | tee "bench/${RUN}/report.md"
echo
echo "DONE"
echo "Results : ${OUT}"
echo "Run info: bench/${RUN}/run_info.txt"
echo "Report  : bench/${RUN}/report.md"
echo "Plot    : bench/${RUN}/cw_levels.png"
