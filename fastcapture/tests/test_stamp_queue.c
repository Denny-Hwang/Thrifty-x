/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Unit tests for the transfer arrival-time queue used to timestamp
 * blocks at reception. */

#include <stdio.h>

#include "stamp_queue.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

static long long usec(const struct timeval *tv) {
    return (long long)tv->tv_sec * 1000000 + tv->tv_usec;
}

static struct timeval at(long long us) {
    struct timeval tv = { (time_t)(us / 1000000), (suseconds_t)(us % 1000000) };
    return tv;
}

/* 1 MSPS: one pair per microsecond makes back-dating easy to read. */
enum { RATE = 1000000, TRANSFER = 1000 };

/* A block ending mid-transfer is dated by that transfer's arrival,
 * back-dated by the pairs that followed it in the transfer, no matter
 * how late the block is read. */
static void test_block_dated_at_reception(void) {
    stamp_queue_t q;
    CHECK(stamp_queue_init(&q, 8, RATE));
    struct timeval t1 = at(5000000000LL), t2 = at(5000001000LL);
    stamp_queue_push(&q, TRANSFER, &t1);       /* pairs 1..1000 */
    stamp_queue_push(&q, 2 * TRANSFER, &t2);   /* pairs 1001..2000 */
    struct timeval out;
    CHECK(stamp_queue_time_of(&q, 1500, &out));
    CHECK(usec(&out) == 5000000500LL);         /* 500 pairs before t2 */
    CHECK(stamp_queue_time_of(&q, 2000, &out));
    CHECK(usec(&out) == 5000001000LL);
    stamp_queue_destroy(&q);
}

static void test_position_not_yet_recorded(void) {
    stamp_queue_t q;
    CHECK(stamp_queue_init(&q, 8, RATE));
    struct timeval t1 = at(1000000);
    struct timeval out;
    CHECK(!stamp_queue_time_of(&q, 1, &out));  /* empty */
    stamp_queue_push(&q, TRANSFER, &t1);
    CHECK(!stamp_queue_time_of(&q, TRANSFER + 1, &out));
    stamp_queue_destroy(&q);
}

/* Records wholly before a queried position are discarded. */
static void test_consumed_records_are_dropped(void) {
    stamp_queue_t q;
    CHECK(stamp_queue_init(&q, 8, RATE));
    for (int i = 1; i <= 5; ++i) {
        struct timeval t = at(1000000LL * i);
        stamp_queue_push(&q, (uint64_t)i * TRANSFER, &t);
    }
    struct timeval out;
    CHECK(stamp_queue_time_of(&q, 3 * TRANSFER + 1, &out));
    CHECK(q.count == 2);                       /* transfers 4 and 5 left */
    stamp_queue_destroy(&q);
}

/* When full, the oldest record goes; a position it covered is then
 * back-dated from the next record, which is still exact for a
 * gap-free stream. */
static void test_overflow_backdates_from_later_record(void) {
    stamp_queue_t q;
    CHECK(stamp_queue_init(&q, 2, RATE));
    for (int i = 1; i <= 3; ++i) {
        struct timeval t = at(10000000LL + 1000LL * i);   /* 1 ms apart */
        stamp_queue_push(&q, (uint64_t)i * TRANSFER, &t);
    }
    struct timeval out;
    CHECK(stamp_queue_time_of(&q, 500, &out));   /* first record dropped */
    CHECK(usec(&out) == 10000000LL + 2000 - 1500);
    stamp_queue_destroy(&q);
}

/* Back-dating across a second boundary keeps tv_usec in range. */
static void test_backdate_across_second_boundary(void) {
    stamp_queue_t q;
    CHECK(stamp_queue_init(&q, 4, RATE));
    struct timeval t = at(7000000LL + 100);    /* 7.000100 s */
    stamp_queue_push(&q, TRANSFER, &t);
    struct timeval out;
    CHECK(stamp_queue_time_of(&q, 1, &out));   /* 999 us earlier */
    CHECK(out.tv_sec == 6 && out.tv_usec == 999101);
    stamp_queue_destroy(&q);
}

int main(void) {
    test_block_dated_at_reception();
    test_position_not_yet_recorded();
    test_consumed_records_are_dropped();
    test_overflow_backdates_from_later_record();
    test_backdate_across_second_boundary();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("stamp_queue tests passed\n");
    return 0;
}
