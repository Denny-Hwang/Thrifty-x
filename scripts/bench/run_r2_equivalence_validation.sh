#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Publication-oriented Airspy R2 qualification paired with
# run_rtl_equivalence_validation.sh.
# Hardware: N9310A -> same AIS filtered preamp -> Airspy R2.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

RUN=${RUN:-equiv_$(date +%Y%m%d_%H%M%S)}
OUTDIR="bench/${RUN}/r2"
CENTER_HZ=${CENTER_HZ:-161300000}
TONE_OFFSETS=${TONE_OFFSETS:-"-30000 -15000 15000 30000"}
REPEATS=${REPEATS:-3}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-3}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-1}
RX_SETTLE=${RX_SETTLE:-0.5}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
AMP_GAIN=${AMP_GAIN:-20}
AMP_POWER=${AMP_POWER:-"receiver bias tee"}
LOSS=${LOSS:-0}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

R2_SERIAL=${R2_SERIAL:-0x637862DC2E602DD7}
R2_RATES=${R2_RATES:-"2500000 10000000"}
R2_PRIMARY_STAGES=${R2_PRIMARY_STAGES:-"0/0/8"}
R2_BIAS_TEE=${R2_BIAS_TEE:-1}
PACKING=${PACKING:-1}
R2_SKIP_REGISTERS=${R2_SKIP_REGISTERS:-0}

PRIMARY_MIN_DBM=${PRIMARY_MIN_DBM:--120}
PRIMARY_MAX_DBM=${PRIMARY_MAX_DBM:--60}
PRIMARY_STEP_DB=${PRIMARY_STEP_DB:-5}

# Same R820T gain-code points as the RTL runner.
GAINMAP_RTL_GAINS=${GAINMAP_RTL_GAINS:-"0,12.5,20.7,29.7,40.2"}
GAINMAP_STAGES=${GAINMAP_STAGES:-"0/0/8,4/3/8,6/6/8,8/8/8,11/11/8"}
GAINMAP_LEVELS=${GAINMAP_LEVELS:-"off,-115,-110,-105,-100,-95,off"}
GAINMAP_REPEATS=${GAINMAP_REPEATS:-2}
GAINMAP_TONE_OFFSET=${GAINMAP_TONE_OFFSET:-15000}
RUN_GAINMAP=${RUN_GAINMAP:-1}

# R2-only one-factor-at-a-time stage sensitivity.
STAGE_MATRIX=${STAGE_MATRIX:-"0/0/0,0/0/4,0/0/8,0/0/12,0/0/15,4/0/8,8/0/8,12/0/8,14/0/8,0/4/8,0/8/8,0/12/8,0/15/8"}
STAGE_LEVELS=${STAGE_LEVELS:-"off,-105,-95,off"}
STAGE_TONE_OFFSET=${STAGE_TONE_OFFSET:-15000}
RUN_STAGE_SENSITIVITY=${RUN_STAGE_SENSITIVITY:-1}

mkdir -p "${OUTDIR}"

generator_off() {
    python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" off         >/dev/null 2>&1 || true
}
trap generator_off EXIT

csv_join() {
    local IFS=,
    echo "$*"
}

build_primary_levels() {
    local direction=$1
    local vals=()
    local x
    if [[ "${direction}" == asc ]]; then
        while read -r x; do vals+=("${x}"); done < <(
            seq "${PRIMARY_MIN_DBM}" "${PRIMARY_STEP_DB}" "${PRIMARY_MAX_DBM}")
    else
        while read -r x; do vals+=("${x}"); done < <(
            seq "${PRIMARY_MAX_DBM}" "-${PRIMARY_STEP_DB}" "${PRIMARY_MIN_DBM}")
    fi
    echo "off,$(csv_join "${vals[@]}"),off"
}

tone_order_for_rep() {
    local rep=$1
    read -r -a tones <<< "${TONE_OFFSETS}"
    local n=${#tones[@]}
    local start=$(( (rep - 1) % n ))
    local i idx
    for ((i=0; i<n; i++)); do
        idx=$(( (start + i) % n ))
        echo "${tones[idx]}"
    done
}

packing_args=()
if [[ "${PACKING}" == 1 ]]; then
    packing_args=(--packing)
elif [[ "${PACKING}" != 0 ]]; then
    echo "PACKING must be 0 or 1" >&2
    exit 2
fi

bias_args=()
if [[ "${R2_BIAS_TEE}" == 1 ]]; then
    bias_args=(--bias-tee)
elif [[ "${R2_BIAS_TEE}" != 0 ]]; then
    echo "R2_BIAS_TEE must be 0 or 1" >&2
    exit 2
fi

register_args=()
if [[ "${R2_SKIP_REGISTERS}" == 1 ]]; then
    register_args=(--skip-registers)
elif [[ "${R2_SKIP_REGISTERS}" != 0 ]]; then
    echo "R2_SKIP_REGISTERS must be 0 or 1" >&2
    exit 2
fi

echo "== N9310A preflight =="
python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status
generator_off

if ! lsusb | grep -qi '1d50:60a1'; then
    echo "ERROR: Airspy 1d50:60a1 is not visible in WSL." >&2
    exit 2
fi
airspy_info >"${OUTDIR}/airspy_info.txt" 2>&1
if ! grep -qi "${R2_SERIAL#0x}" "${OUTDIR}/airspy_info.txt"; then
    echo "ERROR: expected Airspy serial ${R2_SERIAL} not found." >&2
    cat "${OUTDIR}/airspy_info.txt" >&2
    exit 2
fi

echo
echo "=================================================================="
echo " AIRSPY R2 EQUIVALENCE QUALIFICATION"
echo " run                  : ${RUN}"
echo " output               : ${OUTDIR}"
echo " serial               : ${R2_SERIAL}"
echo " centre               : ${CENTER_HZ} Hz"
echo " tone offsets         : ${TONE_OFFSETS} Hz"
echo " rates                : ${R2_RATES} S/s"
echo " primary stages       : ${R2_PRIMARY_STAGES}"
echo " primary grid         : ${PRIMARY_MIN_DBM}:${PRIMARY_MAX_DBM}:${PRIMARY_STEP_DB} dBm"
echo " primary repeats      : ${REPEATS}"
echo " bias tee / packing   : ${R2_BIAS_TEE} / ${PACKING}"
echo " tuner register reads : $([[ "${R2_SKIP_REGISTERS}" == 1 ]] && echo skipped || echo enabled)"
echo " gain-map stages      : ${GAINMAP_STAGES}"
echo " stage matrix         : ${STAGE_MATRIX}"
echo " capture / gen settle : ${CAPTURE_SECONDS} s / ${GENERATOR_SETTLE} s"
echo " amp nominal / power  : ${AMP_GAIN} dB / ${AMP_POWER}"
echo "=================================================================="

if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "Connect R2 to the unchanged RF chain. N9310A is OFF. Enter=start, q=quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

{
    echo "run=${RUN}"
    echo "receiver=Airspy R2"
    echo "date=$(date -Is)"
    echo "host=$(hostname)"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "serial=${R2_SERIAL}"
    echo "center_hz=${CENTER_HZ}"
    echo "tone_offsets_hz=${TONE_OFFSETS}"
    echo "sample_rates=${R2_RATES}"
    echo "primary_stages=${R2_PRIMARY_STAGES}"
    echo "primary_min_dbm=${PRIMARY_MIN_DBM}"
    echo "primary_max_dbm=${PRIMARY_MAX_DBM}"
    echo "primary_step_db=${PRIMARY_STEP_DB}"
    echo "repeats=${REPEATS}"
    echo "capture_seconds=${CAPTURE_SECONDS}"
    echo "generator_settle_s=${GENERATOR_SETTLE}"
    echo "rx_settle_s=${RX_SETTLE}"
    echo "bias_tee=${R2_BIAS_TEE}"
    echo "packing=${PACKING}"
    echo "register_reads=$([[ "${R2_SKIP_REGISTERS}" == 1 ]] && echo skipped || echo enabled)"
    echo "amp_gain_nominal_db=${AMP_GAIN}"
    echo "amp_power=${AMP_POWER}"
    echo "loss_nominal_db=${LOSS}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    echo "gainmap_rtl_gains=${GAINMAP_RTL_GAINS}"
    echo "gainmap_stages=${GAINMAP_STAGES}"
    echo "gainmap_levels=${GAINMAP_LEVELS}"
    echo "stage_matrix=${STAGE_MATRIX}"
    echo "stage_levels=${STAGE_LEVELS}"
} > "${OUTDIR}/run_info.txt"

python - <<'PY' > "${OUTDIR}/gain_code_map.txt"
from thriftyx.hal.r820t import airspy_equivalent_of_rtl_gain, nearest_rtl_gain
for g in (0, 12.5, 20.7, 29.7, 40.2):
    snapped = nearest_rtl_gain(g) / 10
    print(f"RTL {g:g} dB -> supported {snapped:g} dB -> Airspy {airspy_equivalent_of_rtl_gain(g)}")
PY

# Save modeled RTL-vs-R2 register state for the deployment match at both rates.
: > "${OUTDIR}/register_model_primary.txt"
for rate in ${R2_RATES}; do
    {
        echo "=================================================================="
        echo "R2 rate ${rate}, RTL gain 0 vs R2 0/0/8"
        python scripts/r820t_register_model.py             -f "${CENTER_HZ}"             --rtl-rate 2400000 --rtl-gain 0             --airspy r2 --airspy-rate "${rate}"             --lna 0 --mixer 0 --vga 8
    } >> "${OUTDIR}/register_model_primary.txt" 2>&1 || true
done

echo
echo "== Phase A: deployment-setting transfer function =="
for rate in ${R2_RATES}; do
    for ((rep=1; rep<=REPEATS; rep++)); do
        if (( rep % 2 == 1 )); then direction=asc; else direction=desc; fi
        levels=$(build_primary_levels "${direction}")
        while read -r offset; do
            tone=$((CENTER_HZ + offset))
            sign=p
            (( offset < 0 )) && sign=m
            tag="${sign}${offset#-}"
            out="${OUTDIR}/primary_rate${rate}_rep${rep}_${direction}_tone_${tag}.csv"
            log="${OUTDIR}/primary_rate${rate}_rep${rep}_${direction}_tone_${tag}.log"
            echo
            echo "-- R2 primary rate ${rate}, rep ${rep}/${REPEATS}, ${direction}, offset ${offset} Hz --"
            python scripts/bench_cw_auto.py                 --generator-resource "${N9310A_RESOURCE}"                 --generator-settle "${GENERATOR_SETTLE}"                 --unit R2-A                 --device airspy_r2                 --airspy-serial "${R2_SERIAL}"                 --rate "${rate}"                 --freq "${CENTER_HZ}"                 --tone "${tone}"                 "--levels=${levels}"                 --stages "${R2_PRIMARY_STAGES}"                 "${packing_args[@]}"                 "${bias_args[@]}"                 "${register_args[@]}"                 --seconds "${CAPTURE_SECONDS}"                 --settle "${RX_SETTLE}"                 --amp-gain "${AMP_GAIN}"                 --loss "${LOSS}"                 --notes "equivalence phase=A rate=${rate} rep=${rep} direction=${direction} tone_offset_hz=${offset}; amp_power=${AMP_POWER}"                 --out "${out}" 2>&1 | tee "${log}"
        done < <(tone_order_for_rep "${rep}")
    done
done

if [[ "${RUN_GAINMAP}" == 1 ]]; then
    echo
    echo "== Phase B: matched R820T gain-code map =="
    tone=$((CENTER_HZ + GAINMAP_TONE_OFFSET))
    for rate in ${R2_RATES}; do
        for ((rep=1; rep<=GAINMAP_REPEATS; rep++)); do
            out="${OUTDIR}/gainmap_rate${rate}_rep${rep}.csv"
            log="${OUTDIR}/gainmap_rate${rate}_rep${rep}.log"
            python scripts/bench_cw_auto.py                 --generator-resource "${N9310A_RESOURCE}"                 --generator-settle "${GENERATOR_SETTLE}"                 --unit R2-A                 --device airspy_r2                 --airspy-serial "${R2_SERIAL}"                 --rate "${rate}"                 --freq "${CENTER_HZ}"                 --tone "${tone}"                 "--levels=${GAINMAP_LEVELS}"                 --stages "${GAINMAP_STAGES}"                 "${packing_args[@]}"                 "${bias_args[@]}"                 "${register_args[@]}"                 --seconds "${CAPTURE_SECONDS}"                 --settle "${RX_SETTLE}"                 --amp-gain "${AMP_GAIN}"                 --loss "${LOSS}"                 --notes "equivalence phase=B gainmap rate=${rate} rep=${rep} tone_offset_hz=${GAINMAP_TONE_OFFSET}; amp_power=${AMP_POWER}"                 --out "${out}" 2>&1 | tee "${log}"
        done
    done
fi

if [[ "${RUN_STAGE_SENSITIVITY}" == 1 ]]; then
    echo
    echo "== Phase C: R2 one-factor-at-a-time stage sensitivity =="
    tone=$((CENTER_HZ + STAGE_TONE_OFFSET))
    for rate in ${R2_RATES}; do
        out="${OUTDIR}/stage_sensitivity_rate${rate}.csv"
        log="${OUTDIR}/stage_sensitivity_rate${rate}.log"
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --unit R2-A             --device airspy_r2             --airspy-serial "${R2_SERIAL}"             --rate "${rate}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${STAGE_LEVELS}"             --stages "${STAGE_MATRIX}"             "${packing_args[@]}"             "${bias_args[@]}"             "${register_args[@]}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=C stage-sensitivity rate=${rate} tone_offset_hz=${STAGE_TONE_OFFSET}; amp_power=${AMP_POWER}"             --out "${out}" 2>&1 | tee "${log}"
    done
fi

generator_off

echo
echo "AIRSPY R2 QUALIFICATION COMPLETE"
echo "Raw data : ${OUTDIR}/*.csv"
echo "Logs     : ${OUTDIR}/*.log"
echo "Metadata : ${OUTDIR}/run_info.txt"
echo
echo "If the RTL run used the same RUN name, compare now:"
echo "python scripts/bench/compare_receiver_equivalence.py bench/${RUN}"
