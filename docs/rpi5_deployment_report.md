# Raspberry Pi 5 Deployment Analysis and Execution Plan (Standalone RX Site)

Date: 2026-05-06
Target repository: `Thrifty-x` (branch `claude/raspberry-pi-deployment-c3WTH`)
Preceding PR: #25 (Codex, Pi5 deployment report draft)

> This document augments the draft from PR #25 (Codex) with **actual code analysis results**,
> and includes in this PR the deliverables that the draft identified as P0 but were not actually added
> (Pi5-specific installation document, systemd templates).

---

## TL;DR

- **Deployability:** High. From a code/dependency standpoint, operation on Pi 5 (64-bit) is feasible.
- **Immediate unattended-operation readiness:** **Not yet.** Requires applying the P0 items below + on-device validation.
- **What this PR resolves:** Pi5-specific installation guide, systemd templates, operations runbook, and a summary of code-level risk items.
- **Next step:** Complete one full run of `docs/rpi5_validation_checklist.md` on an actual Pi 5 + Airspy Mini/R2 combination.

---

## 1) Code/Configuration-Level Analysis (Added to the Codex draft)

### 1.1 Dependencies — Pi 5 (Bookworm aarch64) Compatibility

| Item | Status | Notes |
|---|---|---|
| `numpy>=1.23`, `scipy>=1.9` | OK | piwheels/manylinux2014_aarch64 wheels provided |
| `matplotlib>=3.6` (optional) | OK | `MPLBACKEND=Agg` recommended for unattended nodes |
| `pyfftw>=0.13` (optional) | Conditionally OK | apt `python3-pyfftw` or pip; `libfftw3-dev` required when building. **The legacy fftwl patch is unnecessary** (64-bit environment) |
| `libairspy` | **Version caution** | The HAL uses `airspy_open_sn`, `airspy_list_devices`, `airspy_get_samplerates`, `airspy_set_packing`. The Bookworm `airspy` package (1.0.10) provides all of them. Older builds may silently ignore the `serial`/`packing` options. (`thriftyx/hal/airspy_mini.py:340-352`, `:618-633`) |
| `fastcard`/`fastdet` C binaries | **Not required** | This fork only invokes fastcard on the RTL-SDR path. On Airspy-operated nodes, building/installing is not required. The fastcard/libvolk/fftwl procedures in the existing `rpi/installation.md` are unsuitable for Pi 5. |

### 1.2 Capture Hot-Loop Performance Issue (Newly Identified)

The capture loops at `thriftyx/airspy_capture.py:275`, `:454` call `np.fft.fft`
directly. The same repository's `thriftyx/signal_utils.py` already has a
`compute_fft()` that automatically accelerates when pyfftw is installed,
but it is not used in the capture path.

- Impact: On the Pi 5 Cortex-A76, with a combination of 6 MSPS (Airspy Mini) or 10 MSPS (R2) +
  large `block_size` (e.g., 16384/32768), the single-thread FFT can
  become a bottleneck. pyfftw accelerates by 2~5× via plan caching
  when the same size is used repeatedly.
- Recommended improvement: Use `compute_fft` in the capture loop, or create a
  `pyfftw.builders.fft(block_size)` plan once at the module
  level and reuse it. (P1, code change required — outside the scope of this PR)

### 1.3 SD Card Wear Issue (Newly Identified)

At `airspy_capture.py:285,466`, `output_file.flush()` is called for every detection block.
When the detection rate is high, small synchronous writes to the microSD repeat,
which is detrimental to lifespan/latency.

- Recommended: (a) place the output file on a USB SSD/HDD, or (b) tmpfs ring buffer +
  periodic flush (e.g., 1 second/100 blocks), (c) guide external storage as the
  default for node operation. Separate to an external mount via the systemd template
  (`Environment=THRIFTYX_OUT=...`).

### 1.4 Signal Handling and systemd Interaction

- `_capture_airspy` exits with `sys.exit(1)` on `DeviceNotFoundError`/`DeviceConfigError`
  (`airspy_capture.py:351,475,478`). This meshes well with
  systemd `Restart=on-failure` + `RestartSec` — **even if the first attempt fails due to
  USB enumeration delay at boot, it retries automatically.**
- The SIGINT/SIGTERM handler only sets `running[0] = False`, then the `finally`
  block calls `device.close()` — the normal shutdown path is OK.
- However, immediately after boot `airspy_list_devices()` may be empty, so a retry
  5~10 seconds after the first startup failure is needed. `RestartSec=10` is recommended.

### 1.5 PPM/AGC/Packing Options (Impact of PR #22, #23 Merged by Codex)

PR #22, #23 (reflecting Codex reviews) added the following:
- 12-bit USB packing (`--packing` / `packing: true`) — reduces USB bandwidth by ~33% during
  6 MSPS (Mini) / 10 MSPS (R2) operation. **Even if the Pi 5's USB 3.0 supplies power stably,
  enabling packing is safer when going through a USB hub.**
- LNA/Mixer AGC toggle — recommended at sites with large RF environment fluctuations.
- Software PPM correction — since the hardware does not support it, pre-scale the LO.
  **Directly affects TDOA accuracy**; per-node measured values must be applied.

These options are exposed as defaults in this PR's `rpi/thriftyx-capture.cfg.example`
so that field operators can adjust them in one place.

---

## 2) Operating Environment Risks (Augmenting the Codex Draft)

### 2.1 Heat/Throttling (New)

- The Pi 5 throttles its clock at 80℃. Airspy 12-bit 6 MSPS capture has a
  CPU load of ≈ 60~80% (estimated); recommended inside a case: **the official active cooler or
  a heatsink + fan**.
- Monitoring items: `vcgencmd measure_temp`, `vcgencmd get_throttled`.
  Included in the runbook.

### 2.2 Power

- When using the official 27W (5V/5A) USB-C PD adapter, the Pi 5 supplies up to
  1.6 A per USB port. **Other adapters are limited to 600 mA** → USB errors may occur frequently
  on the Airspy R2 (especially with bias-tee on).
- Specifying `usb_max_current_enable=1` in `/boot/firmware/config.txt` is recommended.

### 2.3 Time Synchronization

- For TDOA, node time accuracy is critical. Switch to `chrony` (lower jitter than the default `systemd-timesyncd`).
  On unattended nodes, to avoid starting capture before NTP synchronization completes,
  specify `After=time-sync.target` and `Wants=time-sync.target` on the capture service.
  (The old `rpi/ntp-after-online.*` is replaced by
  `systemd-time-wait-sync` or chrony.)

### 2.4 Storage Policy

- microSD-only operation is prohibited (recommended). Mount a USB SSD at `/var/lib/thriftyx`.
- Rotational deletion: clean up files older than N days via `systemd-tmpfiles` or cron.
- Implement disk 80%/90% threshold alerts via `node_exporter` or a simple cron script.
  (See the runbook)

---

## 3) Deliverables of This PR (P0 Complete)

| Deliverable | Path | Status |
|---|---|---|
| Pi 5-specific installation guide | `rpi/installation_pi5.md` | **Added** |
| systemd capture service template | `rpi/systemd/thriftyx-capture@.service` | **Added** |
| systemd environment configuration example | `rpi/systemd/thriftyx-capture@.env.example` | **Added** |
| Capture configuration example (Airspy Mini/R2) | `rpi/thriftyx-capture.cfg.example` | **Added** |
| Disk cleanup cron example | `rpi/cleanup_old_captures.sh` | **Added** |
| Operations runbook | `docs/rpi5_runbook.md` | **Added** |

With this, the Codex draft §3 "P0 — Securing a Deployable State" is all reflected in the
repository in deliverable form. The validation checklist (`docs/rpi5_validation_checklist.md`) was
already merged in PR #25.

---

## 4) Remaining Recommended Work (P1)

The following P1 items were reflected in follow-up commits to this PR:

- [x] **Capture loop FFT acceleration** — In the two capture paths of `airspy_capture.py`,
  replaced `np.fft.fft` → `signal_utils.compute_fft`. Leverages automatic plan caching
  when pyfftw is installed. (§1.2)
- [x] **Periodic flush** — Instead of `flush()` per detection, flush upon reaching `FLUSH_BLOCKS=32`
  or `FLUSH_INTERVAL_S=1.0`. Residual flush at capture termination. (§1.3)
- [x] **Health check/heartbeat** — `rpi/heartbeat.py` + systemd timer
  (`thriftyx-heartbeat.{service,timer}`). Emits one line of JSON every 60 seconds to
  journald, and POSTs when `THRIFTYX_HEARTBEAT_URL` is set.

- [x] **24-hour soak test automation script** — `rpi/soak_test.sh`.
  24h capture + per-minute CSV sampling (RSS, temperature, throttled, disk,
  card size) + automatic PASS/FAIL determination + summary.txt generation.
- [x] **Idempotent remote update wrapper** — `rpi/update_node.sh`.
  fetch → ff-only → pip install → restart → `is-active` verification after 30s;
  if any step fails, automatic rollback to the previous SHA.
  Records `~/thrifty-x/.last_known_good_sha`.

Remaining P1 (optional, possible in a follow-up PR):

- Health-check receiving endpoint (server-side, Nginx + sink). The node side is complete.

---

## 5) Acceptance Criteria for "Runs Without Problems" (Unchanged)

1. Functionality: capture → detect → (optional) identify/match/tdoa/pos pipeline works normally.
2. Stability: 24-hour unattended operation, at least one automatic recovery verified.
3. Operability: logging/alerting/remote access/recovery procedures fully documented.

This PR completes (3) as a first pass; (1)/(2) move to the on-device validation stage.

---

## Appendix A — Summary of Codex Merge Analysis

| PR | Merge Content | Additional Recommendations from a Pi5 Perspective |
|---|---|---|
| #25 | Pi5 deployment report + validation checklist (KR) | Augmented by this document — P0 deliverables added, code-level risks added |
| #23 | AGC/PPM/packing/legacy C dead code removal | Expose defaults in `rpi/thriftyx-capture.cfg.example` |
| #22 | Codex+senior review parity fixes | Improves HAL stability; no impact on this PR |
| #20 | Airspy block index parity | Time-series/timestamp integrity — verify during 24h soak |
| #18,19 | gain default 7/7/7, 4 accuracy bugs | Reflected in the gain defaults of `cfg.example` |
| #15,16 | HAL block_size/sample type/persistent streaming | Positive for Pi5 USB stability |
