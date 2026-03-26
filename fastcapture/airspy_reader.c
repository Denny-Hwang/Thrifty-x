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
#include <time.h>

#include <airspy.h>

#include "airspy_reader.h"
#include "circbuf.h"
#include "reader.h"

/* Internal state for async capture */
typedef struct {
    struct airspy_device *device;
    circbuf_t            circbuf;
    volatile int         running;
    reader_t            *reader;   /* back-pointer for dispatch */
} airspy_state_t;


static int _airspy_callback(airspy_transfer_t *transfer)
{
    airspy_state_t *state = (airspy_state_t *)transfer->ctx;
    if (!state->running || transfer->sample_count <= 0)
        return 0;

    /* Samples are 12-bit signed, packed in int16 (lower 12 bits valid) */
    int16_t *src = (int16_t *)transfer->samples;
    size_t   n   = (size_t)(transfer->sample_count) * 2; /* I and Q */

    circbuf_put(&state->circbuf, (char *)src, n * sizeof(int16_t));
    return 0;
}


static int _reader_read_next(reader_t *reader)
{
    airspy_state_t *state = (airspy_state_t *)reader->context;
    size_t needed = (size_t)(reader->block_size) * 2 * sizeof(int16_t);
    int16_t *dst  = (int16_t *)reader->raw_samples;

    bool success = circbuf_get(&state->circbuf, (char *)dst, needed);
    if (!success) {
        return -1;
    }
    return 0;
}


static int _airspy_reader_next(void *context)
{
    /* The reader_t is found by offsetting from context -- but we stored
       the reader pointer nowhere.  Instead, airspy_reader_open sets
       reader->next to this function and reader->context to the state,
       and the dispatch layer in reader.c calls reader->next(reader->context).
       We need the reader_t* to call _reader_read_next.  So we store a
       back-pointer in airspy_state_t. */
    airspy_state_t *state = (airspy_state_t *)context;
    /* We need the reader_t to call _reader_read_next.  We stored it
       via the backpointer set in airspy_reader_open. */
    return _reader_read_next(state->reader);
}

static int _airspy_reader_stop(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    state->running = 0;
    if (state->device) {
        airspy_stop_rx(state->device);
    }
    circbuf_cancel(&state->circbuf);
    return 0;
}

static void _airspy_reader_cancel(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    state->running = 0;
    circbuf_cancel(&state->circbuf);
}

static void _airspy_reader_free(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    if (!state) return;
    state->running = 0;
    if (state->device) {
        airspy_stop_rx(state->device);
        airspy_close(state->device);
    }
    circbuf_free(&state->circbuf);
    free(state);
}


int airspy_reader_open(const airspy_reader_config_t *config,
                       reader_t *reader)
{
    int ret;
    airspy_state_t *state = calloc(1, sizeof(airspy_state_t));
    if (!state) return -1;

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
    circbuf_init(&state->circbuf, circbuf_size);

    state->running = 1;
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
    state->running = 0;
    if (state->device) {
        airspy_stop_rx(state->device);
        airspy_close(state->device);
    }
    circbuf_free(&state->circbuf);
    free(state);
    reader->ctx = NULL;
}
