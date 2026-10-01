#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# External over-the-air Thrifty TX comparison. This is the repository version
# of the uploaded 02_run_external_tx_compare.sh workflow. The external TX and
# RF chain remain fixed; the operator swaps only RTL-SDR -> Airspy R2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[[ -n "${REPO}" ]] || { echo "ERROR: run inside Thrifty-x or set REPO" >&2; exit 2; }
cd "${REPO}"
if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

CENTER_HZ=${CENTER_HZ:-161300000}
TX_FREQ_HZ=${TX_FREQ_HZ:-161315450}
CHIP_RATE_HZ=${CHIP_RATE_HZ:-999707}
CARRIER_WINDOW=${CARRIER_WINDOW:-10 - 20 kHz}
CARRIER_THRESHOLD=${CARRIER_THRESHOLD:-15 * snr}
CORR_THRESHOLD=${CORR_THRESHOLD:-15 * snr}
EXTRACT_CORR_THRESHOLD=${EXTRACT_CORR_THRESHOLD:-10 * snr}
CAPTURE_SKIP=${CAPTURE_SKIP:-100}
DURATION=${DURATION:-600}
ANALYZE_MAX=${ANALYZE_MAX:-5}
RUN_FORCED_DIAG=${RUN_FORCED_DIAG:-true}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

RTL_RATE=${RTL_RATE:-2400000}
RTL_DEVICE_INDEX=${RTL_DEVICE_INDEX:-0}
R2_SERIAL=${R2_SERIAL:-0x637862DC2E602DD7}
R2_BIAS_TEE=${R2_BIAS_TEE:-true}
R2_PACKING=${R2_PACKING:-true}
R2_LNA=${R2_LNA:-0}
R2_MIXER=${R2_MIXER:-0}
R2_VGA=${R2_VGA:-8}
R2_RATE_LOW=${R2_RATE_LOW:-2500000}
R2_RATE_HIGH=${R2_RATE_HIGH:-10000000}

TX_DISTANCE_M=${TX_DISTANCE_M:-}
TX_HEIGHT_M=${TX_HEIGHT_M:-}
RX_HEIGHT_M=${RX_HEIGHT_M:-}
TX_ANTENNA_NOTE=${TX_ANTENNA_NOTE:-}
RX_ANTENNA_NOTE=${RX_ANTENNA_NOTE:-}
PREAMP_NOTE=${PREAMP_NOTE:-}

RUN=${RUN:-ota_rtl_r2_$(date +%Y%m%d_%H%M%S)}
OUT=${OUT:-bench/${RUN}}
SUMMARY="${OUT}/detection_summary.csv"
EXPECTED_OFFSET_HZ=$((TX_FREQ_HZ - CENTER_HZ))

[[ "${DURATION}" =~ ^[0-9]+$ ]] || { echo "ERROR: DURATION must be integer" >&2; exit 2; }
[[ ! -e "${OUT}" ]] || { echo "ERROR: output already exists: ${OUT}" >&2; exit 2; }
mkdir -p "${OUT}"

ready() {
    local prompt=$1 ans
    read -r -p "${prompt} [Enter=continue, q=quit] " ans || true
    [[ "${ans,,}" == q ]] && exit 0
}

geom() {
    python - "$1" "${CHIP_RATE_HZ}" <<'PY'
import sys
from thriftyx.settings import compute_block_params
bs, h, t = compute_block_params(float(sys.argv[1]), float(sys.argv[2]))
print(bs, h, t)
PY
}

write_capture_cfg() {
    local cfg=$1 dev=$2 rate=$3 bs=$4 hist=$5
    if [[ "${dev}" == rtlsdr ]]; then
        cat > "${cfg}" <<EOF
rxid: 0
device_type: rtlsdr
bit_depth: 8
sample_rate: ${rate}
tuner_freq: ${CENTER_HZ}
capture_skip: ${CAPTURE_SKIP}
block_size: ${bs}
block_history: ${hist}
carrier_window: ${CARRIER_WINDOW}
carrier_threshold: ${CARRIER_THRESHOLD}
tuner_gain: 0
EOF
    else
        cat > "${cfg}" <<EOF
rxid: 0
device_type: airspy_r2
bit_depth: 12
sample_rate: ${rate}
tuner_freq: ${CENTER_HZ}
capture_skip: ${CAPTURE_SKIP}
block_size: ${bs}
block_history: ${hist}
carrier_window: ${CARRIER_WINDOW}
carrier_threshold: ${CARRIER_THRESHOLD}
airspy_serial: ${R2_SERIAL}
gain_mode: manual
lna_gain: ${R2_LNA}
mixer_gain: ${R2_MIXER}
vga_gain: ${R2_VGA}
lna_agc: false
mixer_agc: false
bias_tee: ${R2_BIAS_TEE}
ppm: 0
packing: ${R2_PACKING}
EOF
    fi
}

write_detect_cfg() {
    local cfg=$1 dev=$2 rate=$3 bs=$4 hist=$5 template=$6 threshold=$7 bit=8
    [[ "${dev}" == airspy_r2 ]] && bit=12
    cat > "${cfg}" <<EOF
rxid: 0
device_type: ${dev}
bit_depth: ${bit}
sample_rate: ${rate}
chip_rate: ${CHIP_RATE_HZ}
tuner_freq: ${CENTER_HZ}
block_size: ${bs}
block_history: ${hist}
carrier_window: ${CARRIER_WINDOW}
carrier_threshold: ${CARRIER_THRESHOLD}
corr_threshold: ${threshold}
template: ${template}
freq_shift_method: integer
soa_interpolation: parabolic
EOF
}

count_card_blocks() { awk '!/^#/ && NF {n++} END {print n+0}' "$1"; }
dropped_pairs() {
    grep -Eo 'WARNING: [0-9]+ sample pairs were lost' "$1" 2>/dev/null       | grep -Eo '[0-9]+' | tail -1 || true
}

CODE_REF=""

process_condition() {
    local label=$1 dev=$2 rate=$3 dir="${OUT}/${label}"
    local bs hist tlen capture_cfg card ncard bits idx family
    mkdir -p "${dir}"
    read -r bs hist tlen < <(geom "${rate}")
    capture_cfg="${dir}/capture.cfg"; card="${dir}/capture.card"
    write_capture_cfg "${capture_cfg}" "${dev}" "${rate}" "${bs}" "${hist}"

    echo "=== CAPTURE ${label}: rate=${rate}, block=${bs}, history=${hist} ==="
    if [[ "${dev}" == rtlsdr ]]; then
        local nsamp=$((rate * DURATION))
        rtl_sdr -d "${RTL_DEVICE_INDEX}" -f "${CENTER_HZ}" -s "${rate}" \
          -g 0.1 -n "${nsamp}" - 2> >(tee "${dir}/rtl_sdr.log" >&2) \
          | thriftyx capture "${card}" --input - -c "${capture_cfg}" \
            2> >(tee "${dir}/capture.log" >&2)
        grep -q 'Tuner gain set to 0.00 dB' "${dir}/rtl_sdr.log" || {
            echo "ERROR: RTL manual 0.00 dB was not verified" >&2; exit 3; }
    else
        thriftyx capture "${card}" --duration "${DURATION}" -c "${capture_cfg}" \
          2> >(tee "${dir}/capture.log" >&2)
    fi

    ncard=$(count_card_blocks "${card}")
    (( ncard > 0 )) || { echo "ERROR: no carrier-selected blocks" >&2; exit 4; }

    thriftyx gold --identify "${card}" --sample-rate "${rate}" \
      --chip-rate "${CHIP_RATE_HZ}" | tee "${dir}/gold_identify_card.log"
    read -r bits idx family < <(python - "${card}" "${rate}" "${CHIP_RATE_HZ}" <<'PY'
import sys
from thriftyx import gold
with open(sys.argv[1], "rb") as f:
    results, _, _ = gold.identify_card(
        f, sample_rate=float(sys.argv[2]), chip_rate=float(sys.argv[3]),
        max_blocks=200)
if not results or not gold.is_clear_match(results):
    raise SystemExit("ERROR: no clear Gold/code-family match")
best = results[0]
print(best["bits"], best["index"], best["family"])
PY
)
    local code="${bits}/${idx}/${family}"
    if [[ -z "${CODE_REF}" ]]; then
        CODE_REF="${code}"
        printf 'bits=%s\nindex=%s\nfamily=%s\nsource_condition=%s\n' \
          "${bits}" "${idx}" "${family}" "${label}" > "${OUT}/code_reference.txt"
    elif [[ "${code}" != "${CODE_REF}" ]]; then
        echo "ERROR: transmitter code changed: ${CODE_REF} -> ${code}" >&2; exit 5
    fi

    local theoretical="${dir}/template_theoretical.npy"
    local captured="${dir}/template_captured.npy"
    thriftyx template_generate "${bits}" "${idx}" --family "${family}" \
      --sample-rate "${rate}" --chip-rate "${CHIP_RATE_HZ}" -o "${theoretical}"

    local extract_cfg="${dir}/extract.cfg"
    write_detect_cfg "${extract_cfg}" "${dev}" "${rate}" "${bs}" "${hist}" \
      "$(realpath "${theoretical}")" "${EXTRACT_CORR_THRESHOLD}"
    thriftyx template_extract "${card}" -c "${extract_cfg}" -o "${captured}" \
      2>&1 | tee "${dir}/template_extract.log"

    local detect_cfg="${dir}/detector.cfg"
    write_detect_cfg "${detect_cfg}" "${dev}" "${rate}" "${bs}" "${hist}" \
      "$(realpath "${captured}")" "${CORR_THRESHOLD}"
    thriftyx detect "${card}" -c "${detect_cfg}" -o "${dir}/detections.toad" \
      2>&1 | tee "${dir}/detect.log"

    mkdir -p "${dir}/plots_detected"
    thriftyx analyze_detect "${card}" -c "${detect_cfg}" -m "${ANALYZE_MAX}" \
      -p overview,time,overlays,spectra,corrs \
      --export "${dir}/plots_detected/detect" --no-gui \
      2>&1 | tee "${dir}/analyze_detect.log"

    if [[ "${RUN_FORCED_DIAG,,}" == true ]]; then
        mkdir -p "${dir}/plots_forcedcorr"
        thriftyx analyze_detect "${card}" -c "${detect_cfg}" -F \
          -m "${ANALYZE_MAX}" -p overview,time,overlays,spectra,corrs \
          --export "${dir}/plots_forcedcorr/forced" --no-gui \
          2>&1 | tee "${dir}/analyze_forcedcorr.log" || true
    fi

    local dropped
    dropped=$(dropped_pairs "${dir}/capture.log"); dropped=${dropped:-0}
    python "${SCRIPT_DIR}/detect_log_summary.py" "${dir}/detect.log" \
      --label "${label}" --sample-rate "${rate}" --block-size "${bs}" \
      --history "${hist}" --duration "${DURATION}" \
      --expected-offset-hz "${EXPECTED_OFFSET_HZ}" --card-blocks "${ncard}" \
      --dropped-pairs "${dropped}" --csv "${SUMMARY}" | tee "${dir}/summary.txt"
}

cat > "${OUT}/manifest.txt" <<EOF
experiment=external over-the-air Thrifty TX RTL/R2 comparison
created=$(date -Iseconds)
git_sha=$(git rev-parse HEAD)
center_hz=${CENTER_HZ}
tx_freq_hz=${TX_FREQ_HZ}
chip_rate_hz=${CHIP_RATE_HZ}
duration_s=${DURATION}
carrier_threshold=${CARRIER_THRESHOLD}
corr_threshold=${CORR_THRESHOLD}
rtl_rate=${RTL_RATE}
r2_serial=${R2_SERIAL}
r2_rates=${R2_RATE_LOW},${R2_RATE_HIGH}
r2_gain=LNA ${R2_LNA} / Mixer ${R2_MIXER} / VGA ${R2_VGA}
tx_distance_m=${TX_DISTANCE_M}
tx_height_m=${TX_HEIGHT_M}
rx_height_m=${RX_HEIGHT_M}
tx_antenna_note=${TX_ANTENNA_NOTE}
rx_antenna_note=${RX_ANTENNA_NOTE}
preamp_note=${PREAMP_NOTE}
EOF

echo "============================================================"
echo " OTA PLAN: center=${CENTER_HZ}, tx=${TX_FREQ_HZ}, duration=${DURATION}s"
echo " Only the receiver may be swapped; TX/RX RF geometry stays fixed."
echo "============================================================"
[[ "${CONFIRM_SETTINGS}" == 0 ]] || ready "Review settings; external TX ON/fixed and RTL connected"
process_condition "RTL_2p4M_g0" rtlsdr "${RTL_RATE}"

echo "============================================================"
echo " SWAP RTL -> R2. Do not move TX, antenna, coax, or preamp."
echo "============================================================"
ready "R2 ${R2_SERIAL} connected to the SAME RX chain"
process_condition "R2_2p5M_L${R2_LNA}M${R2_MIXER}V${R2_VGA}" airspy_r2 "${R2_RATE_LOW}"
echo "Keeping the same R2 connected; changing sample rate automatically."
process_condition "R2_10M_L${R2_LNA}M${R2_MIXER}V${R2_VGA}" airspy_r2 "${R2_RATE_HIGH}"

echo "DONE"
echo "Summary: ${SUMMARY}"
echo "Manifest: ${OUT}/manifest.txt"
