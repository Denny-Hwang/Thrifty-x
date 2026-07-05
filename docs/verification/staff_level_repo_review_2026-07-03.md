# Staff-Level Repository Review — Follow-up Audit

**Date:** 2026-07-03
**Scope:** Full-repository review against the original refactoring goals
(Airspy Mini/R2 support, Python 3.10+ modernisation, preserved DSP
pipeline, unified Qt viewer, Pi 5 deployment, C capture/detect parity,
CI gating), performed independently of — and cross-checking — the
2026-06-09 comprehensive audit
(`comprehensive_refactoring_review.md`).
**Method:** (a) local re-run of the CI gates; (b) module-by-module
`diff` of the DSP core against the legacy `thrifty/` reference tree;
(c) line-by-line review of the fixes landed by PR #61 (F1–F3);
(d) targeted line-by-line review of the HAL callback path, the C
`fastcapture`/`fastdet` seams, the Qt viewer, packaging/CI config, and
the `rpi/` deployment scripts.

---

## 1. Executive verdict

The refactoring remains **substantially complete, real, and of
above-average quality for a research codebase**. All CI-parity gates
pass locally (`pytest` 339 passed, `ruff` clean, `mypy` clean — also
verified on Python 3.11 + NumPy 2.4, which CI does not cover). The
three fixes from the previous audit (F1 v2-header precedence, F2
`tdoa -s` metric parsing, F3 systemd capture unit) are implemented
correctly.

The DSP-fidelity contract — *pipeline preserved except two documented
default changes* — is **confirmed at the diff level**: the port not
only preserves the numerics but silently fixes several latent bugs in
the legacy code (§3). New findings from this pass concentrate in the
least-tested seams: the C stop path, the `fastdet` threshold math, and
NaN handling on the default interpolation path (§4).

| Gate (re-run locally, 2026-07-03) | Result |
|---|---|
| `python3 -m pytest -q` | **339 passed** (README still says 336 — stale) |
| `ruff check thriftyx/ tests/` | clean |
| `mypy thriftyx/` | clean (40 files; but see N11 — the gate is shallow) |
| Python version | passes on 3.11 + NumPy 2.4.6 (CI tests 3.10 only) |

---

## 2. Verification of the previous audit's fixes

| Fix | Verdict |
|---|---|
| F1 — `#v2` header wins over configured bit depth | **CORRECT** (`block_data.py:211-219`; warning on conflict; `detect.py:218` passes the arg as fallback only; regression tests present) |
| F2 — `tdoa -s` accepts metric suffixes | **CORRECT** (`tdoa_est.py:29,391-411`; resolution chain CLI → cfg `sample_rate` → cfg `device_type` default → warn) |
| F3 — Pi 5 capture unit | **CORRECT** (`rpi/systemd/thriftyx-capture@.service`: literal `User=pi`/`WorkingDirectory`, `StartLimit*` moved to `[Unit]`, override note present) |

Prior open findings O1–O14 were spot-checked and remain open (notably
O1 unbounded `read_sync` queue and O3 untested streaming core).

---

## 3. DSP fidelity — pipeline-preservation contract

Module-by-module diff of `thriftyx/` against the legacy `thrifty/`
tree confirms the only *deliberate* behavioural changes are the two
documented defaults (`freq_shift_method=integer`,
`soa_interpolation=parabolic`), both overridable and both matching the
legacy algorithm when overridden.

The port additionally **fixes real latent bugs in the legacy code**
without changing numerics (all verified in the diff):

1. `matchmaker.py` / `tdoa_est.py` — legacy
   `sort(cmp=lambda x, y: x.timestamp < y.timestamp)` returned a bool
   (0/1, never −1), i.e. the legacy sort was *incorrect* even on
   Python 2. The `key=`-based port is the first correct version.
2. `matchmaker.py:66` — legacy `if toads[j].rxid in rx_match != -1`
   was a Python 2 chained-comparison accident; port fixed to
   `in rx_match`.
3. `detect.py:96` — legacy `Detector.next()` had no `return`
   (always yielded `None`); port returns the result.
4. `carrier_detect.py` — `sqrt(max(0, noise_power))` guard added;
   peak wrap fixed from `if peak_idx > len` to `%=` (legacy had an
   off-by-one at `peak_idx == len`).
5. `stat_tools.py` — zero-MAD guard added.
6. `soa_estimator.estimate_noise` — code unchanged from legacy
   (single peak-power subtraction); only the legacy *comment*
   ("twice") was wrong and has been corrected. No numerical drift.
7. File-handle hygiene (`toads_data.py`, `matchmaker.py`,
   `tdoa_est.py`): string-opened streams now closed in `finally`.
8. Asserts on user-reachable paths replaced with typed exceptions
   (`soa_estimator.calculate_window`, `pos_est.solve_1d`,
   `Detector.detect`).

**Verdict: the "signal-processing pipeline is preserved" claim holds,
and the port is of higher quality than the original.**

---

## 4. New findings (not in the previous audit)

Severity-ordered. CONFIRMED = demonstrated or unambiguous from code;
PLAUSIBLE = credible failure path, not executed.

| ID | Sev | Where | Finding | Status |
|---|---|---|---|---|
| N1 | Major | `fastcapture/airspy_reader.c:124-133` | `_airspy_reader_stop` calls `airspy_stop_rx()` **before** `circbuf_cancel()`. If the USB callback thread is blocked in `circbuf_put` (buffer full — slow consumer), `airspy_stop_rx`'s thread join never returns → deadlock on stop. The SIGINT path is masked (reader_cancel runs `circbuf_cancel` first, `fastcard.c:229`); the non-signal stop path (e.g. block-limit reached) is exposed. Fix: swap the two calls. | PLAUSIBLE |
| N2 | Major | `fastdet/corr_detector.cpp:118,158` (`corr_detector.h:42`) | `estimate_noise(size_t peak_power, …)` — the float correlation peak power is implicitly truncated to an integer at the call site. With /2048-normalised samples, powers < 1.0 truncate to **0**, so the peak is never subtracted from the noise estimate → threshold biased high → sensitivity loss. Likely inherited from upstream fastdet; still wrong. Change the parameter to `float`. | CONFIRMED (by inspection) |
| N3 | Minor | `thriftyx/soa_estimator.py:183` | `parabolic_interpolation` — the **default** SoA path — has no zero-denominator guard (the gaussian variant has one at `:203`). A flat peak (`2b − a − c == 0`, e.g. clipped/saturated correlation) yields inf/NaN, and `_clip_offset` (`:20-21`) does **not** clip NaN (both comparisons are False), so NaN propagates into the `.toad` SoA. Add the same `abs(denom) < 1e-12` guard and make `_clip_offset` NaN-safe. | CONFIRMED |
| N4 | Minor | `fastdet/corr_detector.cpp:165` | fastdet uses `interpolate_gaussian` while the Python default is parabolic — a C/Python parity drift in the default sub-sample method (equivalent accuracy per the paper, but parity claims should note it). Same NaN-escapes-clip pattern as N3 on `log(0)`. | CONFIRMED |
| N5 | Minor | `thriftyx/hal/airspy_mini.py:665-684` | Exceptions raised inside `_c_callback` (user callback or buffer code) are swallowed by ctypes (traceback printed to stderr, stream continues). A failing consumer degrades capture silently instead of aborting. Record the exception and surface it from `read_sync`/`stop_capture`. | CONFIRMED |
| N6 | Minor | `thriftyx/settings.py:31-36,117-122` + `config_validator.py:78` | Stock defaults are mutually invalid: `--device-type airspy_mini` (default) + `--sample-rate 2.4M` (default) fails validation, so bare `thriftyx capture out.card` errors. Previously catalogued as a doc issue; it is really a code defect — derive the default rate from the device type (mini→3M, r2→2.5M, rtlsdr→2.4M). | CONFIRMED |
| N7 | Minor | `thriftyx/cli.py:88-107` | No top-level error handling: missing `template.npy`, malformed `.card`, bad config values all surface as raw tracebacks. Also the `ImportError` catch at `:94` misattributes genuine import bugs inside a module to "missing optional dependencies". | CONFIRMED |
| N8 | Minor | `thriftyx/carrier_sync.py:209` | `curve_fit` failure (non-convergence, NaN in the window) raises and aborts the entire `detect` run. A 24/7 field detector should catch, log, and fall back to `offset=0` for that block. | PLAUSIBLE |
| N9 | Minor | CI `.github/workflows/ci.yml:71-74` | The "smoke-check binary output" step ends in `|| true` — it can never fail; the C build gate proves compilation only. Also: only Python 3.10 in the matrix (suite passes on 3.11/NumPy 2.4 but nothing pins that), no coverage threshold despite `pytest-cov` shipping in `[dev]`, `on: push` + `pull_request` double-runs PR branches, actions pinned by tag not SHA. | CONFIRMED |
| N10 | Minor | packaging | `setup.py` drifts from `pyproject.toml` (no `all` extra, different description); `setup.cfg` `[aliases] test=pytest` is dead pytest-runner config; `requirements.txt` duplicates `pyproject` deps (drift risk); `pylintrc` is dead now that ruff is the linter. Consolidate on `pyproject.toml` and delete the rest. | CONFIRMED |
| N11 | Minor | `pyproject.toml:38-47` | The lint/type gates are shallower than the README implies: mypy runs with `ignore_missing_imports` and no `disallow_untyped_defs` over a codebase where only 6/40 modules have annotations (hal/, `config_validator`, `setting_parsers`) — "mypy clean" is a low bar; ruff runs the default `E`/`F` set only (no bugbear/pyupgrade/isort). Tighten incrementally, module-by-module like the annotation rollout. | CONFIRMED |
| N12 | Info | `thriftyx/block_data.py:49` | `_raw_reader(stream, chunk_bytes - len(chunk))` — the argument is evaluated once at generator creation (`len(chunk)` is always 0). Works, but the expression implies per-iteration resizing that never happens; write `chunk_bytes`. | CONFIRMED |
| N13 | Info | `thriftyx/block_data.py:137-139` | `complex_to_raw` truncates instead of rounding on both bit-depth paths (≤0.5 LSB round-trip bias; the 8-bit path matches legacy, the new 12-bit path repeats the pattern). | CONFIRMED |
| N14 | Info | `rpi/systemd/thriftyx-capture@.service` | `Wants=time-sync.target` does not guarantee a synced clock unless `systemd-time-wait-sync` (or chrony-wait) is enabled — worth one line in the runbook. `ProtectHome=read-only` will EROFS any `THRIFTYX_OUT` placed under `/home` (the docs do say to use an SSD path). | CONFIRMED |
| N15 | Info | `README.md:63` | Test count stale: says 336, suite has 339 (the previous audit's own regression tests). | CONFIRMED |

---

## 5. Goal-by-goal assessment (this pass)

| Goal | Verdict |
|---|---|
| Preserved DSP pipeline | **Met** — diff-verified; port fixes legacy bugs without numerical drift (§3) |
| Airspy Mini/R2 HAL | **Met** — ctypes layout matches `airspy.h` (`airspy_transfer` field order/alignment verified); callback copies the buffer before return; callback ref GC-pinned; known O1–O3 hardening still open |
| Python 3.10+ modernisation | **Met with caveats** — porting discipline is excellent; the *gates* overstate assurance (N9, N11) |
| Unified Qt viewer | **Met** — single window, two `QTabBar`s, shared canvas, lazy plotter cache, PyQt5→PySide6→Tk→pyplot fallback with WSL probing (`detect_analysis.py:732-860`); well engineered |
| Pi 5 deployment | **Met** — F3 fix verified; `update_node.sh` rollback logic and `cleanup_old_captures.sh` guards are careful; N14 nits |
| C capture/detect parity | **Weakest area** — builds and matches the /2048 contract (`rawconv.c`), but N1/N2/N4 live here and nothing but a `--help` smoke test (which cannot fail, N9) covers it |
| CI gating | **Met structurally**, shallow in depth (N9, N11) |

---

## 6. Recommended next steps (priority order)

> **Resolution update (same branch, follow-up commit):** N1, N2, N3,
> N4, N5, N6, N7, N8, N9 (matrix + strict smoke), N10, N11 (mypy
> per-module strict + ruff bugbear), N15, O1 (bounded read_sync buffer
> with software-drop accounting), and O2 (all-or-nothing libairspy
> binding) are **fixed**; O3 is partially addressed by the new
> streaming-core unit tests (`tests/unit/test_airspy_streaming_core.py`).
> Still open: full O3 (RTL/fastcard path tests), ruff `UP`/`I`
> mechanical sweep (~190 auto-fixable sites), N12–N14 (info-level).

1. **N1 + N2 + N3** — small, high-leverage correctness fixes
   (swap two lines in `airspy_reader.c`; one type in
   `corr_detector.h/cpp`; one guard + NaN-safe clip in
   `soa_estimator.py`) with regression tests for N3.
2. **N6** — derive the default sample rate from `device_type` so the
   out-of-box `capture` invocation works.
3. **O1/O2/O3 from the previous audit** — still the largest field
   risks (unbounded queue, degraded-libairspy binding, untested
   streaming core).
4. **Gate depth** — add a 3.12/3.13 leg to CI, remove `|| true` from
   the smoke step, enable `disallow_untyped_defs` per typed module,
   adopt `ruff` `B`/`UP`/`I` rule groups.
5. **Packaging cleanup (N10)** and the stale README count (N15).
