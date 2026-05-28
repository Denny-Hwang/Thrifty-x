# Design: multi-template support for distinct TX Gold codes

**Status:** design exploration only, no code changes. Not in scope
for the SoftwareX manuscript. Treat this as a roadmap for post-paper
work.

**Recommendation:** **Option 2** (a `--template-map` JSON keyed by
`txid`, paired with `--freq-map` for txid lookup). Lower per-block
cost than Option 1, cleaner semantics, and the `freq-map → txid →
template` chain mirrors what the existing identify pipeline already
does.

---

## 1. Current correlation path (single template)

| Step | File | Lines |
|---|---|---|
| Load template from `.npy` (called once at startup) | `thriftyx/detect.py` | `220: template = np.load(config.template)` |
| Construct `SoaEstimator` with the loaded template | `thriftyx/detect.py` | `59-64` |
| Pre-compute `template_fft` (called once in `__init__`) | `thriftyx/soa_estimator.py` | `77-80` |
| Per block: `corr = fft * template_fft.conj` then `corr.ifft` | `thriftyx/soa_estimator.py` | `97-103, 117-122` |

Template lifetime is per-process; loaded once, FFT-cached, and reused
for every block. No per-detection template switching today.

## 2. Option 1 — directory of templates, best-match per block

API: `--template /path/to/templates_dir/` where the directory contains
`tx1.npy`, `tx2.npy`, etc. For every block the detector correlates
against each template and keeps the best.

### Files that change

| File | Change |
|---|---|
| `thriftyx/detect.py` | Accept either a single `.npy` (existing) or a directory; instantiate one `SoaEstimator` per `.npy` found. Each has its own `template_fft`. |
| `thriftyx/soa_estimator.py` | Optionally a wrapper `MultiTemplateSoaEstimator` that runs the inner estimators sequentially and returns the best peak + its template index. |
| `thriftyx/toads_data.py` | `CorrDetectionInfo` gains a `template_id` field. `serialize` / `deserialize` get one extra column. **Breaking change** to .toad/.toads format. |
| `thrifty/toads_data.py` | Same as above for upstream-compat. |
| `thriftyx/identify.py` | If `template_id` is present, prefer it over carrier-frequency-based classification. |
| `docs/user_guide.md` | Document the directory layout. |

### New tests

- `tests/unit/test_multi_template_dir.py` — directory load, per-block
  selection of correct template against synthetic dual-TX data,
  fallback to single template when only one .npy is present.
- Integration: extend `tests/integration/test_full_pipeline.py` with
  a two-TX synthetic run.

### .toad output format

Extends with one new column. Backward compatibility plan: write
`-1` (or omit) when no `template_id` is meaningful (single-template
mode). Reader uses `len(fields)` to decide whether the new column is
present. Avoid breaking older `analyze_detect` consumers by gating the
extension on a header flag (e.g. `#format: toad+template`).

## 3. Option 2 — `--template-map` JSON keyed by txid

API: `--template-map templates.json` where the JSON is e.g.

```json
{"1": "tx1.npy", "2": "tx2.npy"}
```

Combined with `--freq-map`, the detector uses the carrier-bin lookup
to pick the txid, then loads the matching template *for that block
only*.

### Files that change

| File | Change |
|---|---|
| `thriftyx/detect.py` | Parse `--template-map`; load all templates into a `dict[txid, SoaEstimator]` at startup. |
| `thriftyx/detect.py` `Detector.detect` | After `carrier_info` is computed, look up txid via the existing `freq_map → identify_transmitters` machinery (currently happens *after* detection in `thriftyx/identify.py`). This means hoisting freq-map evaluation to detection time, **OR** running the carrier-bin lookup inline. |
| `thriftyx/soa_estimator.py` | No change. |
| `thriftyx/toads_data.py` | Same `template_id` column extension as Option 1. |
| `thriftyx/identify.py` | If `template_id` is set during detection, skip the auto-classifier for that detection. |
| `docs/user_guide.md` | Document the JSON format and `--template-map` flag. |

### New tests

- `tests/unit/test_template_map.py` — JSON parsing, txid→template lookup,
  fallback to default-template when carrier-bin doesn't match any range.
- Integration: end-to-end run on synthetic dual-TX data with explicit
  `--template-map` and `--freq-map`.

### .toad output format

Same single-column extension as Option 1.

## 4. Cost estimate

Single FFT correlation at Thrifty-X's R2 parameters (block_size=65536,
template_len=10232):

- `signal.fft` (size 65536) — single per block, already done in carrier
  sync.
- `corr_fft = signal_fft * template_fft.conj` — O(block_size).
- `corr.ifft` (size 65536) — single per block.

The dominant cost is the IFFT (and possibly the multiply). For N
templates:

| Option | FFTs per block | Multiplies per block | IFFTs per block |
|---|---|---|---|
| Single-template (status quo) | 1 (already done in sync) | 1 | 1 |
| Option 1 (N templates, all-vs-all) | 1 | N | N |
| Option 2 (N templates, txid-guided) | 1 | 1 | 1 (one chosen template) |

**Option 1's cost grows linearly with N.** With Thrifty-X's 2 templates
(TX1, TX2) it doubles correlation cost. Per the rpi5 deployment
report (`docs/rpi5_deployment_report.md` §1.1: "single-thread FFT
... 10 MSPS"), the Pi 5 has headroom to absorb 2x correlation cost in
batch mode but it could matter at real-time edge.

**Option 2's cost is identical to the status quo** — we still only do
one correlation per block, just against a different template.

For larger N (e.g. 8+ collared animals), Option 1 becomes
prohibitively expensive on Pi 5 in real-time; Option 2 stays flat.

## 5. Risk register

| Risk | Probability | Mitigation |
|---|---|---|
| Option 1: cross-template false positives. TX1 signal weakly correlates with TX2 template; "best peak" picks the wrong template at low SNR. | Medium | After the multi-template best-match, run a secondary check that the *winning* peak's carrier bin matches the expected range for its template's txid. Reject if mismatch. |
| Option 2: misclassified txid (carrier-bin falls into the wrong freq_map range) silently uses the wrong template, depressing peak_mag. | Low (freq_map is human-set, usually correct) | Fall back to "try the default template" when txid is unidentified. Emit a `WARN` log when freq_map's coverage misses a detection. |
| .toad format extension breaks downstream consumers (custom scripts that parse the fixed column count). | Medium | Gate the new column on a `#format` header line. Default to *not* emitting the column when run in single-template mode (preserves byte-for-byte compatibility). |
| Increased detection latency in real-time mode (Option 1 only). | High for N>=4 | Document the latency tradeoff in user_guide. Recommend Option 2 over Option 1 for >2 templates. |
| Both options: template lengths differ across TXs. `SoaEstimator` assumes a single `template_len` for its window computation. | Low (all BatRF beacons use the same length) | Either require all templates to be the same length (asserted at load time) or refactor `calculate_window` to be per-template. |

## 6. Test plan

### Unit tests
- Parse `--template-map` JSON with valid and malformed inputs.
- Load N templates of the same length; FAIL if lengths differ (Option
  2, or relax this for Option 1 + per-template window).
- Per-block detection picks the correct template index against
  synthetic data with carrier in known bin.
- .toad serialize / deserialize round-trips the new `template_id`
  field on both modes (with the column present, without it).

### Integration tests
- End-to-end run on synthetic dual-TX block: assert both TXs detected
  in a single pass, with correct txid attributed in the `.toad` line.
- Performance smoke test: confirm Option 1 doubles correlation time
  (within 20%) on a non-Pi reference machine for N=2.

### Regression tests
- All existing tests in `tests/test_soa_estimator.py` must continue to
  pass with the single-template default code path unchanged.
- `tests/integration/test_full_pipeline.py` continues to pass without
  the new flag.

## 7. Why Option 2 wins

1. **Cost.** Per-block work is unchanged; throughput on Pi 5 stays
   intact.
2. **Semantics.** The freq-map → txid → template chain is the natural
   evolution of how Thrifty already uses `--freq-map`. A user who's
   already curated a freq-map only needs to provide a parallel
   template-map.
3. **Test surface.** Single template selection per block keeps the
   .toad output simple and the downstream identify path unchanged
   (template_id is just a *labeled* observation, not a fallback for
   identify).
4. **Failure mode.** If the freq-map is wrong, you get one wrong
   classification; you don't get a worst-case "every block trying
   every template" cost explosion.

The trade-off is that Option 2 *cannot* fall back to a different
template when the freq-map is silent. Mitigation: support a default
template via the existing `--template` flag, used when txid is
unidentified.

## 8. Out of scope for this design

- Online template learning from the first few detections of each TX.
- Cross-correlation against a *bank* of templates simultaneously
  (would require a different signal-processing approach; not a quick
  win on existing scipy.fft hardware).
- Per-template threshold tuning (separate concern; see
  `docs/verification/threshold_path.md`).

## 9. Implementation order if/when this is greenlit

1. Land PR #39 (sub_offset) and PR #40 (normalization). Both
   investigations affect the correlation path.
2. Add `template_id` to `CorrDetectionInfo` and the .toad format,
   gated behind a header flag. Land before the multi-template
   feature.
3. Implement Option 2 (`--template-map`) behind a feature flag.
4. Add unit + integration tests.
5. Optional: implement Option 1 if a customer asks for
   freq-map-free operation.

## 10. Linked artefacts

- `docs/verification/threshold_path.md` — confirms thresholds are
  template-agnostic (so multi-template doesn't need per-template
  thresholds).
- `docs/verification/auto_classify_robustness.md` — explains why
  `--freq-map` is already the recommended production workflow.
- `docs/rpi5_deployment_report.md` §1.1 — Pi 5 throughput context.
