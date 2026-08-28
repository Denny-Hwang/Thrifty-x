# Refactoring-Goal Validation Review — 2026-08-28

**Scope:** Independent validation of the full repository against the
refactoring goals (Airspy Mini/R2 hardware upgrade, preserved DSP
pipeline, Python 3.10+ modernisation, unified Qt viewer, Pi 5
deployment, C capture/detect parity, CI gating), cross-checking the
2026-06-09 comprehensive audit and the 2026-07-03 staff-level audit
(and re-verifying the fixes landed for them in PR #62).

**Method:** Four independent adversarial review tracks run in
parallel — (a) HAL/ctypes layer vs the real `libairspy` API,
(b) the v1/v2 `.card` data path with empirical exhaustive round-trip
probes, (c) module-by-module diff of `thriftyx/` against the legacy
`thrifty/` reference tree, (d) the C `fastcapture`/`fastdet` trees
(built from source and functionally smoke-tested) plus the `rpi/`
deployment assets — followed by first-hand reproduction of every
MAJOR/CRITICAL claim before inclusion here. CI gates re-run locally.

---

## 1. Executive verdict

The hardware-upgrade refactoring is **real, substantially complete,
and holds up under adversarial re-verification** on the paths the
README recommends: the Airspy Mini ctypes HAL is sound (struct
layout/argtypes verified against `airspy.h`, offsets computed), the
8-bit and 12-bit data paths are numerically exact (8-bit bit-identical
to legacy over all 256 values; 12-bit exactly invertible over all
65,536 int16 values), the core DSP math is byte-identical to the
original, and the Python capture → detect → … → pos pipeline passes
its full test suite.

The residual defects concentrate in three places:

1. **The C file-input paths were never converted to int16** — the C
   tree cannot correctly re-read raw or `.card` files (§4 C1–C3).
   Live Airspy capture → v2 card **write** is correct (round-trip via
   `thriftyx.block_data` verified on a built binary).
2. **Airspy R2 support carries untested copy-paste defects** (§4
   M1–M3) — the Mini path is well-tested, the R2 overrides are not.
3. **The lint gate has drifted**: `ruff` is unpinned and the current
   release (0.16.5) reports 251 errors in the CI scope, so the next
   fresh CI run will fail lint (§2).

| Gate (re-run locally, 2026-08-28) | Result |
|---|---|
| `python3 -m pytest -q` | **363 passed, 6 skipped** (Python 3.11) |
| `mypy thriftyx/` | clean (40 files) |
| `ruff check thriftyx/ tests/` (ruff 0.16.5, unpinned) | **251 errors** — new default rule groups (UP/I/LOG/…) + 4 now-default `E501`; configured `E`/`F`/`B` subset shows only the 4 `E501` |
| Last recorded CI run (master, 2026-07-05) | success (with the ruff release current at that time) |
| `fastcapture` / `fastdet` CMake builds | clean; `fastdet` runs on a synthetic tone |

README drift: claims “373 tests”; local collection is 368 (the count
moves with optional deps — consider dropping the exact number).

---

## 2. Verification of the previous audits’ fixes (PR #62, commit c22d237)

Spot-checked first-hand this pass:

| Fix | Verdict |
|---|---|
| N3 — parabolic zero-denominator guard + NaN-safe `_clip_offset` | **CORRECT** (`soa_estimator.py:20-26,188-193`) |
| N6 — default sample rate derived from `device_type` | **CORRECT** (`airspy_capture.py:112-136`; only when `sample_rate` not explicit) |
| N1 — stop path: `circbuf_cancel` before `airspy_stop_rx` | **PARTIAL** — fixed in `_airspy_reader_stop`, but the same hazard remains in `_airspy_reader_free`/`airspy_reader_close` (§4 M7) |
| O1 — bounded `read_sync` buffer with drop accounting | **CORRECT on Mini**; defeated on R2 by M3 below |
| Data-path F1 (`#v2` header precedence) | **CORRECT** (re-verified with tests + adversarial probes) |

---

## 3. Goal-by-goal assessment

| Goal | Verdict |
|---|---|
| Airspy **Mini** HAL (ctypes) | **Met** — `airspy_transfer` layout (offsets 0/8/16/24/32/40, sizeof 48), enum values, all argtypes/restypes, two-call `airspy_get_samplerates` protocol, callback copy-before-return, GC-pinned trampoline: all verified correct; streaming core well-tested |
| Airspy **R2** support | **Met with defects** — M1–M3; no R2-specific HAL unit tests exist, which is exactly where the bugs live |
| 8-bit ↔ 12-bit data path | **Met, verified exact** — 8-bit conversion bit-identical to legacy (all 256 values); 12-bit round trip exact (all 65,536 values); write/read endianness consistent; sniffer safe for all machine-written files; `/2048` matches `normalization_divisor.md` and `rawconv.c`; thresholds scale-invariant |
| Preserved DSP pipeline | **Met for the core math** (Dirichlet fit, despreading, clock-correction, LM solver byte-identical; `Synchronizer.sync` byte-identical) — but the README’s “only two defaults changed” framing is incomplete (§4 M4 and the minor list) |
| Python 3.10+ modernisation | **Met** — porting discipline confirmed again at the diff level; lint gate drift is operational, not code quality |
| Unified Qt viewer | **Not re-reviewed this pass** — relies on the 2026-07-03 audit (“Met”) + passing viewer tests |
| Pi 5 deployment | **Met on the documented path** (`thriftyx-capture@.service`, heartbeat, chrony script, `update_node.sh` idempotency, `cleanup_old_captures.sh` scoping all verified) — the **legacy** RTL launcher files in `rpi/` are broken/stale (M6) and should be labeled or removed |
| C capture/detect parity | **NOT met** — C1–C3: raw/stdin input and `.card` re-reading in C produce corrupt blocks; `fastdet`’s card export is corrupt; live-capture write path is the only correct C data path |
| CI gating | **At risk** — unpinned ruff will fail the lint job on the next fresh runner (see §2); C build gate still proves compilation only |

---

## 4. Findings

CONFIRMED = reproduced first-hand or demonstrated empirically by the
review track (builds/probes); file:line refer to the current tree.

### Critical (C tree — file-input paths never converted to int16)

| ID | Where | Finding |
|---|---|---|
| C1 | `fastcapture/raw_reader.c:22-30` | Raw/stdin input (the CLI’s **default** input mode) still uses the old fastcard layout (1×uint16 per I/Q pair). With `raw_samples` now `int16_t*` (`reader.h:33`), the history memcpy source offset, history byte count, fread destination and fread count are all half the required size → every block is half stale data. Empirically reproduced: 8192-pair tone file → 16 blocks of 1024 instead of 8; peak magnitude halved. Also overlapping `memcpy` (UB) where the airspy path correctly uses `memmove`. |
| C2 | `fastcapture/card_reader.c:29-33,69-76,92` | The C `.card` reader still assumes the v1 8-bit line length (`(2*block_size+2)/3*4`); a v2 line is twice that, so `fgets` truncates and **fastcapture cannot read its own output** (empirically: write card → read card = 0 blocks, “line too long”). Same stale history math as C1. |
| C3 | `fastdet/fastdet.cpp:127,211-213` | `-x` card export encodes `block_len*2` bytes instead of `block_len*2*sizeof(int16_t)` but writes a `#v2 bit_depth=12` header → Python `card_reader` decodes half-length blocks: silently corrupt exports. (`fastcard_cli.c:189` does it correctly.) |

### Major

| ID | Where | Finding |
|---|---|---|
| M1 | `thriftyx/hal/airspy_mini.py:257-267` | `parse_airspy_serial('0x12345678')` → 12345678 decimal (expected 305419896): the `0x` prefix is stripped before the hex/decimal decision, so digits-only hex parses as decimal → wrong device opened / spurious not-found. Reproduced. Tests only cover hex-with-letters and 16-digit forms. |
| M2 | `thriftyx/hal/airspy_r2.py:20`, `config_validator.py:26` | R2 LNA range declared 0–15; the R820T2/libairspy range is 0–14 (Mini side is correct). `--lna-gain 15` passes validation, is silently clamped by libairspy, and the capture header misreports the applied gain. |
| M3 | `thriftyx/hal/airspy_r2.py:55-65` | R2 `set_sample_rate` override omits `self._sample_rate = int(rate)` (present at `airspy_mini.py:482`), so `_start_rx` sizes the bounded `read_sync` buffer from `max(rates)` = 10 MSPS: at 2.5 MSPS the documented 4 s cap silently becomes 16 s (~160 MB) — defeats O1’s bound on a Pi-class host. Root cause: copy-paste override instead of `super()` call; no R2 HAL tests. |
| M4 | `thriftyx/settings.py:276-307,420` | `_auto_adjust_block_params` silently rewrites explicitly-configured `block_size`/`block_history` (info-level log only) using a **hardcoded 1023-chip** template estimate, changing FFT length/bin width behind the user’s back. Fires exactly in the Airspy-rate regime (defaults at 6 MSPS: 16384/4920 → 32768/12280, matching the user-guide tables — so the intent is real, but it is absent from README’s change list and wrong for non-1023-chip codes). Should warn loudly, respect explicit values, or derive the code length from the actual template. |
| M5 | `fastdet/fastdet.cpp:137` | Leftover `strcmp(input_file, "rtlsdr")` — the sentinel is now `"airspy"` (`fastcard.c:22`), so live-capture runs write card headers with `sample_rate=0` and no tuner line: wrong metadata in every fastdet-produced card. |
| M6 | `rpi/fastdet.sh:15-16,34-36`, `rpi/detector.service:14`, `rpi/detect.sh:27-31`, `rpi/fastdet.cfg:8` | The legacy RTL launcher set is broken with the current binaries: `rtl_biast` path, `-i rtlsdr` (treated as a filename → fopen fails → 120 s restart loop), `RTL_GAIN=30` exceeds LNA 0–14, `detector.service` points at the old `/home/pi/thrifty/` clone path, and `detect.sh`’s pipeline dies at config validation (`detector.cfg` has no `device_type`, so the `airspy_mini` default rejects `sample_rate: 2.4M`). Label as legacy-nonfunctional or remove/fix. |
| M7 | `fastcapture/airspy_reader.c:146-157,246-258` | `_airspy_reader_free`/`airspy_reader_close` call `airspy_stop_rx()` without `circbuf_cancel()` first — the same join-vs-blocked-producer deadlock the N1 fix removed from the stop path; reachable via early-exit error paths (`fastcard_cli.c:122-126` → `goto free`). |
| M8 | `fastcapture/airspy_reader.c:44-59`, `fastcard.c:232-236` | Sample drops are silent in the C path: `transfer->dropped_samples` ignored, `circbuf` overflow counters maintained but `fastcard_print_stats` is stubbed. A silent stream discontinuity invalidates SoA block indexing — a functional regression vs the RTL fastcard, which reported these stats. |
| M9 | CI / `pyproject.toml:21` | `ruff` unpinned in `[dev]`; ruff 0.16.5 enables new default rule groups → 251 errors in the CI scope (`ruff check thriftyx/ tests/`), incl. 4 `E501` under the configured subset. The next fresh CI run fails lint. Pin the ruff version (or pin `lint.select` explicitly) and fix the 4 `E501`. |

### Minor

**DSP — undocumented behavioral deltas vs legacy** (none affect the
recommended default path’s recorded outputs except as noted):

- `carrier_sync.py:277` — time-domain `freq_shift` dropped legacy’s
  `-0.5` freqs offset: output differs by a constant `exp(-jπ·shift)`
  phase. Magnitude-only downstream, so `.toad` values are unaffected —
  but the `--freq-shift-method time_domain` escape hatch is not
  sample-identical to legacy, contrary to the README’s implication.
- `carrier_sync.py:204,235,251` — interpolators return offset 0 within
  `width//2` bins of the FFT edges where legacy wrapped circularly
  (correct for FFT bins) or crashed at the high edge: up to 0.5-bin
  carrier-offset error for near-DC carriers.
- `carrier_detect.py:111` / `soa_estimator.py:146` — `max(0, ·)` noise
  clamp: legacy produced NaN thresholds on very strong carriers
  (block silently **not** detected); the port detects them with
  `noise=0` → different detection sets on strong-signal captures.
  (Recorded in the 2026-07-03 audit; still absent from README.)
- `matchmaker.py:151`, `tdoa_est.py:62,178,198` — legacy’s invalid
  `cmp=` comparators fixed (correctness fix, documented in the audit),
  so byte-for-byte reproduction of legacy `.match`/`.tdoa` output is
  not guaranteed.
- `stat_tools.py:46-47` — zero-MAD guard flips degenerate-case
  polarity: legacy flagged every deviating point as an outlier,
  the port flags none → deviating beacon pairs can now enter the
  clock-drift fit.
- `tdoa_est.py:377-415` — `-s` default resolution chain (audit F2) is
  a behavior change vs legacy’s fixed 2.4e6; usually more correct, but
  not in README’s change list.

**Data path (robustness edges; legacy-parity unless noted):**

- `block_data.py:168` (+ `airspy_capture.py:391,534,596`) —
  `history=0` slices the whole previous block (`[-0:]`) → ever-growing
  blocks; fails loudly downstream. Legacy-identical bug.
- `block_data.py:232-238` — a truncated final card line (power loss
  mid-write; batched flushing makes this plausible) crashes the whole
  detect run instead of salvaging complete lines.
- `block_data.py:25,205` — sniffer requires the exact `'#v2 '` prefix;
  `#v2\n` / tab-separated variants silently decode as 8-bit
  (machine-written files are always well-formed).
- `block_data.py:207-210` — the v2 header’s `sample_rate` is parsed
  and then discarded: a card captured at 6 MSPS processed with a
  2.4 MSPS config is silently mis-scaled although the file carries the
  truth. Cheap, high-value warning opportunity.

**HAL / capture CLI:**

- `settings.py:124-129` vs `:117-122` — default `bit_depth=8` +
  default `device_type=airspy_mini` → validator warns on every stock
  Airspy run (false-alarm training).
- `airspy_mini.py:652-653` — `apply_gain_mode('manual')` calls the AGC
  setters unconditionally: on a libairspy build lacking the AGC
  symbols every capture fails even with AGC off.
- `airspy_capture.py:450-464` — `DeviceConfigError` raised from
  `device.open()` (the sample-type fail-fast) escapes as a raw
  traceback instead of the clean error path.
- `airspy_mini.py:115-119,200-217` — `airspy_lib_version_string` does
  not exist in libairspy (real API: `airspy_lib_version(struct*)`);
  version always logs `unknown` on real hardware; only test fakes
  provide the symbol.
- `airspy_mini.py:829-843` — `read_sync(0)` raises `ValueError` from
  `np.concatenate([])`.

**C tree / rpi (beyond C1–C3, M5–M8):**

- `cardet.c:22-25` — noise estimate not clamped at 0 (Python is):
  threshold dips below `threshold_const` on strong carriers.
- `airspy_reader.c:181,237-242` — `-d <index>` silently ignored
  (always opens first device); `airspy_start_rx` failure path leaks
  the circbuf.
- `fastcard_wrappers.cpp:119-122` — `-o -` writes a file named `-`
  (dead stdout branch; should test `"-"`).
- `corr_detector.cpp:212-213` — parabolic carrier interpolation reads
  out of bounds at bin 0 / fft_len−1 (default window `0--1` includes
  bin 0).
- `corr_detector.cpp:79` — the `history_len >= template_len-1` assert
  is compiled out in Release; underflow → cryptic `volk_malloc failed`
  (reproduced with `-h 512`).
- `rpi/cleanup_old_captures.sh:36-41` — emergency purge can unlink the
  card file currently being written (fd held → no space reclaimed,
  data lost).
- `rpi/soak_test.sh:35` — `MAX_DISK_GROWTH_MB` declared, never used;
  throttle check silently passes without `vcgencmd`.
  `rpi/fastdet.cfg`/`detector.cfg` carrier windows are stale
  2.4 MSPS values.

### Info / nits

- README “373 tests” vs 368 collected; “36 test modules” is correct.
- `block_data.py` v2 format records no endianness (both ends native;
  all realistic targets LE); header omits `block_size`, so
  `diag/check_card_format.py` guesses 65536.
- Mid-file `#v2` headers switch bit depth from that point on
  (concatenated cards misread).
- `card_writer`’s `sample_rate` parameter is dead.
- `cardet.c:14` / `corr_detector.cpp:170` — `uint16_t` peak index
  wraps for `block_len > 65535` (unvalidated).
- `airspy_mini.py` dead fallback branches (all-or-nothing binding
  makes the `airspy_open_sn`-missing path unreachable); second
  `start_capture()` silently swaps the user callback; RX callback
  never asserts `transfer.sample_type`; `-d` help text says “RTL-SDR
  device index” though it drives Airspy selection, where `-d 0` means
  “default open”, not `serials[0]`; validator applies the Airspy
  frequency range to `rtlsdr`.

### Design note (not a defect, worth recording)

There is **no RTL-SDR binding under `thriftyx/hal/`** — the factory
registers only the two Airspys, and `create_device('rtlsdr')` raises.
RTL-SDR capture works via the external `fastcard` binary or a raw
stdin pipe, entirely outside the `SDRDevice` abstraction
(`base.SampleFormat.UINT8` has no consumer). The legacy RTL data path
itself is preserved and correct (v1 cards, 127.4/128 conversion
bit-identical). If “RTL-SDR through the same HAL abstraction” is a
goal, it is unimplemented; the README’s hardware table does not
promise it, so this is recorded as a scoping clarification.

---

## 5. Verified correct (highlights, first-hand or empirically probed)

- `airspy_transfer` struct layout and every bound argtype/restype
  match `airspy.h` (ctypes offsets computed: 0/8/16/24/32/40,
  sizeof 48); CFUNCTYPE-through-`c_void_p` validated experimentally.
- INT16_IQ requested before `start_rx` with fail-fast + handle close;
  callback copies before return; trampoline GC-pinned; callback
  exceptions surfaced (not swallowed); bounded buffer drop accounting
  folds into `dropped_samples` (Mini).
- 8-bit path `(x−127.4)/128` bit-identical to legacy over all 256
  values; 12-bit `/2048` exact round trip over all 65,536 values;
  `rawconv.c` matches; capture→detect round-trips bit-exactly; block
  overlap/index continuity verified on dribbled streams; `#v2` header
  precedence with warning verified.
- Core DSP byte-identical: `Synchronizer.sync`, Dirichlet
  `curve_fit` (bounds present in both trees), despreading, noise
  estimate, clock-correction polynomial, LM solver; all shared
  settings defaults identical; the two documented default changes are
  present and overridable.
- `fastcapture` live-capture v2 write path: correct sizes, header,
  and a built-binary card read back correctly by
  `thriftyx.block_data.card_reader`.
- `circbuf` mutex/condvar core sound; `cleanup_old_captures.sh` rm
  scoping safe; `update_node.sh` idempotency logic correct;
  `thriftyx-capture@.service` correct (`%%` escaping, StartLimit in
  `[Unit]`); every key in `thriftyx-capture.cfg.example` exists in
  `DEFINITIONS`.

---

## 6. Recommended next steps (priority order)

> **Resolution update (same branch, follow-up commit):** items 1–3
> below are **fixed**: C1–C3 (int16 layout in `raw_reader.c`,
> `card_reader.c`, `fastdet.cpp` card export; round-trip verified
> against a built binary and now gated in CI by
> `scripts/c_card_roundtrip_check.py`), M1–M3 (serial-prefix parse
> order, R2 LNA range 0–14, R2 `_sample_rate` bookkeeping; covered by
> the new `tests/unit/test_airspy_r2.py`), and M9 (ruff constrained to
> `>=0.16,<0.17` with an explicit `lint.select`; the four `E501` lines
> wrapped). While validating, a pre-existing test-hygiene defect
> surfaced: `test_airspy_streaming_core._streaming_device` leaked
> fake-state devices whose `__del__` → `close()` → `airspy_stop_rx(NULL)`
> segfaults the suite on any machine with libairspy installed (CI never
> installs it, so the gate was blind); fixed by shadowing `close` on
> the fake-state device. The minor list remains open.
>
> **Second resolution update (follow-up PR):** M4–M8 are now also
> **fixed**: M4 (`_auto_adjust_block_params` respects explicitly-set
> values — warning instead of rewrite — and logs adjustments of
> defaults at WARNING level; documented in README; regression tests in
> `tests/test_settings.py`), M5 (`fastdet.cpp` SDR sentinel updated to
> `"airspy"`), M6 (`rpi/fastdet.sh`+`fastdet.cfg` rewritten for the
> Airspy CLI with chrony sync, `rpi/detector.service` path fixed to
> `thrifty-x`, `rpi/detect.sh` switched from `ntp-wait` to
> `chronyc waitsync`, `rpi/detector.cfg` gained the explicit
> `device_type: rtlsdr` its validation needs), M7 (`circbuf_cancel`
> before `airspy_stop_rx` in `_airspy_reader_free` and
> `airspy_reader_close`; the `airspy_start_rx` failure path no longer
> leaks the ring buffer), and M8 (the RX callback accounts
> `transfer->dropped_samples` with a one-shot stderr warning, and
> `fastcard_print_stats` now reports drops, overflow events, and the
> ring-buffer occupancy histogram via `airspy_reader_print_stats` —
> restoring the stats the RTL fastcard used to print, for both
> `fastcapture` and `fastdet`).

1. **C1–C3** — convert `raw_reader.c`/`card_reader.c` to the int16
   layout and fix `fastdet -x` export size; add a C round-trip test to
   CI (write card → re-read) so the gate proves more than compilation.
2. **M1–M3** — three small HAL fixes (serial parse order, R2 LNA
   range, R2 `super().set_sample_rate()`), plus an R2-specific unit
   test file mirroring the Mini coverage.
3. **M9** — pin ruff (version and/or explicit `lint.select`), fix the
   4 `E501`; then the next CI run is green again.
4. **M4** — make `_auto_adjust_block_params` warn (not info), skip
   explicitly-set values, or derive code length from the template.
5. **M5–M8** — fastdet sentinel, legacy `rpi/` launcher cleanup,
   free/close cancel ordering, drop-stats reporting.
6. Minor robustness batch: truncated-tail salvage + header
   `sample_rate` check in `block_data`, default `bit_depth` warning
   noise, `read_sync(0)`, `open()` error path.
