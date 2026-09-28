#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# Run the whole RTL-SDR vs Airspy R2 CW level sweep, one receiver after
# the other on the same generator -> amplifier -> cable chain, then
# print the report (docs/bench_rtl_vs_r2_cw.md).  The generator level is
# set by hand: the sweep prompts for every step.
#
# Settings come from the environment (defaults in brackets):
#   AMP_GAIN [20]    external amplifier gain, dB (nominal, logged)
#   LOSS     [0]     cable/attenuator loss before the receiver, dB
#   LEVELS   [off,-125:-55:5]  generator levels, dBm ('off' = RF off)
#   CAPTURE_SECONDS [5]  capture per setting and level
#   FREQ     [161.3M] tuned centre; the generator sits 15 kHz above
#   R2_A_SERIAL / R2_B_SERIAL   Airspy serials (empty = skip that unit)
#   R2_STAGES [0/0/0,0/0/8,0/0/10,0/0/11]
#   R2_RATE  [10M]   PACKING [1] (12-bit USB packing for 10 Msps)
#   RUN      [run1]  label; results go to bench/$RUN/results.csv
#   UNITS    [RTL R2-A R2-B]  which receivers to sweep, in order
#   REF      [RTL:g0, or the first unit's first setting without RTL]
#            reference unit:setting of the report
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "${REPO}"

AMP_GAIN=${AMP_GAIN:-20}
LOSS=${LOSS:-0}
LEVELS=${LEVELS:-off,-125:-55:5}
SECONDS_PER=${CAPTURE_SECONDS:-5}
FREQ=${FREQ:-161.3M}
R2_A_SERIAL=${R2_A_SERIAL:-0x637862DC2E602DD7}
R2_B_SERIAL=${R2_B_SERIAL:-0xB01861DC393A891F}
R2_STAGES=${R2_STAGES:-0/0/0,0/0/8,0/0/10,0/0/11}
R2_RATE=${R2_RATE:-10M}
PACKING=${PACKING:-1}
RUN=${RUN:-run1}
UNITS=${UNITS:-RTL R2-A R2-B}
if [ -z "${REF:-}" ]; then
    case " ${UNITS} " in
        *" RTL "*) REF=RTL:g0 ;;
        *) first=(${UNITS}); REF="${first[0]}:${R2_STAGES%%,*}" ;;
    esac
fi
OUT="bench/${RUN}/results.csv"

mkdir -p "bench/${RUN}"
{
    echo "run=${RUN} date=$(date -Is) host=$(hostname)"
    echo "amp_gain=${AMP_GAIN} loss=${LOSS} levels=${LEVELS} seconds=${SECONDS_PER} freq=${FREQ}"
    echo "thriftyx=$(git rev-parse --short HEAD) librtlsdr=$(ldconfig -p | grep -m1 'librtlsdr.so' | awk '{print $NF}')"
} | tee "bench/${RUN}/run_info.txt"

common=(--freq "${FREQ}" --levels "${LEVELS}" --amp-gain "${AMP_GAIN}"
        --loss "${LOSS}" --seconds "${SECONDS_PER}" --out "${OUT}")
packing=()
[ "${PACKING}" = 1 ] && packing=(--packing)

for unit in ${UNITS}; do
    echo
    echo "=================================================================="
    echo " Connect ${unit} to the amplifier output (same cable), RF OFF."
    echo "=================================================================="
    read -r -p " Enter when connected (s = skip ${unit}): " answer
    [ "${answer}" = s ] && continue
    case "${unit}" in
        RTL)
            python scripts/bench_cw_level.py sweep --unit RTL \
                --device rtlsdr --rtl-gains 0 "${common[@]}" ;;
        R2-A|R2-B)
            serial=${R2_A_SERIAL}
            [ "${unit}" = R2-B ] && serial=${R2_B_SERIAL}
            [ -z "${serial}" ] && { echo "no serial for ${unit}; skipped"; continue; }
            python scripts/bench_cw_level.py sweep --unit "${unit}" \
                --device airspy_r2 --airspy-serial "${serial}" \
                --rate "${R2_RATE}" --stages "${R2_STAGES}" \
                "${packing[@]}" "${common[@]}" ;;
        *)
            echo "unknown unit ${unit}"; exit 2 ;;
    esac
done

echo
python scripts/bench_cw_level.py report "${OUT}" --ref "${REF}" \
    --plot "bench/${RUN}/cw_levels.png" | tee "bench/${RUN}/report.md"
