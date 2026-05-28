# Raspberry Pi 5 RX Node Operations Runbook

This document summarizes the daily inspection and incident response
procedures for an unattended RX node built with Pi 5 + Airspy. For the
installation procedure, see `rpi/installation_pi5.md`; for the deployment
analysis/plan, see `docs/rpi5_deployment_report.md`.

---

## 1. Daily Inspection (weekly)

```bash
# Service status
systemctl status thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 --since "24 hours ago" | tail -50

# Disk
df -h /var/lib/thriftyx

# Time synchronization
chronyc tracking

# Heat / throttling
vcgencmd measure_temp
vcgencmd get_throttled       # 0x0 means normal

# Airspy recognition
python3 -c "from thriftyx.hal.airspy_mini import list_airspy_serials; print(list_airspy_serials())"
```

---

## 2. Failure Scenarios and Response

### 2.1 Capture service repeatedly restarts
- Check the last stack/error with `journalctl -u thriftyx-capture@rx0 -n 200`
- `airspy_open() failed`: Inspect USB cable/hub/power → after replacement,
  `systemctl restart thriftyx-capture@rx0`
- `DeviceConfigError`: Verify that the sample_rate in `capture.cfg` is within
  the device's supported range (Mini: 3M/6M, R2: 2.5M/10M)

### 2.2 Disk shortage
- Whether the cron `cleanup_old_captures.sh` ran: `journalctl -t thriftyx-cleanup`
- Temporary measure: `find /var/lib/thriftyx/card -type f -mtime +1 -delete`
- Change the retention policy: set `CARD_RETENTION_DAYS=N` in `/etc/default/thriftyx-capture@rx0`

### 2.3 Throttling/heat
- Normal: `get_throttled` = `0x0`
- Bits 16/17/18 set → throttling occurred in the past. Clean/reseat the cooler,
  ensure case ventilation, and lower `arm_freq` slightly if necessary.

### 2.4 Time synchronization anomaly
- If the `Last offset` of `chronyc tracking` is more than ±10 ms, there is an NTP
  source problem. Compare `chronyc sources -v` across nodes.
- Allow temporary free-run during a WAN disconnect. It automatically re-synchronizes
  after recovery.

### 2.5 Data transfer failure between node and server
- Check the rsync log. Whether `~/.ssh/known_hosts` has expired.
- A network disconnect is independent of the capture itself — the capture
  continues to accumulate locally.

---

## 3. 24-hour Soak Test Procedure

The automation script (`rpi/soak_test.sh`) is recommended — 24h capture + health
sampling every minute (CSV) + automatic PASS/FAIL determination.

```bash
sudo systemctl stop thriftyx-capture@rx0
~/thrifty-x/rpi/soak_test.sh
# → /var/lib/thriftyx/soak/<timestamp>/{summary.txt,samples.csv,capture.card,...}
echo "exit=$?"   # 0=PASS, 1=FAIL, 2=setup error
```

Automatic determination criteria (can be overridden with environment variables):
- Capture exit code == 0
- `vcgencmd get_throttled` is `0x0` for the entire run
- Peak CPU temperature ≤ 80°C (`MAX_TEMP_C`)
- RSS memory growth rate ≤ 10% (median of the early vs. late portions, `MAX_MEM_GROWTH_PCT`)
- Disk free ≥ 10% (`MIN_DISK_FREE_PCT`)
- `.card` file header integrity

When you want to run it manually:

```bash
sudo systemctl stop thriftyx-capture@rx0
source ~/thrifty-x/.venv/bin/activate

OUT=/var/lib/thriftyx/soak/$(date +%Y%m%dT%H%M%S)
mkdir -p "$OUT"

nohup thriftyx capture "$OUT/capture.card" \
    --config /var/lib/thriftyx/capture.cfg \
    --duration 86400 > "$OUT/stdout.log" 2> "$OUT/stderr.log" &

echo $! > "$OUT/pid"
```

Pass determination after 24 hours:
- Process exit code 0
- `card` file size monotonically increasing, no corruption
- `vcgencmd get_throttled` = `0x0`
- Memory usage stable (peak vs end < 10% difference)
- Dropped-sample count (if any) below the allowed threshold

---

## 4. Health Check / Heartbeat

`rpi/heartbeat.py` + a systemd timer emit one line of JSON every 60 seconds.
The default is journald logging; when `THRIFTYX_HEARTBEAT_URL` is set, it
additionally POSTs.

Installation:

```bash
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.service /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.timer   /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.env.example /etc/default/thriftyx-heartbeat
sudo $EDITOR /etc/default/thriftyx-heartbeat   # RXID, OUT, optional URL
sudo systemctl daemon-reload
sudo systemctl enable --now thriftyx-heartbeat.timer
journalctl -t thriftyx-heartbeat -f
```

Payload schema (HTTP POST JSON, every 60 seconds):

```json
{
  "rxid": 0,
  "ts": "2026-05-06T12:34:56Z",
  "uptime_s": 123456,
  "disk_pct": 42,
  "cpu_temp_c": 58.3,
  "throttled": "0x0",
  "service_state": "active",
  "last_detection_ts": "2026-05-06T12:34:50Z",
  "dropped_samples": 0,
  "version": "0.1.0"
}
```

Alert when not received within 60 seconds at the server. The receiving endpoint
is built with separate infrastructure (Nginx + a simple sink).

---

## 5. Remote Access

The reverse SSH section in the existing `rpi/installation.md` remains valid as-is
on the Pi 5 (autossh + systemd). However, the weaved section is deprecated —
ignore it.

---

## 6. Update Procedure

Recommended: `rpi/update_node.sh` (idempotent wrapper, automatic rollback).

```bash
sudo install -m 755 ~/thrifty-x/rpi/update_node.sh /usr/local/bin/
ssh rx0 'sudo /usr/local/bin/update_node.sh'
```

Behavior:
1. No changes after `git fetch` → exit 0 (no-op)
2. `git merge --ff-only` fails → exit without affecting the service
3. `pip install` / `restart` / `is-active` verification after 30 seconds
4. Failure at any step → automatic rollback to the previous SHA + reinstall + restart
5. On success, record the new SHA in `~/thrifty-x/.last_known_good_sha`

Exit codes:
- `0` up to date or update succeeded
- `1` update failed but rollback succeeded (running on the old version)
- `2` both update and rollback failed (immediate human intervention required)
- `3` setup error (working tree dirty, no venv, etc.)

Manual procedure (for reference):

```bash
ssh rx0
cd ~/thrifty-x
git fetch origin
git log --oneline HEAD..origin/master
git pull --ff-only
source .venv/bin/activate
pip install -e ".[analysis,fft]"
sudo systemctl restart thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 -f
```
