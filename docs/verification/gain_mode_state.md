# Airspy gain-mode state & implementation

**Status:** `--gain-mode {manual, linearity, sensitivity}` + `--combined-gain`
were already wired end-to-end via Option 1 (delegate preset modes to
libairspy). This task verified the chain, fixed a documentation
correctness bug, added the missing test suite, and added one validator
warning. No Python re-implementation of the libairspy gain ladders was
added (Option 1 preserved).

---

## 1. Discovery — the chain was already mostly complete

| Layer | File:line | State on master |
|---|---|---|
| CLI flags | `thriftyx/settings.py:147-176` | `--gain-mode` (default `manual`), `--combined-gain` (default 0), `--lna-agc`, `--mixer-agc` all present. |
| ctypes bindings | `thriftyx/hal/airspy_mini.py:102-103` | `airspy_set_linearity_gain` / `airspy_set_sensitivity_gain` declared (`[c_void_p, c_uint8] -> c_int`). |
| HAL delegation | `thriftyx/hal/airspy_mini.py:554-627` | `set_linearity_gain`, `set_sensitivity_gain`, `_check_combined_gain`, `apply_gain_mode` implemented; each checks the C return code and raises `DeviceConfigError`. |
| Range constant | `thriftyx/hal/airspy_mini.py:183` | `_COMBINED_GAIN_RANGE = (0, 21)` (matches `GAIN_COUNT == 22`). |
| Capture wiring | `thriftyx/airspy_capture.py:389-402` | Branches on `gain_mode`: manual → `apply_gain_mode(..., lna/mixer/vga, agc)`; preset → `apply_gain_mode(mode, combined=...)`. |
| INFO logging | `thriftyx/airspy_capture.py:103-116` | Logs `gain mode: manual; LNA=.. Mixer=.. VGA=..` or `gain mode: <preset>; combined=..` to stderr. |
| Validation | `thriftyx/config_validator.py:172-191` | Rejects unknown mode, missing `combined_gain`, out-of-range `combined_gain`; warns on AGC+preset. |

So no `GainConfig` dataclass exists (the design doc's hypothetical); the
config flows as a plain dict from `settings` → `airspy_capture` →
`device.apply_gain_mode(...)`. The representation is a single
`gain_mode` string plus `combined_gain`, exactly as the prompt preferred.

## 2. Primary-source verification (libairspy)

Repo `airspy/airspyone_host`, `libairspy/src/airspy.c`, branch `master`,
fetched 2026-05-28.

- `#define GAIN_COUNT (22)` → valid `--combined-gain` is `0..21`.
- `airspy_set_linearity_gain` / `airspy_set_sensitivity_gain` both:
  * clamp `if (value >= GAIN_COUNT) value = GAIN_COUNT - 1;`
  * **invert** `value = GAIN_COUNT - 1 - value;`
  * force AGC off: `airspy_set_mixer_agc(device, 0); airspy_set_lna_agc(device, 0);`
  * index `airspy_{linearity,sensitivity}_{vga,mixer,lna}_gains[value]`.
- For user value `0` → internal index `21`:
  `vga_gains[21] = 4`, `mixer_gains[21] = 0`, `lna_gains[21] = 0`.

**Two load-bearing facts:**
1. User `combined-gain 0` = **minimum** gain (not maximum).
2. The minimum row is `LNA=0, Mixer=0, VGA=4` — VGA floors at 4, so a
   preset can never reach a true all-zero internal gain. Only manual
   `0/0/0` can.

## 3. Gaps found & fixed

| Gap | Fix |
|---|---|
| **Doc correctness bug.** `docs/user_guide.md` §4.4 said "0 = max gain, 21 = min" — backwards — and printed a raw-LUT excerpt that ignored libairspy's inversion (so it implied combined 0 → LNA/Mix/VGA = 14/12/14). | Rewrote §4.4: combined 0 = minimum, 21 = maximum; documented the AGC-forced-off behaviour and the VGA=4 floor; added the external-LNA / BatRF recommendation to use manual `0/0/0`. Cited `airspy.c`. |
| **No test suite.** `tests/unit/test_airspy_gain_mode.py` did not exist. | Added 16 tests: HAL routing per mode (spy over `_lib` asserts which C function is invoked, and that presets do NOT call the per-stage setters), combined-gain range rejection, preset-requires-combined, unknown-mode rejection, and validator tests for the combined-gain range + the new per-stage warning. |
| **No per-stage-ignored warning.** In preset mode, non-default per-stage gains were silently dropped. | Added a validator warning (`config_validator.py`): in a preset mode, if any of `lna/mixer/vga_gain` is non-zero, warn that libairspy resolves the stages from `combined_gain`. Additive — breaks nothing. |

## 4. Deliberate deviation from the prompt

The prompt's Step 3 says preset + AGC flags should **raise an error**.
Master already **warns** (not errors) for this case, and there is an
existing test that asserts the warning
(`tests/unit/test_config_validator.py::test_gain_mode_linearity_warns_when_agc_set`).
The prompt's own Step 4 requires existing tests to keep passing.

Escalating the warning to a hard error would break that tested,
intentional behaviour for no functional benefit — the warning already
tells the user the flags are ignored, and libairspy forces AGC off
regardless, so nothing silently misbehaves. The warning is therefore
**kept as-is**. The new per-stage-ignored case is added as a warning for
consistency with it.

## 5. Defaults unchanged (BatRF stays manual 0/0/0)

`gain_mode` default stays `manual`, `combined_gain` default stays `0`,
per-stage defaults stay `0/0/0`. The preset modes are an opt-in
convenience; the default path is unchanged, so the BatRF deployment
keeps its true-minimum internal gain.

## 6. Test result

```
$ python -m pytest tests/unit/test_airspy_gain_mode.py -q
16 passed
$ python -m pytest tests/ -q
(all green; see PR description for the count)
```
