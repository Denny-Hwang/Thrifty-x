# Post-Fix Re-Validation — 2026-08-28 (master @ 6259e20)

**Scope:** Full re-verification of the latest `master` after the
validation-campaign fixes landed (PRs #63–#66), against the pre-fix
baseline 7268bfb. Four independent adversarial tracks — (a) C tree,
(b) Python data path/settings/capture CLI, (c) HAL + DSP, (d) rpi
deployment + documentation — each re-reviewed the cumulative diff,
rebuilt/ran the code, and probed the fixes empirically. No files were
modified during verification.

---

## 1. Executive verdict

**All 33 fixes claimed by PRs #63–#66 are VERIFIED CORRECT on master —
no regression was found in any of them.** Every gate is green:

| Gate (master 6259e20) | Result |
|---|---|
| `pytest -q` (local, Py 3.11) | **394 passed, 6 skipped** |
| `ruff check thriftyx/ tests/` (ruff 0.16.5) | clean |
| `mypy thriftyx/` | clean (40 files) |
| GitHub CI on master (run #132) | **success** (lint, 3.10 & 3.13, C builds, round-trip gate) |
| fastcapture / fastdet builds | clean; **zero warnings** at `-Wall -Wextra` in every changed file |
| `scripts/c_card_roundtrip_check.py` | PASS (both passes, sample-exact incl. 65536-block case) |

Empirical highlights from the re-verification:

- `libairspy_version()` **called against the real installed libairspy**
  returned `1.0.11` via the `airspy_lib_version(struct*)` API; both
  ctypes structs (`airspy_transfer`, `airspy_lib_version_t`) match the
  installed `airspy.h` exactly (`sizeof` 48 / 12).
- `freq_shift` is now **byte-identical to legacy Thrifty**
  (`np.array_equal` over multiple sizes/shifts).
- The circular interpolator wrap is mathematically consistent with the
  periodic Dirichlet model: probes at both spectrum edges recover the
  true sub-bin offset to ~1e-15 where the original raised IndexError.
- fastdet `-x` export blocks are sample-exact against the raw input
  including the 4920-pair history overlap, and re-read byte-identically
  by `fastcapture --card`; the Release-build runtime history check
  fires with the clear message (exit 255, NDEBUG confirmed).
- Stock `capture` config for every device type now validates with
  **zero warnings** (rate + bit-depth defaults derived per device).
- The first-header-wins / endian / sample-rate-mismatch / truncated-line
  behaviors are all exercised by tests and re-probed adversarially.

## 2. New findings from this pass

The fixes themselves are sound; the re-verification surfaced a small
set of *residual* issues, almost all pre-existing or interaction-level.
Two are MAJOR and worth fixing promptly; none affect the recommended
Python capture→detect pipeline.

### Major

| ID | Where | Finding |
|---|---|---|
| R1 | `rpi/detector.service` + `rpi/fastdet.sh` | The unit cannot run its own script: `NoNewPrivileges=true` blocks the script's `sudo chronyc makestep`, and `ProtectHome=read-only` blocks its `/home/pi/detector` writes → `set -e` exit, 120 s restart loop, `StartLimitBurst` lockout. The hardening block was copied from `thriftyx-capture@.service` where it is safe (writes go to `/var/lib/thriftyx`). Standalone (non-systemd) invocation works. Fix: drop `NoNewPrivileges`/relax `ProtectHome` for this unit, or move the sync/paths out of `/home` and out of sudo. |
| R2 | `rpi/cleanup_old_captures.sh:42-44` | The emergency-purge loop aborts exactly when needed: with >~1500 card files, `find \| sort \| head -1` dies of SIGPIPE under `set -euo pipefail` (reproduced: 3000 files → exit 141, 0 purged); a missing `card/` dir also aborts (find exit 1) instead of `break`. The `-mmin` grace exclusion itself is correct. Fix: tolerate the pipeline status (e.g. `\|\| true` around the substitution or `sort -n \| head` replaced with `awk 'NR==1'`-safe forms) and guard `[ -d card ]` in the purge loop. |

### Minor

- R3 — `README.md:244`: the auto-adjust example says `12280`; the code
  and user guide produce **12278** (`compute_block_params(6e6, …)` →
  32768/12278/6139).
- R4 — `fastcapture/airspy_reader.c:215`: the new `-d` range check
  casts to `int`; `--device-index` ≥ 2^31 wraps negative, bypasses the
  check, and indexes `serials[]` out of bounds. Compare unsigned.
- R5 — `fastdet -x -` / `fastcard_cli -o -` (stdout) skip
  `fargs_print_card_header`, so a piped card is headerless and Python
  decodes it as 8-bit. Interaction of the (correct) `CFile "-"` fix
  with a pre-existing header condition; emit the `#v2` line on stdout
  too.
- R6 — `fastcapture/raw_reader.c`: `-h` equal to `-b` (new_len 0)
  loops forever re-emitting the same block (`fastcard_new` rejects only
  `history > block`). Pre-existing, unchanged since the baseline.
- R7 — `block_data.card_reader`: an all-corrupt file yields one warning
  per line and a "successful" empty run (no terminal summary/escalation);
  a truncation landing on a 16-char base64 boundary slips through as a
  silent short block — the header's new `block_size` field is not yet
  used to validate block length. Both strictly better than the pre-fix
  hard crash; polish opportunities.
- R8 — `rpi/detect.sh` (legacy RTL pipeline) requires the upstream
  `fastcard` binary on PATH; this repo builds `fastcapture` (sentinel
  `"airspy"`), so without legacy fastcard the pipeline exits. Mitigated
  by the explicit "legacy" labeling in `detector.cfg`.

### Nits

`read_sync(0)` returns before the misuse check; even-`width`
interpolator windows duplicate one wrapped bin on degenerate tiny FFTs
(legacy-inherited); `parse_airspy_serial` masks >64-bit and negative
inputs instead of raising; `serials[32]` unclamped for >32 devices;
`fargs_type.h` comment still says "0-15 R2"; carrier SNR prints
`inf dB` when the clamped noise is 0 (display-only; matches Python);
`circbuf` stats read unlocked (safe in actual call order);
`airspy_reader_close` has no callers; `detector.service` Description
says "detect-from-fastcard" but runs fastdet; `rpi/installation.md`
(legacy Pi 3 doc) clones `~/thrifty` while the unit expects
`/home/pi/thrifty-x`; `heartbeat.py` docstring cites a nonexistent
`rpi5_runbook_ko.md`; README calls heartbeat "written to a known path"
(it writes to stdout/journald/URL); explicit-small block params warn
twice (once per rate) on the capture path; a v1→v2 concatenation
switches decode width at the first header silently (arguably correct);
garbage `bit_depth=` header values raise without file context;
`_apply_device_default_bit_depth` lacks a dedicated unit test.

## 3. Per-area verdict summary

| Area | Fixes re-verified | Verdict |
|---|---|---|
| C tree (fastcapture/fastdet) | 8 | **All VERIFIED CORRECT**; residual R4–R6 |
| Python data path / settings / CLI | 6 | **All VERIFIED CORRECT**; residual R7 |
| HAL (airspy_mini / airspy_r2) | 7 | **All VERIFIED CORRECT**; nits only |
| DSP (carrier_sync / stat_tools / soa) | 4 | **All VERIFIED CORRECT** (freq_shift byte-equal to legacy; soa_estimator diff empty) |
| rpi deployment | — | fastdet.sh/cfg, detect.sh, configs, soak, heartbeat verified; **R1 (service) and R2 (cleanup purge) DEFECTIVE** |
| Docs / CI | — | README behavioral-fix bullets, capture tables, validation-doc claims all match code; CI round-trip wiring replicated locally and passes; R3 number drift |

## 4. Conclusion

The hardware-upgrade refactoring, including the full fix campaign, is
**verified sound on master**. The recommended pipeline (Python
`capture` → `detect` → … → `pos`) and the C capture/detect path are
both exercised end-to-end and correct. The remaining work is confined
to two rpi operational scripts (R1, R2), one README number (R3), and a
short list of minor/nit polish items — none of which affect captured
data or positioning results.
