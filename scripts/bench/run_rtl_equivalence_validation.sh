#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Full RTL-SDR RF-equivalence qualification.
# Fixed chain: N9310A -> AIS filtered preamp -> NESDR SMArTee-family RTL-SDR.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

DEVICE_LABEL="RTL"
RUN=${RUN:-equiv_full_$(date +%Y%m%d_%H%M%S)}
OUTDIR="bench/${RUN}/${DEVICE_LABEL}"

CENTER_HZ=${CENTER_HZ:-161300000}
TONE_OFFSETS=${TONE_OFFSETS:-"-30000 -15000 15000 30000"}
RTL_RATE=${RTL_RATE:-2400000}
REPEATS=${REPEATS:-3}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-3}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-1}
RX_SETTLE=${RX_SETTLE:-0.5}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}
GENERATOR_RETRIES=${GENERATOR_RETRIES:-2}
GENERATOR_RETRY_DELAY=${GENERATOR_RETRY_DELAY:-0.5}

AMP_GAIN=${AMP_GAIN:-20}
AMP_POWER=${AMP_POWER:-"NESDR SMArTee hardware bias tee"}
LOSS=${LOSS:-0}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

PRIMARY_MIN_DBM=${PRIMARY_MIN_DBM:--125}
PRIMARY_MAX_DBM=${PRIMARY_MAX_DBM:--60}
PRIMARY_STEP_DB=${PRIMARY_STEP_DB:-2.5}
RTL_PRIMARY_GAIN=${RTL_PRIMARY_GAIN:-0}

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

prepare_output() {
    local out=$1
    local log=$2
    if [[ -s "${out}" && -s "${log}" ]] &&
       grep -Fq "Done: ${out}" "${log}"; then
        echo "SKIP completed sweep: ${out}"
        return 1
    fi
    # A failed sweep is re-run from its beginning. Remove only that sweep's
    # partial files so rows are never duplicated; already-completed sweeps
    # remain untouched.
    if [[ -e "${out}" || -e "${log}" ]]; then
        echo "RESTART partial sweep: ${out}"
        rm -f "${out}" "${log}"
    fi
    return 0
}

tone_tag() {
    local value=$1
    local sign="p"
    local absval=${value}
    if (( value < 0 )); then
        sign="m"
        absval=$(( -value ))
    fi
    printf "%s%03dkHz" "${sign}" $(( absval / 1000 ))
}

primary_levels() {
    local direction=$1
    if [[ "${direction}" == "asc" ]]; then
        echo "off,${PRIMARY_MIN_DBM}:${PRIMARY_MAX_DBM}:${PRIMARY_STEP_DB},off"
    else
        echo "off,${PRIMARY_MAX_DBM}:${PRIMARY_MIN_DBM}:-${PRIMARY_STEP_DB},off"
    fi
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

rtl_test -t >"${OUTDIR}/${DEVICE_LABEL}_rtl_test.txt" 2>&1 || true
if ! grep -qi 'R820' "${OUTDIR}/${DEVICE_LABEL}_rtl_test.txt"; then
    echo "ERROR: R820T/R820T2 tuner not confirmed." >&2
    cat "${OUTDIR}/${DEVICE_LABEL}_rtl_test.txt" >&2
    exit 2
fi

ETA_TEXT=$(python - <<PY
def parse_levels(text):
    out = []
    for item in text.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if item == "off":
            out.append(None)
        elif ":" in item:
            a, b, s = map(float, item.split(":"))
            n = int(round((b-a)/s)) + 1
            out.extend(a+i*s for i in range(n))
        else:
            out.append(float(item))
    return out

tones = len("""${TONE_OFFSETS}""".split())
primary_levels = int(round(
    (float("${PRIMARY_MAX_DBM}") - float("${PRIMARY_MIN_DBM}"))
    / float("${PRIMARY_STEP_DB}")
)) + 1 + 2
phase_a_sweeps = int("${REPEATS}") * tones
phase_a_meas = phase_a_sweeps * primary_levels

phase_b_levels = len(parse_levels("""${GAINMAP_LEVELS}"""))
phase_b_settings = len("""${GAINMAP_GAINS}""".split(","))
phase_b_sweeps = int("${GAINMAP_REPEATS}") if int("${RUN_GAINMAP}") else 0
phase_b_meas = phase_b_sweeps * phase_b_levels * phase_b_settings

capture = float("${CAPTURE_SECONDS}")
rx_settle = float("${RX_SETTLE}")
gen_settle = float("${GENERATOR_SETTLE}")
per_meas = capture + rx_settle + 0.1

phase_a_sec = phase_a_meas * per_meas + phase_a_sweeps * primary_levels * gen_settle
phase_b_sec = phase_b_meas * per_meas + phase_b_sweeps * phase_b_levels * gen_settle
core = phase_a_sec + phase_b_sec

print(
    f"Phase A: {phase_a_meas} captures; "
    f"Phase B: {phase_b_meas}; total {phase_a_meas + phase_b_meas}. "
    f"Core timing ~{core/60:.1f} min; allow ~{core/60*1.08:.0f}-"
    f"{core/60*1.15:.0f} min wall-clock."
)
PY
)

echo
echo "=================================================================="
echo " RTL FULL EQUIVALENCE QUALIFICATION"
echo " run                  : ${RUN}"
echo " output               : ${OUTDIR}"
echo " centre               : ${CENTER_HZ} Hz"
echo " tone offsets         : ${TONE_OFFSETS} Hz"
echo " sample rate          : ${RTL_RATE} S/s"
echo " primary gain         : manual ${RTL_PRIMARY_GAIN} dB"
echo " primary grid         : ${PRIMARY_MIN_DBM} to ${PRIMARY_MAX_DBM} dBm"
echo " primary step         : ${PRIMARY_STEP_DB} dB"
echo " primary repeats      : ${REPEATS}"
echo " gain-map gains       : ${GAINMAP_GAINS} dB"
echo " capture / gen settle : ${CAPTURE_SECONDS} s / ${GENERATOR_SETTLE} s"
echo " generator retries    : ${GENERATOR_RETRIES} (delay ${GENERATOR_RETRY_DELAY} s)"
echo " RX settle            : ${RX_SETTLE} s"
echo " amp nominal / power  : ${AMP_GAIN} dB / ${AMP_POWER}"
echo " ETA                   : ${ETA_TEXT}"
echo "=================================================================="

if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "RTL is connected to the fixed RF chain. N9310A is OFF. Enter=start, q=quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

{
    echo "run=${RUN}"
    echo "device_label=${DEVICE_LABEL}"
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
    echo "generator_retries=${GENERATOR_RETRIES}"
    echo "generator_retry_delay_s=${GENERATOR_RETRY_DELAY}"
    echo "gainmap_gains_db=${GAINMAP_GAINS}"
    echo "gainmap_levels=${GAINMAP_LEVELS}"
    echo "gainmap_repeats=${GAINMAP_REPEATS}"
    echo "eta_model=${ETA_TEXT}"
} > "${OUTDIR}/${DEVICE_LABEL}_run_info.txt"

python - <<'PY' > "${OUTDIR}/${DEVICE_LABEL}_gain_code_map.txt"
from thriftyx.hal.r820t import airspy_equivalent_of_rtl_gain, nearest_rtl_gain

for gain in (0, 12.5, 20.7, 29.7, 40.2):
    snapped = nearest_rtl_gain(gain) / 10
    mapped = airspy_equivalent_of_rtl_gain(gain)
    print(
        f"RTL {gain:g} dB -> supported {snapped:g} dB "
        f"-> Airspy {mapped}"
    )
PY

echo
echo "== Phase A: dense deployment-setting transfer function =="
for ((rep=1; rep<=REPEATS; rep++)); do
    if (( rep % 2 == 1 )); then
        direction="asc"
    else
        direction="desc"
    fi
    levels=$(primary_levels "${direction}")
    rep_tag=$(printf "%02d" "${rep}")
    while read -r offset; do
        tone=$((CENTER_HZ + offset))
        ttag=$(tone_tag "${offset}")
        stem="${DEVICE_LABEL}_phaseA_primary_rate2p4M_rep${rep_tag}_${direction}_tone_${ttag}"
        out="${OUTDIR}/${stem}.csv"
        log="${OUTDIR}/${stem}.log"
        echo
        echo "-- RTL Phase A 2p4M, rep ${rep_tag}, ${direction}, ${ttag} --"
        if ! prepare_output "${out}" "${log}"; then
            continue
        fi
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --generator-retries "${GENERATOR_RETRIES}"             --generator-retry-delay "${GENERATOR_RETRY_DELAY}"             --unit "${DEVICE_LABEL}"             --device rtlsdr             --rate "${RTL_RATE}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${levels}"             --rtl-gains "${RTL_PRIMARY_GAIN}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=A device=RTL rep=${rep} direction=${direction} tone_offset_hz=${offset}; amp_power=${AMP_POWER}"             --out "${out}" 2>&1 | tee "${log}"
    done < <(tone_order_for_rep "${rep}")
done

if [[ "${RUN_GAINMAP}" == 1 ]]; then
    echo
    echo "== Phase B: RTL manual gain / matched R820T gain-code map =="
    tone=$((CENTER_HZ + GAINMAP_TONE_OFFSET))
    ttag=$(tone_tag "${GAINMAP_TONE_OFFSET}")
    for ((rep=1; rep<=GAINMAP_REPEATS; rep++)); do
        rep_tag=$(printf "%02d" "${rep}")
        stem="${DEVICE_LABEL}_phaseB_gainmap_rate2p4M_rep${rep_tag}_tone_${ttag}"
        out="${OUTDIR}/${stem}.csv"
        log="${OUTDIR}/${stem}.log"
        if ! prepare_output "${out}" "${log}"; then
            continue
        fi
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --generator-retries "${GENERATOR_RETRIES}"             --generator-retry-delay "${GENERATOR_RETRY_DELAY}"             --unit "${DEVICE_LABEL}"             --device rtlsdr             --rate "${RTL_RATE}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${GAINMAP_LEVELS}"             --rtl-gains "${GAINMAP_GAINS}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=B device=RTL gainmap rep=${rep} tone_offset_hz=${GAINMAP_TONE_OFFSET}; amp_power=${AMP_POWER}"             --out "${out}" 2>&1 | tee "${log}"
    done
fi

generator_off

python scripts/bench/combine_device_validation.py     "${OUTDIR}"     --device-label "${DEVICE_LABEL}"

find "${OUTDIR}" -maxdepth 1 -type f -printf '%f\n'     | sort > "${OUTDIR}/${DEVICE_LABEL}_file_manifest.txt"

echo
echo "RTL QUALIFICATION COMPLETE"
echo "Directory : ${OUTDIR}"
echo "All rows  : ${OUTDIR}/RTL_combined_all.csv"
echo "Phase A   : ${OUTDIR}/RTL_combined_phaseA_primary.csv"
echo "Phase B   : ${OUTDIR}/RTL_combined_phaseB_gainmap.csv"
echo "Run info  : ${OUTDIR}/RTL_run_info.txt"
echo "Manifest  : ${OUTDIR}/RTL_file_manifest.txt"
echo
echo "N9310A RF is OFF. All three receiver runs can now be compared."
