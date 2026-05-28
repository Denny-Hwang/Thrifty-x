# Airspy INT16_IQ normalization divisor verification

**Status:** master is correct. `raw_to_complex(bit_depth=12)` in
`thriftyx/block_data.py:89` divides by `2048.0` (native 12-bit full
scale), which matches the empirical R2 capture behaviour documented in
`diag/iq_format_diagnosis_report.md` and what libairspy *actually*
emits after its full INT16_IQ processing chain. **No code change.**

The libairspy source alone *appears* to argue for `/32768.0` (the
``convert_samples_int16`` function applies a 4-bit left shift). On
closer inspection the post-conversion ``iqconverter_int16_process``
right-shifts by 15 bits inside the FIR accumulator, partially undoing
the upstream shift. The empirical answer (samples land in roughly
native 12-bit envelope, with occasional FIR overshoot into the wider
int16 container) is the correct one.

---

## 1. Premise check

The prompt's stated current divisor is `/2048.0`. After fetching
`origin/master`, that matches: master HEAD is `17f501a` (merge of PR
#37), and `thriftyx/block_data.py:89` reads `floats = floats / 2048.0`.
The earlier `/32768.0` divisor was reverted by PR #37 once empirical
R2 capture evidence accumulated.

| Commit | Direction | Justification |
|---|---|---|
| `2fac2f0` "correct sample normalization from /2048 to /32768" | 2048 -> 32768 | Aligned with the convert_samples_int16 source alone; pre-empirical. |
| `8b69e07` "harden 12-bit Airspy path" | (kept 32768) | Reshape + explicit unpacking; no scale change. |
| `7614cd5` (merged via PR #37) | 32768 -> 2048 | Empirical: real R2 captures show samples in native 12-bit envelope. |

## 2. Primary source: libairspy `airspy.c`

Repository: `airspy/airspyone_host`, file `libairspy/src/airspy.c`,
branch `master`. Fetched 2026-05-28.

### Step 1: `convert_samples_int16`

```c
// libairspy/src/airspy.c:53-57
#define SAMPLE_RESOLUTION    12
#define SAMPLE_ENCAPSULATION 16
#define SAMPLE_SHIFT (SAMPLE_ENCAPSULATION - SAMPLE_RESOLUTION)   // = 4
```

```c
// libairspy/src/airspy.c:283-291
static void convert_samples_int16(uint16_t *src, int16_t *dest, int count)
{
    int i;
    for (i = 0; i < count; i += 4)
    {
        dest[i + 0] = (src[i + 0] - 2048) << SAMPLE_SHIFT;
        // ... same for i+1, i+2, i+3
    }
}
```

In isolation this *suggests* output lands in `[-32768, +32767]`.

### Step 2: `iqconverter_int16_process` (the critical post-step)

For `AIRSPY_SAMPLE_INT16_IQ`, libairspy *always* runs the output of
step 1 through this FIR-based I/Q corrector:

```c
// libairspy/src/airspy.c:432-434
case AIRSPY_SAMPLE_INT16_IQ:
    convert_samples_int16(input_samples, (int16_t *)output_buffer, count);
    iqconverter_int16_process(device->cnv_i, (int16_t *)output_buffer, count);
```

The FIR convolution accumulates into a 32-bit ``acc`` and emits
``samples[i] = acc >> 15`` (`libairspy/src/iqconverter_int16.c:148`),
plus additional ``>> 1`` operations in `translate_fs_4` at lines 177
and 179. Net effect: the magnitude of the FIR output sits roughly at
``2^(16-15-1) = 2^0 = 1`` times the *original* 12-bit ADC value -
i.e. the post-conversion samples occupy approximately
`[-2048, +2047]`, with occasional FIR-overshoot excursions wider.

### What this means for the divisor

| Step | What it does to the magnitude |
|---|---|
| convert_samples_int16 | `<< 4` (multiply by 16) |
| iqconverter_int16_process | `>> 15` accumulator + `>> 1` in translate_fs_4 |
| Net | approximately *identity* on the 12-bit ADC scale |

The samples Thrifty-X reads via `np.frombuffer(..., dtype=np.int16)`
are at *native 12-bit scale*, not the post-shift scale that a naive
read of step 1 alone would predict. The right divisor to land in
`[-1, +1]` is therefore `/2048.0`.

### Why I initially read this wrong

My first pass cited only `convert_samples_int16` and treated the FIR
filter as a no-op for scale. That's incorrect: a FIR convolution with
the *unnormalised* coefficients used by `iqconverter_int16_process`
accumulates a value proportional to the sum of |coeffs| * input, which
is then divided by 2^15. The kernel is not unity-gain. See PR #37 for
the empirical confirmation that comes out the other side at native
12-bit scale.

## 3. HAL trace

`thriftyx/hal/airspy_mini.py:361-364` calls
`airspy_set_sample_type(handle, AIRSPY_SAMPLE_INT16_IQ)`. Both
post-processing steps (convert + iqconverter) are unconditionally
applied for that sample type. Thrifty-X reinterprets the resulting
buffer as `np.int16` and feeds it to `raw_to_complex(bit_depth=12)`,
which divides by `2048.0` per the empirically-calibrated master.

## 4. Empirical anchor (from diag report)

`gs_r2_161_3_20260513_152100_TX2_Gain000/b000/capture.card`
(`diag/iq_format_diagnosis_report.md` §9.3) at gain=0/0/0:

| Metric | Value | Interpretation |
|---|---|---|
| int16 min/max | -153 / +159 | Both well below +/-2048; consistent with low-gain on the 12-bit envelope. |
| int16 std | 15.94 | At gain=0 the noise floor is ~1 LSB in 12-bit terms; this is exactly what you'd see if the on-disk samples are at native 12-bit scale. |

If libairspy were truly emitting samples at the post-shift
`[-32768, +32767]` scale, gain=0 noise std would be ~256 LSB, not
~16 LSB. The empirical evidence rules out `/32768`.

A `gain=7/7/7` capture would land closer to the int16 ceiling; the
follow-up below covers running that capture once available.

## 5. Decision

| Question | Answer |
|---|---|
| What does libairspy ultimately emit for INT16_IQ? | Samples at approximately native 12-bit scale, after convert + iqconverter. |
| What divisor matches that? | `/2048.0`. |
| What does master use today? | `/2048.0`. |
| Is a code change warranted? | **No.** Master is correct. |
| Was PR #37 the right call? | **Yes** - empirical R2 evidence was the tiebreaker over a naive read of `convert_samples_int16` alone. |

## 6. Code-only tests added

`tests/unit/test_block_data_range.py` - 8 cases pinned to the
`/2048.0` convention:

| Test | Verifies |
|---|---|
| `test_native_12bit_maps_to_unit` | `+2047 -> +1.0`, `-2048 -> -1.0`. Would fail if a future change re-introduced `/32768`. |
| `test_half_native_maps_to_half_unit` | `1024 -> 0.5`. |
| `test_int16_overshoot_preserved_as_headroom` | `8192 -> 4.0`, documenting that samples *can* exceed +/-1 in rare FIR-overshoot. |
| `test_roundtrip_preserves_int16_within_one_lsb` | int16 -> complex -> int16 is lossless across the 12-bit envelope. |
| `test_post_normalisation_magnitudes_in_sensible_range` (3 params) | Median `|z|` lands in `[1e-4, 1.0]` at low/mid/high gain. |
| `test_v2_card_roundtrip_preserves_normalization` | Full .card write+read preserves magnitude. |

```
$ python -m pytest tests/unit/test_block_data_range.py -v
============================== 8 passed in 0.12s ===============================
```

## 7. Follow-up

1. When a `gain=7/7/7` R2 .card is available, run
   `python diag/check_signal_strength.py <card>` and confirm
   `max(|int16|)` lands around `+/-2048` (with possible FIR-overshoot
   excursions). That observation completes the empirical loop. If
   `max(|int16|)` instead reaches `+/-30000`, the assumptions in this
   report break and the `/32768.0` divisor needs to be reconsidered.
2. If libairspy ever ships a build that skips `iqconverter_int16_process`
   for INT16_IQ output, the net scale will jump 16x and detection
   thresholds will need re-tuning. Treat the divisor as a derived
   property of libairspy's full pipeline, not a constant.
3. A note in `docs/user_guide.md` explaining that magnitudes on the
   complex64 path are normalised to *roughly* `[-1, +1]` (with rare
   excursions up to ~`+/-2`) would help downstream code reviewers
   avoid making absolute-scale assumptions.
