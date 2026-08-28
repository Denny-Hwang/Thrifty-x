#!/bin/bash
# Legacy RTL-SDR capture+detect pipeline (see rpi/detector.cfg).
#
# REQUIREMENT: `thrifty capture` with device_type=rtlsdr prefers a
# binary named `fastcard` (the ORIGINAL Thrifty C capture tool built
# against librtlsdr) on PATH.  This repository's C tree builds
# `fastcapture`, whose only live-SDR input is "airspy" — it is NOT a
# substitute here.  Without the upstream fastcard binary, capture
# falls back to reading raw samples from stdin, which this pipeline
# does not provide, and the run exits with "fastcard died
# prematurely".  For Airspy hardware use rpi/fastdet.sh or the
# systemd units under rpi/systemd/ instead.

set -e

killjobs() {
    JOBS="$(jobs -p)"
    echo "Kill jobs: ${JOBS}"
    if [ -n "${JOBS}" ]; then
        kill ${JOBS}
    fi
    exit -1
}

trap "killjobs" SIGINT SIGTERM

echo "Waiting for chrony to discipline the clock..."
# Pi 5 / Bookworm stack uses chrony (legacy ntpd's ntp-wait is not
# installed) — same convention as rpi/ntp-after-online.sh.
chronyc waitsync 60 0.1

echo "Starting Thrifty..."
cd /home/pi/detector
rm -f fifo.card buffer
mkfifo fifo.card buffer
TOAD_FILE="toad/$(date +"%Y-%m-%d_%H-%M-%S").toad"

set -m
(
    (thrifty capture buffer >>capture.log || (echo "fastcard died prematurely" && kill 0)) &
    (buffer -m 32M -s 55236 -i buffer -o fifo.card || (echo "buffer died prematurely" && kill 0)) &
    (thrifty detect fifo.card -a $TOAD_FILE >>detect.log || (echo "thrifty detect died prematurely" && kill 0)) &
    wait
)

exit 0
