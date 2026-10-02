#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Controlled CW comparison that archives every Thrifty-X .card block.
# Derived from the uploaded 01_run_cw_carrier_compare.sh workflow, with
# automatic N9310A control. The operator reviews settings and swaps only
# the receiver at the fixed RF-chain endpoint.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[[ -n "${REPO}" ]] || { echo "ERROR: run inside Thrifty-x or set REPO" >&2; exit 2; }
cd "${REPO}"
if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

for cmd in python thriftyx rtl_sdr; do
    command -v "${cmd}" >/dev/null || { echo "ERROR: ${cmd} not found" >&2; exit 2; }
done

CENTER_HZ=${CENTER_HZ:-161300000}
CW_HZ=${CW_HZ:-161315000}
LEVELS=${LEVELS:--90 -85 -80 -75 -70 -65 -60}
DURATION=${DURATION:-10}
CARRIER_WINDOW=${CARRIER_WINDOW:-10 - 20 kHz}
CAPTURE_SKIP=${CAPTURE_SKIP:-100}
RTL_RATE=${RTL_RATE:-2400000}
RTL_DEVICE_INDEX=${RTL_DEVICE_INDEX:-0}

R2_SERIAL=${R2_SERIAL:-0x637862DC2E602DD7}
R2_BIAS_TEE=${R2_BIAS_TEE:-true}
R2_PACKING=${R2_PACKING:-true}
R2_LNA=${R2_LNA:-0}
R2_MIXER=${R2_MIXER:-0}
R2_VGA=${R2_VGA:-8}
R2_RATES=${R2_RATES:-2500000 10000000}

N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-0.25}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

RUN=${RUN:-cw_card_rtl_r2_$(date +%Y%m%d_%H%M%S)}
OUT=${OUT:-bench/${RUN}}
SUMMARY="${OUT}/cw_summary.csv"

[[ "${DURATION}" =~ ^[0-9]+$ ]] || { echo "ERROR: DURATION must be integer" >&2; exit 2; }
[[ ! -e "${OUT}" ]] || { echo "ERROR: output already exists: ${OUT}" >&2; exit 2; }

sg() { python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" "$@"; }
sg_off() { sg off >/dev/null 2>&1 || true; }
trap sg_off EXIT

echo "== N9310A preflight =="
sg status
sg_off

echo
echo "============================================================"
echo " FORCED-.card CW COMPARISON PLAN"
echo " N9310A tone : ${CW_HZ} Hz"
echo " RX centre   : ${CENTER_HZ} Hz"
echo " levels      : ${LEVELS} dBm"
echo " RTL rate    : ${RTL_RATE}"
echo " R2 rates    : ${R2_RATES}"
echo " R2 gain     : L${R2_LNA}/M${R2_MIXER}/V${R2_VGA}"
echo " duration    : ${DURATION} s"
echo " output      : ${OUT}"
echo "============================================================"
if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "Review settings. Enter = start, q = quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

mkdir -p "${OUT}"

ready() {
    local prompt=$1 ans
    read -r -p "${prompt} [Enter=continue, q=quit] " ans || true
    [[ "${ans,,}" == q ]] && exit 0
}

geom() {
    python - "$1" <<'PY'
import sys
from thriftyx.settings import compute_block_params
rate = float(sys.argv[1])
bs, h, t = compute_block_params(rate, 0.999707e6)
print(bs, h, t)
PY
}

write_rtl_cfg() {
    local cfg=$1 rate=$2 bs h t
    read -r bs h t < <(geom "${rate}")
    cat > "${cfg}" <<EOF
rxid:               0
device_type:        rtlsdr
bit_depth:          8
sample_rate:        ${rate}
tuner_freq:         ${CENTER_HZ}
capture_skip:       ${CAPTURE_SKIP}
block_size:         ${bs}
block_history:      ${h}
carrier_window:     ${CARRIER_WINDOW}
carrier_threshold:  0
tuner_gain:         0
EOF
}

write_r2_cfg() {
    local cfg=$1 rate=$2 bs h t
    read -r bs h t < <(geom "${rate}")
    cat > "${cfg}" <<EOF
rxid:               0
device_type:        airspy_r2
bit_depth:          12
sample_rate:        ${rate}
tuner_freq:         ${CENTER_HZ}
capture_skip:       ${CAPTURE_SKIP}
block_size:         ${bs}
block_history:      ${h}
carrier_window:     ${CARRIER_WINDOW}
carrier_threshold:  0
airspy_serial:      ${R2_SERIAL}
gain_mode:          manual
lna_gain:           ${R2_LNA}
mixer_gain:         ${R2_MIXER}
vga_gain:           ${R2_VGA}
lna_agc:            false
mixer_agc:          false
bias_tee:           ${R2_BIAS_TEE}
ppm:                0
packing:            ${R2_PACKING}
EOF
}

analyze_card() {
    local label=$1 level=$2 card=$3 dir=$4
    python "${SCRIPT_DIR}/cw_card_metrics.py" "${card}" \
        --center-hz "${CENTER_HZ}" --expected-hz "${CW_HZ}" \
        --search-lo-hz 10000 --search-hi-hz 20000 \
        --label "${label}" --generator-dbm "${level}" \
        --csv "${SUMMARY}" | tee "${dir}/metrics.txt"
}

set_level() {
    local level=$1
    sg set --frequency "${CW_HZ}" --power "${level}" --rf-on >/dev/null
    sleep "${GENERATOR_SETTLE}"
    echo "N9310A -> ${CW_HZ} Hz, ${level} dBm, RF ON"
}

capture_rtl() {
    local level=$1 label="RTL_2p4M_g0_${level}dBm"
    local dir="${OUT}/${label}" cfg card nsamp
    mkdir -p "${dir}"
    cfg="${dir}/capture.cfg"; card="${dir}/capture.card"
    write_rtl_cfg "${cfg}" "${RTL_RATE}"
    set_level "${level}"
    nsamp=$((RTL_RATE * DURATION))
    rtl_sdr -d "${RTL_DEVICE_INDEX}" -f "${CENTER_HZ}" -s "${RTL_RATE}" \
        -g 0.1 -n "${nsamp}" - \
        2> >(tee "${dir}/rtl_sdr.log" >&2) \
      | thriftyx capture "${card}" --input - -c "${cfg}" \
        2> >(tee "${dir}/capture.log" >&2)
    grep -q 'Tuner gain set to 0.00 dB' "${dir}/rtl_sdr.log" || {
        echo "ERROR: RTL manual 0.00 dB was not verified" >&2
        exit 3
    }
    analyze_card "${label}" "${level}" "${card}" "${dir}"
}

capture_r2() {
    local rate=$1 level=$2 tag label dir cfg card
    if [[ "${rate}" == 2500000 ]]; then tag=2p5M; else tag="$((rate / 1000000))M"; fi
    label="R2_${tag}_L${R2_LNA}M${R2_MIXER}V${R2_VGA}_${level}dBm"
    dir="${OUT}/${label}"; mkdir -p "${dir}"
    cfg="${dir}/capture.cfg"; card="${dir}/capture.card"
    write_r2_cfg "${cfg}" "${rate}"
    thriftyx capture "${card}" --duration "${DURATION}" -c "${cfg}" \
        2> >(tee "${dir}/capture.log" >&2)
    analyze_card "${label}" "${level}" "${card}" "${dir}"
}

{
    echo "experiment=controlled CW forced-card RTL/R2 comparison"
    echo "created=$(date -Iseconds)"
    echo "git_sha=$(git rev-parse HEAD)"
    echo "center_hz=${CENTER_HZ}"
    echo "cw_hz=${CW_HZ}"
    echo "levels_dbm=${LEVELS}"
    echo "duration_s=${DURATION}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    sg status | sed 's/^/n9310a: /'
    echo "capture_threshold=0 (force all blocks; intentional)"
    echo "rtl_rate=${RTL_RATE}"
    echo "rtl_gain=manual g0 via rtl_sdr -g 0.1 -> verified 0.00 dB"
    echo "r2_serial=${R2_SERIAL}"
    echo "r2_rates=${R2_RATES}"
    echo "r2_gain=LNA ${R2_LNA} / Mixer ${R2_MIXER} / VGA ${R2_VGA}"
} > "${OUT}/manifest.txt"

sg_off
ready "Connect RTL-SDR to the SAME RF chain; N9310A is OFF"
for level in ${LEVELS}; do capture_rtl "${level}"; done

sg_off
echo
echo "============================================================"
echo " RECEIVER SWAP: RTL -> Airspy R2"
echo " Disconnect only the receiver end. Keep the RF chain fixed."
echo "============================================================"
ready "R2 ${R2_SERIAL} connected to the SAME RF chain"

for level in ${LEVELS}; do
    set_level "${level}"
    for rate in ${R2_RATES}; do capture_r2 "${rate}" "${level}"; done
done

sg_off
echo
echo "DONE"
echo "Summary : ${SUMMARY}"
echo "Manifest: ${OUT}/manifest.txt"
