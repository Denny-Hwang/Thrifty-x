# auto_classify robustness re-verification

**Status:** Re-verified on current `master`. The prompt's premise — that
auto-classification uses a `mean + 2*std` gap rule — does **not** match
this repo. The actual algorithm is a histogram-peak detector with
hysteresis, and it already passes all six prompt cases. Per the prompt's
own stop condition ("if all six cases already pass, the hand calculation
was wrong; document and stop"), **no classifier change is made**. The one
genuinely-missing item from the prompt (Step 4, the soft `--map` nudge)
is added.

The original robustness analysis is in
`docs/verification/auto_classify_robustness.md` (PR #41). This document
is the fresh re-check requested.

---

## 1. Actual algorithm (not std-of-gaps)

`detect_transmitter_windows` (`thriftyx/identify.py:33`) builds a
histogram of carrier bins and scans it with hysteresis thresholds:

```python
# thriftyx/identify.py:49-50
low_thresh = np.std(cnts) * 0.4
high_thresh = np.std(cnts) * 1.25
```

A peak (transmitter) opens when a bin count exceeds `high_thresh` and
closes when it drops below `low_thresh`. Splits are placed at the
midpoint of the gap between consecutive peaks. This is fundamentally
different from the prompt's hypothesised `mean(diffs) + 2*std(diffs)`
gap rule.

## 2. Six prompt cases — all pass

`tests/unit/test_identify_auto_classify.py::test_prompt_cases`:

| Case | bins | expected | actual |
|---|---|---:|---:|
| a | `[101]` | 1 | 1 |
| b | `[101, 200]` | 2 | 2 |
| c | `[101, 102, 128, 129]` | 2 | 2 |
| d | `[101, 102, 128]` (the "observed failure") | 2 | 2 |
| e | `[100, 101, 128, 129, 156, 157]` | 3 | 3 |
| f | `[101, 102, 103, 104]` | 1 | 1 |

Case d — the prompt's predicted failure — passes: the histogram between
bins 102 and 128 is 25 empty bins, far below `low_thresh`, so the split
fires cleanly.

## 3. Known limitation (unchanged)

The histogram-peak detector is brittle to **extremely uneven TX
populations** (a ~40:1 detection-count ratio inflates `std(cnts)` and
can swallow the weak transmitter). This is documented in
`auto_classify_robustness.md` and is not the BatRF pattern (TX1/TX2 have
similar duty cycles). The production-grade mitigation is `--map`.

## 4. Step 4 added — soft `--map` nudge

`auto_classify_transmitters` (`thriftyx/identify.py`) now emits a
`logging.warning` when it auto-classifies `>= 2` transmitters,
recommending `--map` for production runs with a known TX count and
pointing at `user_guide.md` sec 9.2.1 (added in PR #46). Routed through
`logging` (stderr by default) so it never contaminates the `.toad`
output stream.

Tests added:
- `test_auto_classify_nudges_to_map_for_multi_tx` — 2-TX input emits the
  nudge.
- `test_auto_classify_no_nudge_for_single_tx` — 1-TX input stays quiet.

## 5. Docs

The `--map` production recommendation is already documented in
`user_guide.md` sec 9.2.1 (PR #46). No `_ko` changes (English-only
policy).

```
$ python -m pytest tests/unit/test_identify_auto_classify.py -q
21 passed
```
