/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Unit tests for the circular buffer that carries libairspy transfers
 * to the block reader.  A hang is a failure: alarm() aborts the run. */

#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "circbuf.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

static void fill(char *buf, size_t len, char seed) {
    for (size_t i = 0; i < len; ++i) buf[i] = (char)(seed + i);
}

/* The regression: a put of exactly `size` bytes into an empty ring
 * used to wait forever (len + size >= size was always true). */
static void test_put_exactly_size_does_not_block(void) {
    enum { SIZE = 4096 };
    char in[SIZE], out[SIZE];
    circbuf_t cb;
    CHECK(circbuf_init(&cb, SIZE));
    fill(in, SIZE, 7);
    CHECK(circbuf_put(&cb, in, SIZE));
    CHECK(cb.len == SIZE);
    CHECK(circbuf_overflows(&cb) == 0);
    CHECK(circbuf_get(&cb, out, SIZE));
    CHECK(memcmp(in, out, SIZE) == 0);
    circbuf_destroy(&cb);
}

static void test_put_larger_than_size_fails(void) {
    char in[65];
    circbuf_t cb;
    CHECK(circbuf_init(&cb, 64));
    CHECK(!circbuf_put(&cb, in, sizeof(in)));
    circbuf_destroy(&cb);
}

/* Fill to exactly full across the wrap point and read it back in order. */
static void test_fill_to_full_across_wrap(void) {
    enum { SIZE = 16 };
    char a[3], b[SIZE], out[SIZE];
    circbuf_t cb;
    CHECK(circbuf_init(&cb, SIZE));
    fill(a, sizeof(a), 0);
    CHECK(circbuf_put(&cb, a, sizeof(a)));
    CHECK(circbuf_get(&cb, out, 2));       /* head=3, tail=2, len=1 */
    fill(b, SIZE - 1, 100);
    CHECK(circbuf_put(&cb, b, SIZE - 1));  /* wraps; len == SIZE */
    CHECK(cb.len == SIZE);
    CHECK(circbuf_get(&cb, out, SIZE));
    CHECK(out[0] == a[2]);
    CHECK(memcmp(out + 1, b, SIZE - 1) == 0);
    circbuf_destroy(&cb);
}

struct producer_args {
    circbuf_t *cb;
    char *data;
    size_t len;
    int ok;
};

static void *producer(void *arg) {
    struct producer_args *p = arg;
    p->ok = circbuf_put(p->cb, p->data, p->len);
    return NULL;
}

/* A put that does not fit waits for the consumer, counts one overflow,
 * and completes once space is freed. */
static void test_put_waits_for_consumer(void) {
    enum { SIZE = 32 };
    char first[SIZE], second[8], out[SIZE];
    circbuf_t cb;
    pthread_t thread;
    CHECK(circbuf_init(&cb, SIZE));
    fill(first, SIZE, 1);
    fill(second, sizeof(second), 50);
    CHECK(circbuf_put(&cb, first, SIZE));
    struct producer_args p = { &cb, second, sizeof(second), 0 };
    CHECK(pthread_create(&thread, NULL, producer, &p) == 0);
    /* Wait until the producer has found the ring full and is blocked
     * (alarm() bounds this loop). */
    while (circbuf_overflows(&cb) == 0) usleep(1000);
    CHECK(circbuf_get(&cb, out, sizeof(second)));
    pthread_join(thread, NULL);
    CHECK(p.ok);
    CHECK(circbuf_overflows(&cb) == 1);
    CHECK(circbuf_get(&cb, out, SIZE));
    CHECK(memcmp(out, first + sizeof(second), SIZE - sizeof(second)) == 0);
    CHECK(memcmp(out + SIZE - sizeof(second), second, sizeof(second)) == 0);
    circbuf_destroy(&cb);
}

/* cancel() releases a producer blocked on a full ring. */
static void test_cancel_unblocks_producer(void) {
    enum { SIZE = 16 };
    char full[SIZE], more[1];
    circbuf_t cb;
    pthread_t thread;
    CHECK(circbuf_init(&cb, SIZE));
    CHECK(circbuf_put(&cb, full, SIZE));
    struct producer_args p = { &cb, more, sizeof(more), 1 };
    CHECK(pthread_create(&thread, NULL, producer, &p) == 0);
    while (circbuf_overflows(&cb) == 0) usleep(1000);
    circbuf_cancel(&cb);
    pthread_join(thread, NULL);
    CHECK(!p.ok);
    circbuf_destroy(&cb);
}

/* A stalled producer (Airspy unplugged) must not block the reader
 * forever: the timed get returns CIRCBUF_TIMEOUT, consumes nothing, and
 * succeeds once the data arrives. */
static void test_get_timeout_consumes_nothing(void) {
    enum { SIZE = 64 };
    char in[16], out[16];
    circbuf_t cb;
    CHECK(circbuf_init(&cb, SIZE));
    fill(in, sizeof(in), 3);
    CHECK(circbuf_put(&cb, in, 8));
    CHECK(circbuf_get_timeout(&cb, out, 16, 50) == CIRCBUF_TIMEOUT);
    CHECK(cb.len == 8);
    CHECK(circbuf_put(&cb, in + 8, 8));
    CHECK(circbuf_get_timeout(&cb, out, 16, 50) == CIRCBUF_OK);
    CHECK(memcmp(in, out, 16) == 0);
    circbuf_cancel(&cb);
    CHECK(circbuf_get_timeout(&cb, out, 1, 50) == CIRCBUF_CANCELLED);
    circbuf_destroy(&cb);
}

int main(void) {
    alarm(10);  /* any deadlock fails the test instead of hanging CI */
    test_get_timeout_consumes_nothing();
    test_put_exactly_size_does_not_block();
    test_put_larger_than_size_fails();
    test_fill_to_full_across_wrap();
    test_put_waits_for_consumer();
    test_cancel_unblocks_producer();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("circbuf tests passed\n");
    return 0;
}
