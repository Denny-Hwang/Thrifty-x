/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/*
 * Arrival times of SDR transfers, for timestamping blocks at reception.
 *
 * The SDR callback records, for each transfer, the cumulative number of
 * I/Q pairs delivered so far and the wall-clock time the transfer
 * arrived.  The block reader later asks when a given cumulative sample
 * position arrived, however long the samples waited in the ring buffer.
 * The producer (callback thread) and consumer (reader) may run
 * concurrently; all operations take the internal mutex.
 */

#ifndef STAMP_QUEUE_H
#define STAMP_QUEUE_H

#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/time.h>

typedef struct {
    uint64_t end_pair;      /* cumulative I/Q pairs after this transfer */
    struct timeval arrived; /* when the transfer (its last pair) arrived */
} sample_stamp_t;

typedef struct {
    sample_stamp_t *items;
    size_t capacity;
    size_t head;            /* index of the oldest record */
    size_t count;
    uint32_t sample_rate;   /* pairs per second, to back-date positions */
    pthread_mutex_t mutex;
} stamp_queue_t;

/// Allocate a queue holding up to `capacity` transfer records.
bool stamp_queue_init(stamp_queue_t *q, size_t capacity,
                      uint32_t sample_rate);

void stamp_queue_destroy(stamp_queue_t *q);

/// Record that the stream reached `end_pair` cumulative pairs at
/// `arrived`.  When full, the oldest record is discarded; positions it
/// covered are then back-dated from a later record.
void stamp_queue_push(stamp_queue_t *q, uint64_t end_pair,
                      const struct timeval *arrived);

/// Arrival time of the `pair`-th cumulative I/Q pair (1-based).  Uses
/// the first transfer that contains it and back-dates by the pairs that
/// followed it within that transfer.  Records wholly before `pair` are
/// discarded, so positions must be queried in non-decreasing order.
/// Returns false when no recorded transfer contains `pair` yet.
bool stamp_queue_time_of(stamp_queue_t *q, uint64_t pair,
                         struct timeval *out);

#endif /* STAMP_QUEUE_H */
