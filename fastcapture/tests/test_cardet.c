/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* The carrier search must cover every bin of its window.  At 10M the
 * default block is 65536 bins and the default window the whole
 * spectrum, one bin more than volk_32f_index_max_16u searches. */

#include <stdio.h>
#include <stdlib.h>

#include "cardet.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

enum { FFT_LEN = 65536 };

/* Detect a lone peak at `peak` with the window `min`..`max` (negative
 * values count from the end, as -w does); return the argmax found, or
 * -1 when nothing is detected. */
static long detect_peak(size_t peak, int min, int max) {
    float* power = (float*)calloc(FFT_LEN, sizeof(float));
    for (size_t i = 0; i < FFT_LEN; ++i) power[i] = 1;
    power[peak] = 1e6f;

    cardet_settings_t settings = { 100, 2, min, max, FFT_LEN };
    CHECK(cardet_normalize_window(&settings) == 0);
    cardet_detection_t det;
    long found = cardet_detect(&settings, &det, power) ? (long)det.argmax
                                                       : -1;
    free(power);
    return found;
}

int main(void) {
    /* Whole-spectrum default window (0 - -1). */
    CHECK(detect_peak(FFT_LEN - 1, 0, -1) == FFT_LEN - 1);
    CHECK(detect_peak(FFT_LEN - 2, 0, -1) == FFT_LEN - 2);
    CHECK(detect_peak(0, 0, -1) == 0);
    CHECK(detect_peak(40000, 0, -1) == 40000);
    /* A window that starts past bin 0 still reaches its last bin. */
    CHECK(detect_peak(FFT_LEN - 1, 7, -1) == FFT_LEN - 1);
    CHECK(detect_peak(124, 7, 124) == 124);
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("cardet tests passed\n");
    return 0;
}
