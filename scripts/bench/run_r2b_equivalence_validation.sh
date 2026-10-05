#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Full R2-B RF-equivalence qualification.
# Fixed chain: N9310A -> AIS filtered preamp -> Airspy R2-B.
#
# Reliability note:
#   During Phases A/B/C, tuner register reads are skipped and Airspy RX is
#   stopped before every N9310A USBTMC transaction. This prevents a 10 MSPS
#   Airspy bulk stream from overlapping generator USB traffic on the shared
#   WSL/usbipd path. Actual R820T2 register snapshots are collected separately
#   in Phase D, one short independent session per rate/gain setting.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

DEVICE_LABEL="R2-B"
R2_SERIAL=${R2_SERIAL:-0xB01861DC393A891F}
RUN=${RUN:-equiv_full_$(date +%Y%m%d_%H%M%S)}
OUTDIR="bench/${RUN}/${DEVICE_LABEL}"

CENTER_HZ=${CENTER_HZ:-161300000}
TONE_OFFSETS=${TONE_OFFSETS:-"-30000 -15000 15000 30000"}
R2_RATES=${R2_RATES:-"2500000 10000000"}
REPEATS=${REPEATS:-3}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-3}
GENERATOR_SETTLE=${GENERATOR_SETTLE:-1}
RX_SETTLE=${RX_SETTLE:-0.5}
N9310A_RESOURCE=${N9310A_RESOURCE:-auto}

# The AIS preamp is labelled gain >20 dB. Keep 20 dB as nominal metadata
# until actual gain and cable loss are measured.
AMP_GAIN=${AMP_GAIN:-20}
AMP_POWER=${AMP_POWER:-"Airspy R2 bias tee"}
LOSS=${LOSS:-0}

R2_BIAS_TEE=${R2_BIAS_TEE:-1}
PACKING=${PACKING:-1}
# Measurement phases intentionally skip per-point register reads.
R2_SKIP_REGISTERS=${R2_SKIP_REGISTERS:-1}
# Keep one persistent Airspy stream through each sweep. Repeated stop/start
# at 10 MSPS can itself fail after many cycles; use 1 only as a diagnostic.
STOP_AIRSPY_BETWEEN_LEVELS=${STOP_AIRSPY_BETWEEN_LEVELS:-0}
RECEIVER_WARMUP=${RECEIVER_WARMUP:-3}
RETRY_DROPPED=${RETRY_DROPPED:-2}
RETRY_DELAY=${RETRY_DELAY:-0.5}
CONFIRM_SETTINGS=${CONFIRM_SETTINGS:-1}

# Phase A: dense deployment-setting transfer function.
PRIMARY_MIN_DBM=${PRIMARY_MIN_DBM:--125}
PRIMARY_MAX_DBM=${PRIMARY_MAX_DBM:--60}
PRIMARY_STEP_DB=${PRIMARY_STEP_DB:-2.5}
R2_PRIMARY_STAGES=${R2_PRIMARY_STAGES:-"0/0/8"}

# Phase B: RTL gain-code equivalence map.
GAINMAP_STAGES=${GAINMAP_STAGES:-"0/0/8,4/3/8,6/6/8,8/8/8,11/11/8"}
GAINMAP_LEVELS=${GAINMAP_LEVELS:-"off,-115,-110,-105,-100,-95,off"}
GAINMAP_REPEATS=${GAINMAP_REPEATS:-2}
GAINMAP_TONE_OFFSET=${GAINMAP_TONE_OFFSET:-15000}
RUN_GAINMAP=${RUN_GAINMAP:-1}

# Phase C: R2-only one-factor-at-a-time stage characterization.
STAGE_MATRIX=${STAGE_MATRIX:-"0/0/0,0/0/4,0/0/8,0/0/12,0/0/15,4/0/8,8/0/8,12/0/8,14/0/8,0/4/8,0/8/8,0/12/8,0/15/8"}
STAGE_LEVELS=${STAGE_LEVELS:-"off,-105,-95,off"}
STAGE_TONE_OFFSET=${STAGE_TONE_OFFSET:-15000}
RUN_STAGE_SENSITIVITY=${RUN_STAGE_SENSITIVITY:-1}

# Phase D: actual tuner-register snapshots, isolated from dense sweeps.
REGISTER_STAGES=${REGISTER_STAGES:-"0/0/8,4/3/8,6/6/8,8/8/8,11/11/8"}
REGISTER_TONE_OFFSET=${REGISTER_TONE_OFFSET:-15000}
REGISTER_CAPTURE_SECONDS=${REGISTER_CAPTURE_SECONDS:-1}
RUN_REGISTER_SNAPSHOTS=${RUN_REGISTER_SNAPSHOTS:-1}

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

rate_tag() {
    case "$1" in
        2500000) echo "2p5M" ;;
        10000000) echo "10M" ;;
        *) echo "$1" ;;
    esac
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

stage_tag() {
    local stage=$1
    local lna mixer vga
    IFS=/ read -r lna mixer vga <<< "${stage}"
    printf "L%sM%sV%s" "${lna}" "${mixer}" "${vga}"
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

packing_args=()
if [[ "${PACKING}" == 1 ]]; then
    packing_args=(--packing)
elif [[ "${PACKING}" != 0 ]]; then
    echo "ERROR: PACKING must be 0 or 1" >&2
    exit 2
fi

bias_args=()
if [[ "${R2_BIAS_TEE}" == 1 ]]; then
    bias_args=(--bias-tee)
elif [[ "${R2_BIAS_TEE}" != 0 ]]; then
    echo "ERROR: R2_BIAS_TEE must be 0 or 1" >&2
    exit 2
fi

register_args=()
if [[ "${R2_SKIP_REGISTERS}" == 1 ]]; then
    register_args=(--skip-registers)
elif [[ "${R2_SKIP_REGISTERS}" != 0 ]]; then
    echo "ERROR: R2_SKIP_REGISTERS must be 0 or 1" >&2
    exit 2
fi

stream_isolation_args=()
if [[ "${STOP_AIRSPY_BETWEEN_LEVELS}" == 1 ]]; then
    stream_isolation_args=(--stop-airspy-between-levels)
elif [[ "${STOP_AIRSPY_BETWEEN_LEVELS}" != 0 ]]; then
    echo "ERROR: STOP_AIRSPY_BETWEEN_LEVELS must be 0 or 1" >&2
    exit 2
fi

echo "== N9310A preflight =="
python scripts/n9310a_control.py --resource "${N9310A_RESOURCE}" status
generator_off

if ! lsusb | grep -qi '1d50:60a1'; then
    echo "ERROR: Airspy 1d50:60a1 is not visible in WSL." >&2
    exit 2
fi

airspy_info >"${OUTDIR}/${DEVICE_LABEL}_airspy_info.txt" 2>&1
if ! grep -qi "${R2_SERIAL#0x}" "${OUTDIR}/${DEVICE_LABEL}_airspy_info.txt"; then
    echo "ERROR: expected ${DEVICE_LABEL} serial ${R2_SERIAL} not found." >&2
    cat "${OUTDIR}/${DEVICE_LABEL}_airspy_info.txt" >&2
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

rates = len("""${R2_RATES}""".split())
tones = len("""${TONE_OFFSETS}""".split())
primary_levels = int(round(
    (float("${PRIMARY_MAX_DBM}") - float("${PRIMARY_MIN_DBM}"))
    / float("${PRIMARY_STEP_DB}")
)) + 1 + 2
phase_a_sweeps = rates * int("${REPEATS}") * tones
phase_a_meas = phase_a_sweeps * primary_levels

phase_b_levels = len(parse_levels("""${GAINMAP_LEVELS}"""))
phase_b_settings = len("""${GAINMAP_STAGES}""".split(","))
phase_b_sweeps = rates * int("${GAINMAP_REPEATS}") if int("${RUN_GAINMAP}") else 0
phase_b_meas = phase_b_sweeps * phase_b_levels * phase_b_settings

phase_c_levels = len(parse_levels("""${STAGE_LEVELS}"""))
phase_c_settings = len("""${STAGE_MATRIX}""".split(","))
phase_c_sweeps = rates if int("${RUN_STAGE_SENSITIVITY}") else 0
phase_c_meas = phase_c_sweeps * phase_c_levels * phase_c_settings

phase_d_meas = (
    rates * len("""${REGISTER_STAGES}""".split(","))
    if int("${RUN_REGISTER_SNAPSHOTS}") else 0
)

capture = float("${CAPTURE_SECONDS}")
rx_settle = float("${RX_SETTLE}")
gen_settle = float("${GENERATOR_SETTLE}")
per_meas = capture + rx_settle + 0.15

phase_a_sec = phase_a_meas * (per_meas + gen_settle)
phase_b_sec = phase_b_meas * per_meas + phase_b_sweeps * phase_b_levels * gen_settle
phase_c_sec = phase_c_meas * per_meas + phase_c_sweeps * phase_c_levels * gen_settle
phase_d_sec = phase_d_meas * (
    float("${REGISTER_CAPTURE_SECONDS}") + rx_settle + gen_settle + 1.0
)
core = phase_a_sec + phase_b_sec + phase_c_sec + phase_d_sec

print(
    f"Phase A: {phase_a_meas}; Phase B: {phase_b_meas}; "
    f"Phase C: {phase_c_meas}; Phase D: {phase_d_meas}; "
    f"total {phase_a_meas + phase_b_meas + phase_c_meas + phase_d_meas}. "
    f"Core timing ~{core/60:.1f} min; allow ~{core/60*1.10:.0f}-"
    f"{core/60*1.25:.0f} min wall-clock."
)
PY
)

echo
echo "=================================================================="
echo " ${DEVICE_LABEL} FULL EQUIVALENCE QUALIFICATION — SAFE USB MODE"
echo " run                  : ${RUN}"
echo " output               : ${OUTDIR}"
echo " serial               : ${R2_SERIAL}"
echo " centre               : ${CENTER_HZ} Hz"
echo " tone offsets         : ${TONE_OFFSETS} Hz"
echo " rates                : ${R2_RATES} S/s"
echo " primary stages       : ${R2_PRIMARY_STAGES}"
echo " primary grid         : ${PRIMARY_MIN_DBM} to ${PRIMARY_MAX_DBM} dBm"
echo " primary step         : ${PRIMARY_STEP_DB} dB"
echo " primary repeats      : ${REPEATS}"
echo " bias tee / packing   : ${R2_BIAS_TEE} / ${PACKING}"
echo " A/B/C register reads : $([[ "${R2_SKIP_REGISTERS}" == 1 ]] && echo skipped || echo enabled)"
echo " stop RX for N9310A   : ${STOP_AIRSPY_BETWEEN_LEVELS}"
echo " receiver warmup      : ${RECEIVER_WARMUP} s"
echo " retry dropped rows   : ${RETRY_DROPPED}"
echo " Phase D reg snapshots: ${RUN_REGISTER_SNAPSHOTS} (${REGISTER_STAGES})"
echo " capture / gen settle : ${CAPTURE_SECONDS} s / ${GENERATOR_SETTLE} s"
echo " RX settle            : ${RX_SETTLE} s"
echo " amp nominal / power  : ${AMP_GAIN} dB / ${AMP_POWER}"
echo " ETA                   : ${ETA_TEXT}"
echo "=================================================================="

if [[ "${CONFIRM_SETTINGS}" == 1 ]]; then
    read -r -p "${DEVICE_LABEL} is connected to the fixed RF chain. N9310A is OFF. Enter=start, q=quit: " answer
    [[ "${answer,,}" == q ]] && exit 0
fi

{
    echo "run=${RUN}"
    echo "device_label=${DEVICE_LABEL}"
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
    echo "measurement_register_reads=$([[ "${R2_SKIP_REGISTERS}" == 1 ]] && echo skipped || echo enabled)"
    echo "stop_airspy_between_levels=${STOP_AIRSPY_BETWEEN_LEVELS}"
    echo "receiver_warmup_s=${RECEIVER_WARMUP}"
    echo "retry_dropped=${RETRY_DROPPED}"
    echo "retry_delay_s=${RETRY_DELAY}"
    echo "phase_d_register_snapshots=${RUN_REGISTER_SNAPSHOTS}"
    echo "amp_gain_nominal_db=${AMP_GAIN}"
    echo "amp_power=${AMP_POWER}"
    echo "loss_nominal_db=${LOSS}"
    echo "n9310a_resource=${N9310A_RESOURCE}"
    echo "gainmap_stages=${GAINMAP_STAGES}"
    echo "gainmap_levels=${GAINMAP_LEVELS}"
    echo "gainmap_repeats=${GAINMAP_REPEATS}"
    echo "stage_matrix=${STAGE_MATRIX}"
    echo "stage_levels=${STAGE_LEVELS}"
    echo "register_stages=${REGISTER_STAGES}"
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

: > "${OUTDIR}/${DEVICE_LABEL}_register_model_primary.txt"
for rate in ${R2_RATES}; do
    {
        echo "=================================================================="
        echo "R2 rate ${rate}, RTL gain 0 vs R2 0/0/8"
        python scripts/r820t_register_model.py             -f "${CENTER_HZ}"             --rtl-rate 2400000             --rtl-gain 0             --airspy r2             --airspy-rate "${rate}"             --lna 0             --mixer 0             --vga 8
    } >> "${OUTDIR}/${DEVICE_LABEL}_register_model_primary.txt" 2>&1 || true
done

echo
echo "== Phase A: dense deployment-setting transfer function =="
for rate in ${R2_RATES}; do
    rtag=$(rate_tag "${rate}")
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
            stem="${DEVICE_LABEL}_phaseA_primary_rate${rtag}_rep${rep_tag}_${direction}_tone_${ttag}"
            out="${OUTDIR}/${stem}.csv"
            log="${OUTDIR}/${stem}.log"
            echo
            echo "-- ${DEVICE_LABEL} Phase A ${rtag}, rep ${rep_tag}, ${direction}, ${ttag} --"
            if ! prepare_output "${out}" "${log}"; then
                continue
            fi
            python scripts/bench_cw_auto.py                 --generator-resource "${N9310A_RESOURCE}"                 --generator-settle "${GENERATOR_SETTLE}"                 --receiver-warmup "${RECEIVER_WARMUP}"                 --retry-dropped "${RETRY_DROPPED}"                 --retry-delay "${RETRY_DELAY}"                 "${stream_isolation_args[@]}"                 --unit "${DEVICE_LABEL}"                 --device airspy_r2                 --airspy-serial "${R2_SERIAL}"                 --rate "${rate}"                 --freq "${CENTER_HZ}"                 --tone "${tone}"                 "--levels=${levels}"                 --stages "${R2_PRIMARY_STAGES}"                 "${packing_args[@]}"                 "${bias_args[@]}"                 "${register_args[@]}"                 --seconds "${CAPTURE_SECONDS}"                 --settle "${RX_SETTLE}"                 --amp-gain "${AMP_GAIN}"                 --loss "${LOSS}"                 --notes "equivalence phase=A device=${DEVICE_LABEL} rep=${rep} direction=${direction} tone_offset_hz=${offset}; amp_power=${AMP_POWER}; airspy_stream_mode=$([[ "${STOP_AIRSPY_BETWEEN_LEVELS}" == 1 ]] && echo restart-each-level || echo persistent)"                 --out "${out}" 2>&1 | tee "${log}"
        done < <(tone_order_for_rep "${rep}")
    done
done

if [[ "${RUN_GAINMAP}" == 1 ]]; then
    echo
    echo "== Phase B: matched R820T gain-code transfer map =="
    tone=$((CENTER_HZ + GAINMAP_TONE_OFFSET))
    ttag=$(tone_tag "${GAINMAP_TONE_OFFSET}")
    for rate in ${R2_RATES}; do
        rtag=$(rate_tag "${rate}")
        for ((rep=1; rep<=GAINMAP_REPEATS; rep++)); do
            rep_tag=$(printf "%02d" "${rep}")
            stem="${DEVICE_LABEL}_phaseB_gainmap_rate${rtag}_rep${rep_tag}_tone_${ttag}"
            out="${OUTDIR}/${stem}.csv"
            log="${OUTDIR}/${stem}.log"
            if ! prepare_output "${out}" "${log}"; then
                continue
            fi
            python scripts/bench_cw_auto.py                 --generator-resource "${N9310A_RESOURCE}"                 --generator-settle "${GENERATOR_SETTLE}"                 --receiver-warmup "${RECEIVER_WARMUP}"                 --retry-dropped "${RETRY_DROPPED}"                 --retry-delay "${RETRY_DELAY}"                 "${stream_isolation_args[@]}"                 --unit "${DEVICE_LABEL}"                 --device airspy_r2                 --airspy-serial "${R2_SERIAL}"                 --rate "${rate}"                 --freq "${CENTER_HZ}"                 --tone "${tone}"                 "--levels=${GAINMAP_LEVELS}"                 --stages "${GAINMAP_STAGES}"                 "${packing_args[@]}"                 "${bias_args[@]}"                 "${register_args[@]}"                 --seconds "${CAPTURE_SECONDS}"                 --settle "${RX_SETTLE}"                 --amp-gain "${AMP_GAIN}"                 --loss "${LOSS}"                 --notes "equivalence phase=B device=${DEVICE_LABEL} gainmap rep=${rep} tone_offset_hz=${GAINMAP_TONE_OFFSET}; amp_power=${AMP_POWER}; usb_isolation=stop-rx-between-levels"                 --out "${out}" 2>&1 | tee "${log}"
        done
    done
fi

if [[ "${RUN_STAGE_SENSITIVITY}" == 1 ]]; then
    echo
    echo "== Phase C: R2 one-factor-at-a-time stage characterization =="
    tone=$((CENTER_HZ + STAGE_TONE_OFFSET))
    ttag=$(tone_tag "${STAGE_TONE_OFFSET}")
    for rate in ${R2_RATES}; do
        rtag=$(rate_tag "${rate}")
        stem="${DEVICE_LABEL}_phaseC_stage_rate${rtag}_tone_${ttag}"
        out="${OUTDIR}/${stem}.csv"
        log="${OUTDIR}/${stem}.log"
        if ! prepare_output "${out}" "${log}"; then
            continue
        fi
        python scripts/bench_cw_auto.py             --generator-resource "${N9310A_RESOURCE}"             --generator-settle "${GENERATOR_SETTLE}"             --receiver-warmup "${RECEIVER_WARMUP}"             --retry-dropped "${RETRY_DROPPED}"             --retry-delay "${RETRY_DELAY}"             "${stream_isolation_args[@]}"             --unit "${DEVICE_LABEL}"             --device airspy_r2             --airspy-serial "${R2_SERIAL}"             --rate "${rate}"             --freq "${CENTER_HZ}"             --tone "${tone}"             "--levels=${STAGE_LEVELS}"             --stages "${STAGE_MATRIX}"             "${packing_args[@]}"             "${bias_args[@]}"             "${register_args[@]}"             --seconds "${CAPTURE_SECONDS}"             --settle "${RX_SETTLE}"             --amp-gain "${AMP_GAIN}"             --loss "${LOSS}"             --notes "equivalence phase=C device=${DEVICE_LABEL} stage-sensitivity tone_offset_hz=${STAGE_TONE_OFFSET}; amp_power=${AMP_POWER}; usb_isolation=stop-rx-between-levels"             --out "${out}" 2>&1 | tee "${log}"
    done
fi

if [[ "${RUN_REGISTER_SNAPSHOTS}" == 1 ]]; then
    echo
    echo "== Phase D: isolated actual R820T2 register snapshots =="
    tone=$((CENTER_HZ + REGISTER_TONE_OFFSET))
    for rate in ${R2_RATES}; do
        rtag=$(rate_tag "${rate}")
        IFS=, read -r -a reg_stages <<< "${REGISTER_STAGES}"
        for stage in "${reg_stages[@]}"; do
            stag=$(stage_tag "${stage}")
            stem="${DEVICE_LABEL}_phaseD_reg${stag}_rate${rtag}"
            out="${OUTDIR}/${stem}.csv"
            log="${OUTDIR}/${stem}.log"
            if ! prepare_output "${out}" "${log}"; then
                continue
            fi
            python scripts/bench_cw_auto.py                 --generator-resource "${N9310A_RESOURCE}"                 --generator-settle "${GENERATOR_SETTLE}"                 --receiver-warmup "${RECEIVER_WARMUP}"                 --retry-dropped "${RETRY_DROPPED}"                 --retry-delay "${RETRY_DELAY}"                 "${stream_isolation_args[@]}"                 --unit "${DEVICE_LABEL}"                 --device airspy_r2                 --airspy-serial "${R2_SERIAL}"                 --rate "${rate}"                 --freq "${CENTER_HZ}"                 --tone "${tone}"                 --levels=off                 --stages "${stage}"                 "${packing_args[@]}"                 "${bias_args[@]}"                 --seconds "${REGISTER_CAPTURE_SECONDS}"                 --settle "${RX_SETTLE}"                 --amp-gain "${AMP_GAIN}"                 --loss "${LOSS}"                 --notes "equivalence phase=D device=${DEVICE_LABEL} isolated-register-snapshot stage=${stage}; amp_power=${AMP_POWER}"                 --out "${out}" 2>&1 | tee "${log}"
        done
    done
fi

generator_off

python scripts/bench/combine_device_validation.py     "${OUTDIR}"     --device-label "${DEVICE_LABEL}"

find "${OUTDIR}" -maxdepth 1 -type f -printf '%f\n'     | sort > "${OUTDIR}/${DEVICE_LABEL}_file_manifest.txt"

echo
echo "${DEVICE_LABEL} QUALIFICATION COMPLETE"
echo "Directory : ${OUTDIR}"
echo "All rows  : ${OUTDIR}/${DEVICE_LABEL}_combined_all.csv"
echo "Phase A   : ${OUTDIR}/${DEVICE_LABEL}_combined_phaseA_primary.csv"
echo "Phase B   : ${OUTDIR}/${DEVICE_LABEL}_combined_phaseB_gainmap.csv"
echo "Phase C   : ${OUTDIR}/${DEVICE_LABEL}_combined_phaseC_stage.csv"
echo "Phase D   : ${OUTDIR}/${DEVICE_LABEL}_combined_phaseD_registers.csv"
echo "Run info  : ${OUTDIR}/${DEVICE_LABEL}_run_info.txt"
echo "Manifest  : ${OUTDIR}/${DEVICE_LABEL}_file_manifest.txt"
echo
echo "N9310A RF is OFF. Keep the RF chain fixed before swapping to RTL."
