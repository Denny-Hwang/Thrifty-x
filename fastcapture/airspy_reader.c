/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 * Based on Thrifty by Schalk Willem Krüger
 * (https://github.com/swkrueger/Thrifty)
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/**
 * @file airspy_reader.c
 * @brief Airspy SDR reader implementation for fastcapture.
 *
 * Replaces rtlsdr_reader.c. Uses libairspy to capture 12-bit signed
 * int16 I/Q samples from Airspy Mini or Airspy R2.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <time.h>
#include <sys/time.h>  /* gettimeofday */

#include <airspy.h>

#include "airspy_reader.h"
#include "circbuf.h"
#include "reader.h"

/* Internal state for async capture */
typedef struct {
    struct airspy_device *device;
    circbuf_t            circbuf;
    atomic_int           running;
    reader_t            *reader;   /* back-pointer for dispatch */
    block_t             *output;   /* block whose raw_samples we fill */
    size_t               history_size; /* history length in I/Q pairs */
} airspy_state_t;


static int _airspy_callback(airspy_transfer_t *transfer)
{
    airspy_state_t *state = (airspy_state_t *)transfer->ctx;
    if (!atomic_load(&state->running) || transfer->sample_count <= 0)
        return 0;

    /* Airspy 12-bit ADC samples in an int16 container.  libairspy does
     * NOT left-shift to full int16 range — values stay in the native
     * 12-bit envelope (see rawconv.c and
     * docs/verification/normalization_divisor.md). */
    int16_t *src = (int16_t *)transfer->samples;
    size_t   n   = (size_t)(transfer->sample_count) * 2; /* I and Q */

    circbuf_put(&state->circbuf, (char *)src, n * sizeof(int16_t));
    return 0;
}


/*
 * Refill ``reader->raw_samples`` with one new block.
 *
 * Layout matches raw_reader.c so the rest of fastcapture (carrier
 * detection, output) sees identical semantics regardless of the SDR:
 *   1. The last ``history_size * 2`` int16 values of the previous block
 *      are copied into the front of the buffer (history overlap).
 *   2. ``new_len = block_size - history_size`` fresh I/Q pairs (i.e.
 *      ``new_len * 2 * sizeof(int16_t)`` bytes) are pulled from the
 *      circular buffer into the tail.
 *   3. ``output->index`` is incremented and ``output->timestamp`` is
 *      stamped with the current wall-clock time.
 *
 * Without these three steps fastcapture would emit blocks with no
 * carrier-history overlap, frozen indices, and zero timestamps — which
 * would be silently malformed for downstream detect/identify stages.
 */
static int _reader_read_next(reader_t *reader)
{
    airspy_state_t *state = (airspy_state_t *)reader->context;
    size_t block_size = reader->block_size;
    size_t history_size = state->history_size;
    if (history_size > block_size) {
        return -1;
    }
    size_t new_len = block_size - history_size;

    int16_t *dst = (int16_t *)reader->raw_samples;

    /* 1. Copy history from tail of the previous block to the front.  Each
     *    sample is one int16; per-sample we have 2 int16 values (I + Q). */
    if (history_size > 0) {
        memmove(dst,
                dst + new_len * 2,
                history_size * 2 * sizeof(int16_t));
    }

    /* 2. Read new_len * 2 int16 worth of fresh samples from the ring
     *    buffer into the post-history portion of the buffer. */
    size_t needed = new_len * 2 * sizeof(int16_t);
    bool success = circbuf_get(&state->circbuf,
                                (char *)(dst + history_size * 2),
                                needed);
    if (!success) {
        return -1;
    }

    /* 3. Update block metadata. */
    if (state->output != NULL) {
        state->output->index++;
        gettimeofday(&state->output->timestamp, NULL);
    }
    return 0;
}


static int _airspy_reader_next(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    return _reader_read_next(state->reader);
}

static int _airspy_reader_stop(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    atomic_store(&state->running, 0);
    if (state->device) {
        airspy_stop_rx(state->device);
    }
    circbuf_cancel(&state->circbuf);
    return 0;
}

static void _airspy_reader_cancel(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    atomic_store(&state->running, 0);
    circbuf_cancel(&state->circbuf);
}

static void _airspy_reader_free(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    if (!state) return;
    atomic_store(&state->running, 0);
    if (state->device) {
        airspy_stop_rx(state->device);
        airspy_close(state->device);
    }
    circbuf_destroy(&state->circbuf);
    free(state);
}


int airspy_reader_open(const airspy_reader_config_t *config,
                       const reader_settings_t *settings,
                       reader_t *reader)
{
    int ret;
    if (settings == NULL || settings->output == NULL) {
        fprintf(stderr, "airspy_reader_open: settings/output required\n");
        return -1;
    }
    if (settings->history_size > settings->block_size) {
        fprintf(stderr,
                "airspy_reader_open: history_size (%zu) > block_size (%zu)\n",
                settings->history_size, settings->block_size);
        return -1;
    }
    airspy_state_t *state = calloc(1, sizeof(airspy_state_t));
    if (!state) return -1;

    state->output = settings->output;
    state->history_size = settings->history_size;

    ret = airspy_open(&state->device);
    if (ret != AIRSPY_SUCCESS) {
        fprintf(stderr, "airspy_open() failed: %s\n",
                airspy_error_name(ret));
        free(state);
        return -1;
    }

    ret = airspy_set_samplerate(state->device, config->sample_rate);
    if (ret != AIRSPY_SUCCESS) goto err;

    ret = airspy_set_freq(state->device, config->center_freq);
    if (ret != AIRSPY_SUCCESS) goto err;

    ret = airspy_set_lna_gain(state->device, config->lna_gain);
    if (ret != AIRSPY_SUCCESS) goto err;

    ret = airspy_set_mixer_gain(state->device, config->mixer_gain);
    if (ret != AIRSPY_SUCCESS) goto err;

    ret = airspy_set_vga_gain(state->device, config->vga_gain);
    if (ret != AIRSPY_SUCCESS) goto err;

    ret = airspy_set_rf_bias(state->device, config->bias_tee);
    if (ret != AIRSPY_SUCCESS) goto err;

    /* Sample type: signed int16 (AIRSPY_SAMPLE_INT16_IQ) */
    ret = airspy_set_sample_type(state->device, AIRSPY_SAMPLE_INT16_IQ);
    if (ret != AIRSPY_SUCCESS) goto err;

    /* Initialize circular buffer (holds 4 * block_size samples) */
    size_t circbuf_size = (size_t)(reader->block_size) * 4 * 2 * sizeof(int16_t);
    if (!circbuf_init(&state->circbuf, circbuf_size)) {
        fprintf(stderr, "circbuf_init failed: could not allocate %zu bytes\n",
                circbuf_size);
        airspy_close(state->device);
        free(state);
        return -1;
    }

    atomic_store(&state->running, 1);
    state->reader          = reader;
    reader->context        = state;
    reader->read_next  = _reader_read_next;
    reader->sample_format = SAMPLE_FORMAT_INT16;
    reader->next = (reader_func_t)_airspy_reader_next;
    reader->start = NULL;
    reader->stop = (reader_func_t)_airspy_reader_stop;
    reader->cancel = (reader_func_void_t)_airspy_reader_cancel;
    reader->free = (reader_func_void_t)_airspy_reader_free;

    ret = airspy_start_rx(state->device, _airspy_callback, state);
    if (ret != AIRSPY_SUCCESS) goto err;

    return 0;

err:
    fprintf(stderr, "airspy device configuration failed: %s\n",
            airspy_error_name(ret));
    airspy_close(state->device);
    free(state);
    return -1;
}


void airspy_reader_close(reader_t *reader)
{
    if (!reader || !reader->context) return;
    airspy_state_t *state = (airspy_state_t *)reader->context;
    atomic_store(&state->running, 0);
    if (state->device) {
        airspy_stop_rx(state->device);
        airspy_close(state->device);
    }
    circbuf_destroy(&state->circbuf);
    free(state);
    reader->context = NULL;
}
