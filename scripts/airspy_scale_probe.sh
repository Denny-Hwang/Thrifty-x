#!/usr/bin/env bash
# Measure the int16 scale of libairspy's AIRSPY_SAMPLE_INT16_IQ output.
#
# Compiles libairspy's own conversion code (convert_samples_int16, copied
# from airspy.c, plus iqconverter_int16.c and the HB_KERNEL_INT16 taps)
# at a pinned upstream commit, feeds it 12-bit ADC tones, and prints the
# resulting |I + jQ|.  This is the basis for AIRSPY_INT16_FULL_SCALE in
# thriftyx/block_data.py and the divisor in fastcapture/rawconv.c.
#
# Expected: a tone of A ADC codes gives |I + jQ| ~= 8 * A (2069 for 256),
# so ADC full scale (2048 codes) is ~16384.  Near full scale libairspy's
# int16 arithmetic starts to distort, so the linear rows are the ones to
# read.
#
# Usage: scripts/airspy_scale_probe.sh      (needs curl and a C compiler)
set -euo pipefail

COMMIT=fc61ab6be57ed61f0e2bdd9c6dfae74cacef57d0
BASE=https://raw.githubusercontent.com/airspy/airspyone_host/$COMMIT/libairspy/src
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

for f in iqconverter_int16.c iqconverter_int16.h filters.h; do
    curl -fsS -o "$WORK/$f" "$BASE/$f"
done

cat > "$WORK/probe.c" <<'C'
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include "iqconverter_int16.h"
#include "filters.h"

/* airspy.c: dest[i] = (src[i] - 2048) << SAMPLE_SHIFT, SAMPLE_SHIFT = 4 */
static void convert_samples_int16(const uint16_t *src, int16_t *dest, int n) {
    for (int i = 0; i < n; i++) dest[i] = (int16_t)((src[i] - 2048) << 4);
}

static void probe(double codes) {
    enum { N = 131072 };              /* one 256 KiB USB transfer */
    static uint16_t raw[N];
    static int16_t buf[N];
    iqconverter_int16_t *cnv =
        iqconverter_int16_create(HB_KERNEL_INT16, HB_KERNEL_INT16_LEN);
    for (int pass = 0; pass < 2; pass++) {   /* pass 0 settles the FIR */
        for (int i = 0; i < N; i++) {
            long c = lround(2048.0 + codes * cos(2 * M_PI * 0.3 * ((long)pass * N + i)));
            raw[i] = (uint16_t)(c < 0 ? 0 : c > 4095 ? 4095 : c);
        }
        convert_samples_int16(raw, buf, N);
        iqconverter_int16_process(cnv, buf, N);
    }
    double sum = 0;
    for (int k = 0; k < N / 2; k++)
        sum += hypot(buf[2 * k], buf[2 * k + 1]);
    printf("tone %5.0f ADC codes -> mean |I+jQ| = %7.0f  (%.2f per code)\n",
           codes, sum / (N / 2), sum / (N / 2) / codes);
}

int main(void) {
    probe(64); probe(256); probe(1024); probe(2047);
    return 0;
}
C

cc -O2 -I"$WORK" -o "$WORK/probe" "$WORK/probe.c" "$WORK/iqconverter_int16.c" -lm
"$WORK/probe"
