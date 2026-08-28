#!/bin/bash
# Launcher for the C fastdet detector against a live Airspy.
#
# Updated for the Airspy stack: fastdet's SDR input sentinel is
# "airspy" (fastcapture/fastcard.c), bias tee is fastdet's own -B flag
# (no rtl_biast), gains are the R820T2 LNA/Mixer/VGA indices, and time
# sync uses chrony (the only supported daemon on Pi 5 / Bookworm — see
# rpi/ntp-after-online.sh).  Configuration lives in fastdet.cfg.

set -e

echo "Waiting for chrony to discipline the clock"
# Step immediately, then block until the offset is within tolerance.
# Same approach as ntp-after-online.sh; capture must not start on an
# undisciplined clock or TDOA alignment is meaningless.
sudo chronyc makestep > /dev/null
chronyc waitsync 60 0.1

echo "Starting fastdet"
cd /home/pi/detector
mkdir -p toad/ card/ log/
TOAD_FILE="toad/$(date +"%Y-%m-%d_%H-%M-%S").toad"
CARD_FILE="card/$(date +"%Y-%m-%d_%H-%M-%S").card"
LOG_FILE="log/$(date +"%Y-%m-%d_%H-%M-%S").log"
. ./fastdet.cfg
CARD_ARG=
if [ -n "${EXPORT_CARD}" ]; then
    CARD_ARG="-x ${CARD_FILE}"
fi
BIAS_ARG=
if [ -n "${BIAS_TEE}" ]; then
    BIAS_ARG="-B"
fi
exec fastdet \
    -r ${RXID} \
    -t ${THRESH_CARRIER} \
    -u ${THRESH_CORR} \
    -k ${SKIP} \
    -w ${WINDOW} \
    -m ${WISDOM_FILE} \
    -z ${TEMPLATE_FILE} \
    -i airspy \
    -s ${SAMPLE_RATE} \
    -f ${FREQ} \
    -g ${LNA_GAIN} \
    -M ${MIXER_GAIN} \
    -V ${VGA_GAIN} \
    ${BIAS_ARG} \
    -o ${TOAD_FILE} ${CARD_ARG} >> ${LOG_FILE}
