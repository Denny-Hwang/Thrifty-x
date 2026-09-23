/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Argument checks and the rate-derived block geometry, which must match
 * thriftyx capture (settings._auto_adjust_block_params). */

#include <stdio.h>
#include <stdlib.h>

#include "fargs.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

static fargs_t* parsed(const char* rate) {
    fargs_t* fa = fargs_new();
    char arg[32];
    snprintf(arg, sizeof(arg), "%s", rate);
    CHECK(fargs_parse_opt(fa, 's', arg) == 0);
    return fa;
}

static void check_geometry(const char* rate, size_t block, size_t history) {
    fargs_t* fa = parsed(rate);
    CHECK(fargs_finalize(fa) == 0);
    if (fa->block_len != block || fa->history_len != history) {
        fprintf(stderr, "%s: got %zu/%zu, expected %zu/%zu\n", rate,
                fa->block_len, fa->history_len, block, history);
        failures++;
    }
    free(fa);
}

static void test_geometry_matches_python(void) {
    check_geometry("2.5M", 16384, 4920);
    check_geometry("3M", 16384, 4920);
    check_geometry("6M", 32768, 12278);
    check_geometry("10M", 65536, 20464);
}

static void test_explicit_geometry_is_kept(void) {
    fargs_t* fa = parsed("6M");
    char b[] = "16384", h[] = "4920";
    CHECK(fargs_parse_opt(fa, 'b', b) == 0);
    CHECK(fargs_parse_opt(fa, 'h', h) == 0);
    CHECK(fargs_finalize(fa) == 0);
    CHECK(fa->block_len == 16384 && fa->history_len == 4920);
    free(fa);
}

static void test_history_must_fit_block(void) {
    fargs_t* fa = parsed("6M");
    char b[] = "8192";
    CHECK(fargs_parse_opt(fa, 'b', b) == 0);
    CHECK(fargs_finalize(fa) == FARGS_INVALID_VALUE);  /* 12278 >= 8192 */
    free(fa);
}

static void test_bad_values_are_rejected(void) {
    fargs_t* fa = fargs_new();
    char rate_index[] = "6", lna[] = "15", mixer[] = "-1", vga[] = "16";
    char freq[] = "10M", ok_lna[] = "14", ok_freq[] = "433.83M";
    CHECK(fargs_parse_opt(fa, 's', rate_index) == FARGS_INVALID_VALUE);
    CHECK(fargs_parse_opt(fa, 'g', lna) == FARGS_INVALID_VALUE);
    CHECK(fargs_parse_opt(fa, 'M', mixer) == FARGS_INVALID_VALUE);
    CHECK(fargs_parse_opt(fa, 'V', vga) == FARGS_INVALID_VALUE);
    CHECK(fargs_parse_opt(fa, 'f', freq) == FARGS_INVALID_VALUE);
    CHECK(fargs_parse_opt(fa, 'g', ok_lna) == 0 && fa->sdr_gain == 14);
    CHECK(fargs_parse_opt(fa, 'f', ok_freq) == 0
          && fa->sdr_freq == 433830000);
    free(fa);
}

int main(void) {
    test_geometry_matches_python();
    test_explicit_geometry_is_kept();
    test_history_must_fit_block();
    test_bad_values_are_rejected();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("fargs tests passed\n");
    return 0;
}
