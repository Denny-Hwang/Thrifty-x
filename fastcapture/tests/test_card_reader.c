/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Replaying a card must use the block geometry it was captured with:
 * SoA = (block_size - history) * index + peak, so a different history
 * shifts every SoA.  The reader checks the geometry the header records,
 * and refuses a card that records no history unless one is given. */

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "card_reader.h"
#include "lib/base64.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

enum { BLOCK = 16, HISTORY = 4 };

#define V2_6M "#v2 bit_depth=12 sample_rate=6000000 endian=little "

typedef struct {
    int header;         /* card_reader_read_header's return */
    uint32_t rate;      /* the sample rate it gave */
    long header_end;    /* file position after it */
    int next;           /* the first reader_next's return */
    char err[1024];     /* what the reader printed */
} result_t;

/* Read the first block (index 3) of a card with header `header` and
 * `trailer` after that block, at BLOCK/HISTORY (-h given when
 * `history_set`).  `read_header`: call card_reader_read_header first,
 * as fastcard_new does; otherwise reader_next reads the header. */
static result_t replay(const char* header, const char* trailer,
                       bool history_set, bool read_header) {
    result_t result = {0, 0, -1, 0, ""};
    int16_t samples[2 * BLOCK];
    for (int i = 0; i < 2 * BLOCK; ++i) samples[i] = (int16_t)(i * 7);
    char encoded[256];
    Base64encode(encoded, (const char*)samples, sizeof(samples));

    FILE* file = tmpfile();
    fprintf(file, "%s1000.500000 3 %s\n%s", header, encoded, trailer);
    rewind(file);

    /* The reader explains a refusal on stderr: keep it. */
    fflush(stderr);
    int saved_stderr = dup(STDERR_FILENO);
    FILE* err = tmpfile();
    dup2(fileno(err), STDERR_FILENO);

    block_t* block = reader_block_new(BLOCK);
    reader_settings_t settings = { block, BLOCK, HISTORY, history_set };
    reader_t* reader = card_reader_new(settings, file);
    if (read_header) {
        result.header = card_reader_read_header(reader, &result.rate);
        result.header_end = ftell(file);
        /* A second call gives the same answer. */
        uint32_t rate;
        CHECK(card_reader_read_header(reader, &rate) == result.header);
        CHECK(rate == result.rate);
    }
    result.next = reader->next(reader->context);
    if (result.next == 0) {
        CHECK(block->index == 3);
        CHECK(memcmp(block->raw_samples, samples, sizeof(samples)) == 0);
        int ret = reader->next(reader->context);
        if (ret != 1) {
            result.next = ret;      /* what the trailer gave */
        }
    }
    reader->free(reader->context);
    free(reader);
    reader_block_free(block);
    fclose(file);

    fflush(stderr);
    dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
    rewind(err);
    size_t len = fread(result.err, 1, sizeof(result.err) - 1, err);
    result.err[len] = '\0';
    fclose(err);
    return result;
}

static int read_first_block(const char* header) {
    return replay(header, "", false, false).next;
}

static void test_recorded_geometry_must_match(void) {
    CHECK(read_first_block(
        V2_6M "block_size=16 block_history=4\n# tool: 'x'\n") == 0);
    CHECK(read_first_block(
        V2_6M "block_size=16 block_history=5\n") == -7);  /* history */
    CHECK(read_first_block(
        V2_6M "block_size=32 block_history=4\n") == -7);  /* size */
    /* Older fastcapture cards: history only on the "# arguments" line. */
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=0 endian=little block_size=16\n"
        "# arguments: { carrier_bin: '0-15', threshold: '0c+5s', "
        "block_size: 16, history_size: 4 }\n") == 0);
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=0 endian=little block_size=16\n"
        "# arguments: { carrier_bin: '0-15', threshold: '0c+5s', "
        "block_size: 16, history_size: 9 }\n") == -7);
    /* Before 611e320 the #v2 line held no sizes at all. */
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=6000000\n"
        "# arguments: { carrier_bin: '0-15', threshold: '0c+5s', "
        "block_size: 32, history_size: 4 }\n") == -7);
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=6000000\n"
        "# arguments: { carrier_bin: '0-15', threshold: '0c+5s', "
        "block_size: 16, history_size: 4 }\n") == 0);
    /* A -h that disagrees with the header is refused all the same. */
    CHECK(replay(V2_6M "block_size=16 block_history=5\n", "", true,
                 true).header == -7);
}

/* Python cards before 30fc39a recorded no history; SoAs replayed with
 * this run's derived history were silently off (12349 instead of the
 * 12278 capture used at 6M), and a re-emitted card recorded the guess. */
static void test_history_must_be_recorded_or_given(void) {
    result_t r = replay(V2_6M "block_size=16\n", "", false, true);
    CHECK(r.header == -8 && r.next == -8);
    CHECK(strstr(r.err, "records no block history") != NULL);
    CHECK(strstr(r.err, "-h 12278") != NULL);  /* Python's rule at 6M */
    r = replay("#v2 bit_depth=12 sample_rate=10000000 endian=little "
               "block_size=16\n", "", false, true);
    CHECK(r.header == -8 && strstr(r.err, "-h 20464") != NULL);
    r = replay("#v2 bit_depth=12 sample_rate=3000000\n", "", false, true);
    CHECK(r.header == -8 && strstr(r.err, "-h 4920") != NULL);
    r = replay("#v2 bit_depth=12 sample_rate=0 endian=little "
               "block_size=16\n", "", false, true);
    CHECK(r.header == -8 && strstr(r.err, "-h <") != NULL);
    CHECK(read_first_block(V2_6M "block_size=16\n") == -8);  /* lazily */

    /* No header at all: nothing records the history either. */
    r = replay("", "", false, true);
    CHECK(r.header == -8 && strstr(r.err, "no #v2 header") != NULL);
    CHECK(read_first_block("") == -8);

    /* -h given: the card is replayed with it. */
    r = replay(V2_6M "block_size=16\n", "", true, true);
    CHECK(r.header == 0 && r.next == 0 && r.err[0] == '\0');
    CHECK(replay("", "", true, true).next == 0);
    CHECK(replay("", "", true, false).next == 0);
}

static void test_empty_input_is_no_error(void) {
    FILE* file = tmpfile();
    block_t* block = reader_block_new(BLOCK);
    reader_settings_t settings = { block, BLOCK, HISTORY, false };
    reader_t* reader = card_reader_new(settings, file);
    uint32_t rate = 1;
    CHECK(card_reader_read_header(reader, &rate) == 0 && rate == 0);
    CHECK(reader->next(reader->context) == 1);
    reader->free(reader->context);
    free(reader);
    reader_block_free(block);
    fclose(file);
}

/* The rate the #v2 line records is passed on (for re-emitted cards). */
static void test_sample_rate_is_read(void) {
    result_t r = replay(V2_6M "block_size=16 block_history=4\n", "",
                        false, true);
    CHECK(r.header == 0 && r.rate == 6000000 && r.next == 0);
    r = replay("#v2 bit_depth=12 sample_rate=2500000.0 endian=little "
               "block_size=16 block_history=4\n", "", false, true);
    CHECK(r.header == 0 && r.rate == 2500000);        /* Python's float */
    r = replay("#v2 bit_depth=12 sample_rate=0 endian=little "
               "block_size=16 block_history=4\n", "", false, true);
    CHECK(r.header == 0 && r.rate == 0);
    CHECK(replay("", "", true, true).rate == 0);
}

/* With the history known from the #v2 line the header read stops
 * there: on a pipe it must not wait for the first block. */
static void test_header_read_stops_at_known_history(void) {
    const char* v2 = V2_6M "block_size=16 block_history=4\n";
    result_t r = replay(v2, "", false, true);
    CHECK(r.header_end == (long)strlen(v2));
    const char* old = "#v2 bit_depth=12 sample_rate=0 endian=little "
                      "block_size=16\n"
                      "# arguments: { block_size: 16, history_size: 4 }\n";
    r = replay(old, "", false, true);
    CHECK(r.header == 0 && r.header_end == (long)strlen(old));
}

/* Joined cards: each header is checked when its blocks come. */
static void test_later_headers_are_checked(void) {
    const char* v2 = V2_6M "block_size=16 block_history=4\n";
    char trailer[256];
    snprintf(trailer, sizeof(trailer), "%s", v2);
    CHECK(replay(v2, trailer, false, true).next == 0);    /* EOF after */
    CHECK(replay(v2, V2_6M "block_size=16\n1000.6 4 x\n", false,
                 true).next == -8);
    CHECK(replay(v2, "#v2 bit_depth=12 sample_rate=3000000 endian=little "
                 "block_size=16 block_history=4\n1000.6 4 x\n", false,
                 true).next == -7);
    CHECK(replay(v2, V2_6M "block_size=16 block_history=5\n1000.6 4 x\n",
                 false, true).next == -7);
}

int main(void) {
    test_recorded_geometry_must_match();
    test_history_must_be_recorded_or_given();
    test_empty_input_is_no_error();
    test_sample_rate_is_read();
    test_header_read_stops_at_known_history();
    test_later_headers_are_checked();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("card_reader tests passed\n");
    return 0;
}
