/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <stdlib.h>

#include "stamp_queue.h"

bool stamp_queue_init(stamp_queue_t *q, size_t capacity,
                      uint32_t sample_rate) {
    q->items = calloc(capacity, sizeof(sample_stamp_t));
    if (q->items == NULL || capacity == 0) {
        free(q->items);
        q->items = NULL;
        return false;
    }
    if (pthread_mutex_init(&q->mutex, NULL) != 0) {
        free(q->items);
        q->items = NULL;
        return false;
    }
    q->capacity = capacity;
    q->head = 0;
    q->count = 0;
    q->sample_rate = sample_rate;
    return true;
}

void stamp_queue_destroy(stamp_queue_t *q) {
    if (q->items == NULL) {
        return;
    }
    pthread_mutex_destroy(&q->mutex);
    free(q->items);
    q->items = NULL;
}

void stamp_queue_push(stamp_queue_t *q, uint64_t end_pair,
                      const struct timeval *arrived) {
    pthread_mutex_lock(&q->mutex);
    if (q->count == q->capacity) {
        q->head = (q->head + 1) % q->capacity;   /* drop the oldest */
        q->count--;
    }
    size_t tail = (q->head + q->count) % q->capacity;
    q->items[tail].end_pair = end_pair;
    q->items[tail].arrived = *arrived;
    q->count++;
    pthread_mutex_unlock(&q->mutex);
}

bool stamp_queue_time_of(stamp_queue_t *q, uint64_t pair,
                         struct timeval *out) {
    pthread_mutex_lock(&q->mutex);
    while (q->count > 0 && q->items[q->head].end_pair < pair) {
        q->head = (q->head + 1) % q->capacity;
        q->count--;
    }
    if (q->count == 0) {
        pthread_mutex_unlock(&q->mutex);
        return false;
    }
    sample_stamp_t rec = q->items[q->head];
    pthread_mutex_unlock(&q->mutex);

    /* `pair` arrived (end_pair - pair) sample periods before the
     * transfer's last pair. */
    int64_t usec = 0;
    if (q->sample_rate > 0) {
        usec = (int64_t)((rec.end_pair - pair) * 1000000ULL
                         / q->sample_rate);
    }
    int64_t t = (int64_t)rec.arrived.tv_sec * 1000000
                + rec.arrived.tv_usec - usec;
    out->tv_sec = (time_t)(t / 1000000);
    out->tv_usec = (suseconds_t)(t % 1000000);
    return true;
}
