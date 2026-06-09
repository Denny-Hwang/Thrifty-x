# Comprehensive A–Z Refactoring Review

**Date:** 2026-06-09
**Scope:** Full-repository audit of whether every feature matches the
original refactoring goals (Airspy Mini/R2 support, Python 3.10+
modernisation, unified Qt viewer, Pi 5 deployment, preserved DSP
pipeline, C capture/detect parity, CI gating).
**Method:** (a) local re-run of every CI gate; (b) a synthetic
end-to-end positioning experiment driven through the real CLIs;
(c) line-by-line review of the HAL/capture layer and of all 22
documentation files; (d) targeted claim-by-claim verification of the
DSP, CLI/viewer, C, and deployment layers.

Verification depth varied by area: the HAL/capture layer and the
documentation set received exhaustive line-by-line review; the DSP,
CLI/viewer, C, and rpi/ layers received targeted verification of every
documented claim plus functional execution, not a full line-by-line
pass.

---

## 1. Executive verdict

The refactoring goals are **substantially complete and real**. All
local CI-parity gates pass, the full pipeline works end-to-end on
synthetic data, and the flagship claims (sub-offset bounds, /2048
normalisation, fail-fast sample type, gain-mode delegation, v1/v2
format support, 336-test suite) are confirmed in code with tests.

Three defects contradicting documented contracts were found and are
**fixed in this PR** (§5).  A further set of open findings — none of
which block the documented workflows — is catalogued in §6 and §7 for
follow-up.

| Gate (CI parity, run locally) | Result |
|---|---|
| `pytest -q` | **339 passed** (336 before this PR; 3 regression tests added here) |
| `ruff check thriftyx/ tests/` | clean |
| `mypy thriftyx/` | clean (40 source files) |
| `cmake` build `fastcapture` | builds; `--help` OK |
| `cmake` build `fastdet` (links installed fastcapture) | builds; `--help` OK |
| `systemd-analyze verify` capture unit | **failed before this PR** (fatal); clean after |

---

## 2. End-to-end functional verification (new evidence)

The README admits the `detect → identify → match → tdoa → pos` chain is
covered by unit tests only.  To close that gap for this review, a
synthetic experiment was run through the **real CLIs**:

- 3 receivers at (0,0), (40000,0), (20000,30000) m; per-receiver clock
  bias of 0 / +12345.6 / −7890.1 samples.
- TX0 beacon at (20000,10000), TX1 mobile at (25000,15000); OOK
  Gold-code bursts (1023 chips, 2455-sample template @ 2.4 Msps,
  carrier bins 100/120), 20 + 15 transmissions over 8 s embedded in
  noise; v2 12-bit `.card` files written via `block_data`.

Results:

| Stage | Result |
|---|---|
| `detect` (×3) | 49/47/49 detections (duplicates expected from block overlap) |
| `identify --map` | 145 → 105 = exactly 3 rx × 35 TX bursts; 40 duplicates removed |
| `match` | 35 matches, 0 misses, 0 collisions |
| `tdoa` | 15/15 mobile TDOA estimations, 0 failures |
| `pos` | all 15 solutions ≈ (25021, 14911) vs truth (25000, 15000) |

Position error ≈ 21–89 m (0.17–0.7 sample at 125 m/sample) with
estimate spread < ±0.3 m — i.e. the beacon clock-sync cancelled the
±12 k-sample clock biases correctly and the LM solver is consistent;
the residual is dominated by the integer-sample placement of the
synthetic bursts.  `analyze_detect --export` was also exercised
headless and produced all five plot families per block.

This experiment is what exposed findings F1 and F2 below: both sit in
CLI seams whose line coverage is < 45 %.

---

## 3. Claim-by-claim goal matrix

Confirmed against code (file:line spot checks; see
`docs/verification/` for the underlying single-topic reports):

| # | Goal / claim | Verdict |
|---|---|---|
| 1 | Airspy Mini + R2 HAL via ctypes; select by index or 64-bit serial; `list_airspy_serials` exported | CONFIRMED (`thriftyx/hal/airspy_mini.py:223-346`, `hal/__init__.py:12,26`) |
| 2 | RTL-SDR retained (8-bit, fastcard path, auto-detected) | CONFIRMED (`airspy_capture.py:636-653`; NOTICE wording fixed in this PR) |
| 3 | Python 3.10+; ruff + mypy gated in CI | CONFIRMED (`pyproject.toml`, `.github/workflows/ci.yml`) |
| 4 | Unified Qt viewer: one window, two `QTabBar`s, shared canvas, lazy plotters, PyQt5→PySide6 fallback, `--no-gui`/`--export`/`--save` | CONFIRMED structurally (`detect_analysis.py:656-657,769,775,993,1134,1137`); README's `--prefer-qt` flag did not exist → README fixed in this PR |
| 5 | 3-stage gain + `--gain-mode` presets delegating to libairspy; `--combined-gain` 0–21 (0=min, libairspy inverts); AGC flags manual-only | CONFIRMED (`airspy_mini.py:554-627`; caveat: "presets force AGC off" happens inside libairspy and is not pinned by a test) |
| 6 | `--ppm`, `--packing`, `--bias-tee` (warning) | CONFIRMED (`airspy_mini.py:461-470,629-644,511-521`) |
| 7 | 12-bit normalisation `/2048.0` (native range, not left-shifted) | CONFIRMED (`block_data.py:83-89`, `fastcapture/rawconv.c:32-41`, pinned by `test_block_data_range.py`).  Three stale "full int16 / 32768" comments cleaned up in this PR |
| 8 | Fail-fast on `airspy_set_sample_type` failure | CONFIRMED (`airspy_mini.py:360-373` raises `DeviceConfigError`, closes handle; regression test exists) |
| 9 | Carrier sub-offset bounds `[-0.5,0.5]`; corr clip ±0.6; fastdet ±0.5 | CONFIRMED (`carrier_sync.py:210`, `soa_estimator.py:20,108`, `fastdet/corr_detector.cpp:97-113`) |
| 10 | v1/v2 `.card` sniffer; header wins | **was REFUTED at the CLI seam — fixed in this PR** (F1) |
| 11 | Deliberate default changes limited to `freq_shift_method=integer`, `soa_interpolation=parabolic`, both overridable | CONFIRMED (`carrier_sync.py:106-122,272-275`, `soa_estimator.py:71-92`) |
| 12 | `identify --map` + auto-classifier with multi-TX nudge | CONFIRMED (`identify.py:111-116,125-140`) and exercised E2E |
| 13 | TDOA beacon sync + LM position solve preserved (incl. patched `thrifty/` reference) | CONFIRMED functionally by the E2E experiment (§2) |
| 14 | 33 test modules / 336 tests; CI runs lint+type+pytest+2 C builds | CONFIRMED (now 339 tests) |
| 15 | Pi 5 deployment kit complete (units, env, heartbeat, soak, idempotent update, cleanup) | Files all exist; **capture unit had a fatal config error — fixed in this PR** (F3) |
| 16 | Packaging: pyproject + setup.py, dynamic version, `thrifty` alias, `thrifty/` not packaged | CONFIRMED |
| 17 | fastcapture/fastdet build in CI; cmake ≥ 3.10 | CONFIRMED (built locally; `CMakeLists.txt:1,7`) |

---

## 4. Documented-but-unfinished items (as admitted by the docs)

Not regressions — listed so the completion picture is honest:

1. Multi-receiver TDOA field documentation — "Future Work"
   (`user_guide.md` §11).
2. Multi-template support — design proposal only
   (`docs/design/multi_template_proposal.md`).
3. Threshold sweep — template only; needs the 5/14 field `.card`s
   (`threshold_sweep.md`).
4. Pi 5 on-device validation — checklist 100 % unchecked
   (`rpi5_validation_checklist.md`); heartbeat server endpoint not built.
5. Hot-plug recovery; C binaries smoke-built only (README Known
   Limitations).
6. Auto-classifier ≥40:1 uneven-population limitation (documented,
   `--map` recommended).
7. Type hints still rolling out module-by-module.
8. Post-fix re-analysis of the 5/14 field data still owed
   (`sub_offset_recheck.md` §"follow-up").

---

## 5. Defects found by this review — FIXED in this PR

### F1 (Major) — v2 `.card` header was silently overridden by configured bit depth
`detect` and `analyze_detect` always pass the configured `bit_depth`
(default **8**) into `card_reader`, and `card_reader` only honoured the
`#v2 bit_depth=12` header when the argument was `None`
(`block_data.py`).  Net effect: a 12-bit Airspy card processed with a
default/RTL config failed with a misleading
`Block length 32768 does not match expected 16384` (demonstrated live
in §2), the in-code comments claimed the opposite precedence, and the
README's "auto-detected by the v1/v2 header sniffer" contract was
false at the CLI seam.
**Fix:** header now always wins; a conflicting explicit value logs a
warning; comments updated; 2 regression tests added
(`tests/unit/test_block_data.py::TestCardReader::test_v2_header_wins_over_stale_bit_depth_arg`,
`::test_v1_headerless_uses_bit_depth_arg`).

### F2 (Minor) — `tdoa -s` rejected metric notation
`tdoa -s 2.4M` → `argparse error: invalid float value`, while every
other command's `-s` accepts metric suffixes — an inconsistency wired
in `tdoa_est._main` (`type=float`).
**Fix:** `type=metric_float`; help text updated; regression test added
(`tests/unit/test_tdoa_sample_rate.py::test_cli_flag_accepts_metric_suffix`).

### F3 (Critical, deployment) — Pi 5 capture unit could never start
`rpi/systemd/thriftyx-capture@.service` used
`User=${THRIFTYX_USER}` / `WorkingDirectory=${THRIFTYX_HOME}` —
systemd does **not** expand environment variables in those directives.
`systemd-analyze verify` reported *"Unit configuration has fatal
error, unit will not be started"* on the unit exactly as installed by
`rpi/installation_pi5.md` §7.  Additionally `StartLimitIntervalSec`
sat in `[Service]` where systemd ignores it (in both the capture unit
and the legacy `detector.service`), so restart rate-limiting was
silently inactive.
**Fix:** literal `User=pi` / `WorkingDirectory=/home/pi/thrifty-x`
with an override note (`systemctl edit`), `THRIFTYX_USER` removed from
the env example, start-limit keys moved to `[Unit]` in both units,
installation doc updated.  `systemd-analyze verify` is now clean.

### F4 (Doc corrections bundled here)
- `NOTICE`: "Replaced RTL-SDR hardware support" → extended (RTL-SDR
  remains supported); "8-bit to 12-bit" → "8/12-bit".
- `fastcapture/README.md`: stale `val / 32768.0` + "full int16 range"
  table row corrected to `/2048.0` / native 12-bit.
- `README.md`: nonexistent `--prefer-qt/--no-prefer-qt` flag row
  replaced with the real behaviour (`--no-gui`; `prefer_qt` is a
  Python-API knob).
- Stale "full int16 / left-shift" rationale comments corrected in
  `fastcapture/airspy_reader.c`, `tests/mocks/signal_generator.py`,
  `tests/integration/test_full_pipeline.py` (these were the exact
  comments most likely to re-introduce the /32768 regression).

---

## 6. Open findings — code (recommended follow-ups, not fixed here)

Severity ordered.  None breaks a documented workflow today.

| ID | Sev | Finding |
|---|---|---|
| O1 | Major | **Unbounded sample queue in `read_sync`** (`airspy_mini.py:680-683`): no backpressure/drop accounting; a persistently slow consumer (e.g. no pyfftw at 6 MSPS on a Pi) grows RSS until OOM. The C path uses a bounded `circbuf`. Recommend a bounded deque + software-drop counter. |
| O2 | Major | **Partial ctypes-binding failure leaves `_lib` non-None** (`airspy_mini.py:29-151`): on older libairspy missing e.g. `airspy_open_sn`, later bindings are untyped, `airspy_init` never ran, and a user-pinned `--airspy-serial` is silently ignored (wrong board in a multi-RX rig). Recommend `_lib = None` reset or granular feature flags. |
| O3 | Major | **No test executes the real ctypes streaming core** (`_start_rx`/`_c_callback`/`read_sync`/timeout): all capture tests use hand-rolled fakes; `_capture_rtlsdr*` has no tests at all. |
| O4 | Minor | `DeviceConfigError` from `open()` escapes `_capture_airspy` as a raw traceback (`airspy_capture.py:400-414` catches the wrong exception set for the fail-fast path). |
| O5 | Minor | `block_history=0` ⇒ `raw[-0:]` returns the whole buffer — ever-growing blocks on the library (non-validated) surface (`airspy_capture.py:484,546`, `block_data.py:165`); `read_sync(0)` crashes in `np.concatenate`. |
| O6 | Minor | R2 LNA range advertised 0–15 (`airspy_r2.py:20`, `config_validator.py:26`) vs 0–14 everywhere else (hardware clamps at 14). |
| O7 | Minor | Manual gain mode hard-fails on AGC-stripped libairspy builds even with AGC off (`airspy_mini.py:618-619`); should no-op + warn like `set_packing`. |
| O8 | Minor | 16-digit pure-decimal serials silently parsed as hex (`airspy_mini.py:258-261`). |
| O9 | Minor | `--duration` ignored on the RTL/fastcard path; `--input` ignored for Airspy; no warning either way (`airspy_capture.py:175-232,598-600`). |
| O10 | Minor | fastcard child not reaped on Ctrl-C (`airspy_capture.py:227-232`); capture signal handlers never restored; SIGINT during a stalled read waits out the full 10 s timeout. |
| O11 | Minor | `open()` not idempotent (second open leaks a handle); `start_capture()` silently swaps callbacks mid-stream (`airspy_mini.py:334-358,646-653`). |
| O12 | Info | `dropped_samples` unit (pairs vs raw samples) unverified against libairspy; block-index drift ×2 possible during overflow accounting (`airspy_capture.py:506-514`). |
| O13 | Info | Dead code: `_bit_depth_for_device` (`airspy_capture.py:93-97`), unused `airspy_r2` attrs, `kitchen_sink.py` and `experimental/` at 0 % coverage; root `Makefile` `docs`/`venv` targets are broken legacy (no Sphinx tree). |
| O14 | Info | `.card` int16 payload endianness is native (LE-only de facto); `_compute_threshold` duplicates `carrier_detect._calculate_threshold`; truncated final card line raises instead of skipping. |

Line coverage snapshot (this review): total **42 %** — strong on the
DSP core (carrier_detect 96 %, block_data 94 %, signal_utils 91 %,
soa_estimator 88 %) and thin exactly where F1/F2 lived (detect 43 %,
identify 43 %, matchmaker 24 %, tdoa_est 22 %, detect_analysis 16 %,
scope 6 %).  Recommend a CLI-seam smoke test per stage (the §2
synthetic harness is a ready-made basis).

## 7. Open findings — documentation consistency

High-confusion items first; all are doc-only:

1. `fastdet/README.md` is an untouched upstream relic (librtlsdr/
   libfastcard instructions) contradicting the fastcapture link
   story it actually builds with.
2. Frequency-range disagreements: Mini 24–1800 (README) vs 24–1700
   (user guide); RTL ~24–1700 vs 24–1766.
3. Stale point-in-time test counts inside older verification docs
   (`gain_mode_state.md` "16 tests" vs 14 today;
   `auto_classify_*.md` 30/21 vs 17 today).  The READMEs' own 33/336
   figure was accurate (now 339).
4. README "Repository Layout" omits `diag/` (and `Makefile`,
   `pylintrc`, `tests/mocks/`); "All documentation is in English" is
   violated by the Korean `diag/phase_a_code_analysis.md` (move under
   a clearly-marked historical section or translate).
5. `user_guide.md:848` claims the unified viewer layout matches
   original thrifty; README says the opposite (README is right).
6. Pi 5 table lists legacy assets (`detector.service`,
   `ntp-after-online.*`, pyFFTW 0.9.2 patch) without legacy marking;
   the deployment report says chrony replaces ntp-after-online and the
   fftwl patch is unnecessary on 64-bit.
7. Default `--device-type airspy_mini` + default `--sample-rate 2.4M`
   are mutually unusable (Mini supports 3M/6M) — pick a coherent
   default pair or document the required override.
8. Smaller: `example/detector_r2.cfg` comment says LNA 0-15; broken
   README anchor `#gain-tuning`; `user_guide` `[all]` description
   omits `gui`; `threshold_path.md` references an internal
   "Prompt 5"; boolean flags (`--bias-tee` etc.) are `type=str`, so
   bare `--bias-tee` errors although README tables read like
   store-true switches; `diag/` reports describe the pre-fix
   `/32768.0` and warning-only sample-type states without a
   "historical snapshot" banner.

---

## 8. Bottom line

- **Goal completion:** the refactoring's advertised feature set is
  implemented, tested at 339 tests, CI-gated, and now proven
  end-to-end on synthetic data through the real CLI chain.
- **Fixed here:** the three contract-breaking defects (v2 header
  precedence; `tdoa -s` parser parity; an un-startable Pi 5 capture
  unit) plus the highest-risk stale docs/comments.
- **Largest remaining risks** for unattended field deployment:
  O1 (unbounded capture queue), O2 (degraded-libairspy serial
  selection), O3 (untested streaming core) — recommended as the next
  hardening PR, together with the §7 doc sweep.
