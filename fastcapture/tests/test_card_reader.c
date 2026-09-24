/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Replaying a card must use the block geometry it was captured with:
 * SoA = (block_size - history) * index + peak, so a different history
 * shifts every SoA.  The reader checks the geometry the header records. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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

/* Read the first block of a card with header `header` at BLOCK/HISTORY. */
static int read_first_block(const char* header) {
    int16_t samples[2 * BLOCK];
    for (int i = 0; i < 2 * BLOCK; ++i) samples[i] = (int16_t)(i * 7);
    char encoded[256];
    Base64encode(encoded, (const char*)samples, sizeof(samples));

    FILE* file = tmpfile();
    fprintf(file, "%s1000.500000 3 %s\n", header, encoded);
    rewind(file);

    block_t* block = reader_block_new(BLOCK);
    reader_settings_t settings = { block, BLOCK, HISTORY };
    reader_t* reader = card_reader_new(settings, file);
    int ret = reader->next(reader->context);
    if (ret == 0) {
        CHECK(block->index == 3);
        CHECK(memcmp(block->raw_samples, samples, sizeof(samples)) == 0);
    }
    reader->free(reader->context);
    free(reader);
    reader_block_free(block);
    fclose(file);
    return ret;
}

int main(void) {
    CHECK(read_first_block("") == 0);                     /* no header */
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=6000000 endian=little "
        "block_size=16 block_history=4\n# tool: 'x'\n") == 0);
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=6000000 endian=little "
        "block_size=16 block_history=5\n") == -7);        /* history */
    CHECK(read_first_block(
        "#v2 bit_depth=12 sample_rate=6000000 endian=little "
        "block_size=32 block_history=4\n") == -7);        /* size */
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
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("card_reader tests passed\n");
    return 0;
}
