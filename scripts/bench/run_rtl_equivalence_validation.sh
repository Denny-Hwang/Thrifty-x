#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Publication-oriented RTL-SDR qualification for comparison with Airspy R2.
# Hardware: N9310A -> same AIS filtered preamp -> NESDR SMArTee-family RTL.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

RUN=${RUN:-equiv_$(date +%Y%m%d_%H%M%S)}
OUTDIR="bench/${RUN}/rtl"
CENTER_HZ=${CENTER_HZ:-161300000}
TONE_OFFSETS=${TONE_OFFSETS:-"-30000 -15000 15000 30000"}
REPEATS=${REPEATS:-3}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-3}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-1}
RX_SETTLE=${RX_SETTLE:-0.5}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
AMP_GAIN=${AMP_GAIN:-20}
AMP_POWER=${AMP_POWER:-"receiver bias tee (NESDR SMArTee hardware)"}
LOSS=${LOSS:-0}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

RTL_RATE=${RTL_RATE:-2400000}
RTL_PRIMARY_GAIN=${RTL_PRIMARY_GAIN:-0}

PRIMARY_MIN_DBM=${PRIMARY_MIN_DBM:--120}
PRIMARY_MAX_DBM=${PRIMARY_MAX_DBM:--60}
PRIMARY_STEP_DB=${PRIMARY_STEP_DB:-5}

GAINMAP_GAINS=${GAINMAP_GAINS:-"0,12.5,20.7,29.7,40.2"}
GAINMAP_LEVELS=${GAINMAP_LEVELS:-"off,-115,-110,-105,-100,-95,off"}
GAINMAP_REPEATS=${GAINMAP_REPEATS:-2}
GAINMAP_TONE_OFFSET=${GAINMAP_TONE_OFFSET:-15000}
RUN_GAINMAP=${RUN_GAINMAP:-1}

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

echo "== N9310A preflight =="
python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status
generator_off

if ! lsusb | grep -qi '0bda:2838'; then
    echo "ERROR: RTL-SDR 0bda:2838 is not visible in WSL." >&2
    exit 2
fi

rtl_test -t >"${OUTDIR}/rtl_test.txt" 2>&1 || true
if ! grep -qi 'R820' "${OUTDIR}/rtl_test.txt"; then
    echo "ERROR: R820T/R820T2 tuner not confirmed. See ${OUTDIR}/rtl_test.txt" >&2
    exit 2
fi

echo
echo "=================================================================="
echo " RTL EQUIVALENCE QUALIFICATION"
echo " run                  : ${RUN}"
echo " output               : ${OUTDIR}"
echo " centre               : ${CENTER_HZ} Hz"
echo " tone offsets         : ${TONE_OFFSETS} Hz"
echo " primary gain         : manual ${RTL_PRIMARY_GAIN} dB"
echo " sample rate          : ${RTL_RATE} S/s"
echo " primary grid         : ${PRIMARY_MIN_DBM}:${PRIMARY_MAX_DBM}:${PRIMARY_STEP_DB} dBm"
echo " primary repeats      : ${REPEATS}"
echo " gain-map gains       : ${GAINMAP_GAINS} dB"
echo " gain-map levels      : ${GAINMAP_LEVELS}"
echo " capture / gen settle : ${CAPTURE_SECONDS} s / ${GENERATOR_SETTLE} s"
echo " amp nominal / power  : ${AMP_GAIN} dB / ${AMP_POWER}"
echo "=================================================================="

if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "Connect RTL to the unchanged RF chain. N9310A is OFF. Enter=start, q=quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

{
    echo "run=${RUN}"
    echo "receiver=RTL-SDR"
    echo "date=$(date -Is)"
    echo "host=$(hostname)"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "center_hz=${CENTER_HZ}"
    echo "tone_offsets_hz=${TONE_OFFSETS}"
    echo "sample_rate=${RTL_RATE}"
    echo "primary_gain_db=${RTL_PRIMARY_GAIN}"
    echo "primary_min_dbm=${PRIMARY_MIN_DBM}"
    echo "primary_max_dbm=${PRIMARY_MAX_DBM}"
    echo "primary_step_db=${PRIMARY_STEP_DB}"
    echo "repeats=${REPEATS}"
    echo "capture_seconds=${CAPTURE_SECONDS}"
    echo "generator_settle_s=${GENERATOR_SETTLE}"
    echo "rx_settle_s=${RX_SETTLE}"
    echo "amp_gain_nominal_db=${AMP_GAIN}"
    echo "amp_power=${AMP_POWER}"
    echo "loss_nominal_db=${LOSS}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    echo "gainmap_gains_db=${GAINMAP_GAINS}"
    echo "gainmap_levels=${GAINMAP_LEVELS}"
    echo "gainmap_repeats=${GAINMAP_REPEATS}"
} > "${OUTDIR}/run_info.txt"

python - <<'PY' > "${OUTDIR}/gain_code_map.txt"
from thriftyx.hal.r820t import airspy_equivalent_of_rtl_gain, nearest_rtl_gain
for g in (0, 12.5, 20.7, 29.7, 40.2):
    snapped = nearest_rtl_gain(g) / 10
    print(f"RTL {g:g} dB -> supported {snapped:g} dB -> Airspy {airspy_equivalent_of_rtl_gain(g)}")
PY

echo
echo "== Phase A: deployment-setting transfer function =="
for ((rep=1; rep<=REPEATS; rep++)); do
    if (( rep % 2 == 1 )); then direction=asc; else direction=desc; fi
    levels=$(build_primary_levels "${direction}")
    while read -r offset; do
        tone=$((CENTER_HZ + offset))
        sign=p
        (( offset < 0 )) && sign=m
        tag="${sign}${offset#-}"
        out="${OUTDIR}/primary_rep${rep}_${direction}_tone_${tag}.csv"
        log="${OUTDIR}/primary_rep${rep}_${direction}_tone_${tag}.log"
        echo
        echo "-- RTL primary rep ${rep}/${REPEATS}, ${direction}, offset ${offset} Hz --"
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --unit RTL             --device rtlsdr             --rate "${RTL_RATE}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${levels}"             --rtl-gains "${RTL_PRIMARY_GAIN}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=A rep=${rep} direction=${direction} tone_offset_hz=${offset}; amp_power=${AMP_POWER}"             --out "${out}" 2>&1 | tee "${log}"
    done < <(tone_order_for_rep "${rep}")
done

if [[ "${RUN_GAINMAP}" == 1 ]]; then
    echo
    echo "== Phase B: matched R820T gain-code map =="
    tone=$((CENTER_HZ + GAINMAP_TONE_OFFSET))
    for ((rep=1; rep<=GAINMAP_REPEATS; rep++)); do
        out="${OUTDIR}/gainmap_rep${rep}.csv"
        log="${OUTDIR}/gainmap_rep${rep}.log"
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --unit RTL             --device rtlsdr             --rate "${RTL_RATE}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${GAINMAP_LEVELS}"             --rtl-gains "${GAINMAP_GAINS}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=B gainmap rep=${rep} tone_offset_hz=${GAINMAP_TONE_OFFSET}; amp_power=${AMP_POWER}"             --out "${out}" 2>&1 | tee "${log}"
    done
fi

generator_off

echo
echo "RTL QUALIFICATION COMPLETE"
echo "Raw data : ${OUTDIR}/*.csv"
echo "Logs     : ${OUTDIR}/*.log"
echo "Metadata : ${OUTDIR}/run_info.txt"
echo
echo "Now swap ONLY RTL -> Airspy R2 and run the R2 validation with the SAME RUN=${RUN}."
