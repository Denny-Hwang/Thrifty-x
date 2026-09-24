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
    check_geometry("2.4M", 16384, 4920);
    check_geometry("2.5M", 16384, 5182);
    check_geometry("3M", 16384, 6206);
    check_geometry("6M", 32768, 12349);
    check_geometry("10M", 65536, 20539);
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
    CHECK(fargs_finalize(fa) == FARGS_INVALID_VALUE);  /* 12349 >= 8192 */
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

/* strtoul took "-1" for ULONG_MAX: -h -1 then hung fargs_finalize
 * (the block doubled until it wrapped to 0) and -k -1 skipped every
 * block.  Counts are digits only, in range. */
static int parse(fargs_t* fa, int key, const char* value) {
    char arg[32];
    snprintf(arg, sizeof(arg), "%s", value);
    return fargs_parse_opt(fa, key, arg);
}

static void test_counts_are_range_checked(void) {
    fargs_t* fa = fargs_new();
    const char* bad_history[] = {"-1", "0", "65536", "18446744073709551615",
                                 "+5", " 5", "5x", ""};
    for (size_t i = 0; i < sizeof(bad_history) / sizeof(*bad_history);
            ++i) {
        CHECK(parse(fa, 'h', bad_history[i]) == FARGS_INVALID_VALUE);
    }
    CHECK(!fa->history_len_set);
    CHECK(parse(fa, 'h', "65535") == 0 && fa->history_len == 65535);

    CHECK(parse(fa, 'k', "-1") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'k', "4294967296") == FARGS_INVALID_VALUE);
    CHECK(fa->skip == 1);
    CHECK(parse(fa, 'k', "4294967295") == 0 && fa->skip == 4294967295u);
    CHECK(parse(fa, 'k', "0") == 0 && fa->skip == 0);

    CHECK(parse(fa, 'b', "-1") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'b', "-65536") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'b', "131072") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'b', "3000") == FARGS_INVALID_VALUE);
    CHECK(!fa->block_len_set);

    CHECK(parse(fa, 'd', "-1") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'd', "4294967296") == FARGS_INVALID_VALUE);
    CHECK(parse(fa, 'd', "2") == 0 && fa->sdr_dev_index == 2);
    free(fa);
}

/* A history too long for any block ends with an error, not a loop:
 * the ctest TIMEOUT fails a hang. */
static void test_block_search_is_bounded(void) {
    fargs_t* fa = parsed("6M");
    CHECK(parse(fa, 'h', "65535") == 0);
    CHECK(fargs_finalize(fa) == FARGS_INVALID_VALUE);
    free(fa);

    fa = parsed("6M");
    fa->history_len = (size_t)-1;       /* what "-h -1" used to give */
    fa->history_len_set = true;
    CHECK(fargs_finalize(fa) == FARGS_INVALID_VALUE);
    free(fa);
}

int main(void) {
    test_geometry_matches_python();
    test_explicit_geometry_is_kept();
    test_history_must_fit_block();
    test_bad_values_are_rejected();
    test_counts_are_range_checked();
    test_block_search_is_bounded();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("fargs tests passed\n");
    return 0;
}
