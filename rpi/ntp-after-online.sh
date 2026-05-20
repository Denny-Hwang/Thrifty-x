#!/bin/sh
# Force-sync the system clock after the network comes up.
#
# Why: TDOA cross-receiver alignment is meaningless if any node starts
#      capture before its clock has been disciplined. Raspberry Pi 5 has
#      no battery-backed RTC, so the system time can be off by months at
#      boot until NTP corrects it.
#
# Service used: chrony (per docs/installation_pi5.md). This script does
# NOT support the legacy ntpd; chrony is the only supported time daemon
# on Bookworm and the only one referenced by installation_pi5.md.
#
# Exit codes:
#   0  — clock disciplined (chronyc waitsync returned 0)
#   1  — chronyc reached the system but failed to step / wait, OR
#        chronyc is not installed at all.  We *fail closed* in both
#        cases: on a Pi with no RTC, returning success on no-sync would
#        let capture start with an arbitrary wall-clock and silently
#        corrupt TDOA alignment — exactly what this script exists to
#        prevent.  If a deployment legitimately runs without chrony,
#        disable the unit (`systemctl disable ntp-after-online`)
#        instead of relying on this script to silently noop.
#
# Inputs (env):
#   THRIFTYX_NTP_PROBE_HOST  host to ping before attempting sync
#                            (default: 8.8.8.8)
#   THRIFTYX_NTP_WAITSYNC_TRIES  passes for chronyc waitsync (default: 60)
#   THRIFTYX_NTP_WAITSYNC_TOL    max acceptable offset in s
#                                (default: 0.1)

set -u

host="${THRIFTYX_NTP_PROBE_HOST:-8.8.8.8}"
tries="${THRIFTYX_NTP_WAITSYNC_TRIES:-60}"
tol="${THRIFTYX_NTP_WAITSYNC_TOL:-0.1}"

pingcheck() {
    ping -n -c 1 -w 5 "$1" >/dev/null 2>&1
}

# Wait for network reachability before talking to NTP servers.
while :; do
    pingcheck "${host}" && break
    sleep 10
done

if ! command -v chronyc >/dev/null 2>&1; then
    echo "ntp-after-online: chronyc not found — refusing to declare the" \
         "system clock disciplined.  Install chrony (see" \
         "rpi/installation_pi5.md) or disable this unit." >&2
    exit 1
fi

# Step the clock immediately (large adjustment in one go) instead of
# slewing.  Required for TDOA correctness on first boot.
if ! chronyc makestep >/dev/null 2>&1; then
    echo "ntp-after-online: chronyc makestep failed" >&2
    exit 1
fi

# Block until chrony reports the clock is disciplined to within ${tol}
# seconds, polling up to ${tries} times (~tries seconds).  A non-zero
# exit means we never converged and capture should NOT start.
if ! chronyc waitsync "${tries}" "${tol}" >/dev/null 2>&1; then
    echo "ntp-after-online: chronyc waitsync did not converge within ${tries} tries" >&2
    exit 1
fi

echo "ntp-after-online: clock disciplined (tries=${tries}, tol=${tol}s)"
exit 0
