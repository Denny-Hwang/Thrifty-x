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
#include "stamp_queue.h"

/* The reader wakes every AIRSPY_POLL_MS while waiting for samples to
 * check that the device is still streaming, and gives up after
 * AIRSPY_STALL_LIMIT_MS without any (the Python HAL uses 10 s too). */
#define AIRSPY_POLL_MS          1000
#define AIRSPY_STALL_LIMIT_MS  10000

/* Transfer arrival records kept for timestamping.  The ring holds at
 * most ~160 transfers (40 MB / 256 KiB); older records are discarded
 * and positions they covered are back-dated from a later one. */
#define AIRSPY_STAMP_CAPACITY 1024

/* Ring-buffer sizing.  libairspy hands the callback a fixed 256 KiB of
 * int16 I/Q per USB transfer, independent of the FFT block size, so the
 * ring must be sized from the sample rate, not from block_size: it has
 * to absorb at least one second of samples (4 bytes per I/Q pair) and
 * never less than the 32 MiB the original RTL-SDR fastcard used.  The
 * former block_size * 16 bytes was exactly one transfer at the default
 * block size, which deadlocked, and smaller than one transfer below it,
 * which silently discarded every sample. */
#define AIRSPY_RING_MIN_BYTES   ((size_t)32 * 1024 * 1024)
#define AIRSPY_RING_SECONDS     1
#define AIRSPY_BYTES_PER_SAMPLE (2 * sizeof(int16_t))

static size_t _ring_size(uint32_t sample_rate)
{
    size_t by_rate = (size_t)sample_rate * AIRSPY_RING_SECONDS
                     * AIRSPY_BYTES_PER_SAMPLE;
    return by_rate > AIRSPY_RING_MIN_BYTES ? by_rate : AIRSPY_RING_MIN_BYTES;
}

/* Internal state for async capture */
typedef struct {
    struct airspy_device *device;
    circbuf_t            circbuf;
    atomic_int           running;
    reader_t            *reader;   /* back-pointer for dispatch */
    block_t             *output;   /* block whose raw_samples we fill */
    size_t               history_size; /* history length in I/Q pairs */
    atomic_ullong        dropped_samples; /* libairspy-reported drops */
    atomic_int           drop_warned;     /* one-shot stderr warning */
    atomic_int           cancelled;       /* stopped on request (Ctrl-C) */
    stamp_queue_t        stamps;          /* transfer arrival times */
    uint64_t             produced_pairs;  /* callback thread only */
    uint64_t             consumed_pairs;  /* reader thread only */
} airspy_state_t;


static int _airspy_callback(airspy_transfer_t *transfer)
{
    airspy_state_t *state = (airspy_state_t *)transfer->ctx;
    if (!atomic_load(&state->running) || transfer->sample_count <= 0)
        return 0;

    /* A dropped-sample report means a stream discontinuity: block
     * boundaries after this point no longer line up with wall-clock
     * sample counts, which invalidates downstream SoA indexing.  Keep
     * an account and warn the operator once — silence here would let a
     * TDOA deployment record subtly wrong data for hours. */
    if (transfer->dropped_samples > 0) {
        atomic_fetch_add(&state->dropped_samples,
                         (unsigned long long)transfer->dropped_samples);
        if (!atomic_exchange(&state->drop_warned, 1)) {
            fprintf(stderr,
                    "airspy: dropped samples detected (stream "
                    "discontinuity); SoA/block indices are unreliable "
                    "from this point\n");
        }
    }

    /* int16 I/Q as produced by libairspy: ADC full scale is about
     * +/-16384 (see rawconv.c).  Stored unmodified. */
    int16_t *src = (int16_t *)transfer->samples;
    size_t   n   = (size_t)(transfer->sample_count) * 2; /* I and Q */

    /* Record when these samples arrived before queueing them, so the
     * reader can timestamp blocks at reception rather than at the
     * (possibly much later) moment it dequeues them. */
    struct timeval arrived;
    gettimeofday(&arrived, NULL);
    state->produced_pairs += (uint64_t)transfer->sample_count;
    stamp_queue_push(&state->stamps, state->produced_pairs, &arrived);

    if (!circbuf_put(&state->circbuf, (char *)src, n * sizeof(int16_t))) {
        /* A put fails only when the ring was cancelled (shutdown, when
         * running is already 0) or when the transfer can never fit.
         * The latter is fatal: every later transfer would be dropped too
         * and the consumer would wait forever for data.  Cancel the ring
         * so the consumer's read fails, and stop streaming. */
        if (atomic_exchange(&state->running, 0)) {
            fprintf(stderr,
                    "airspy: a %zu-byte transfer does not fit the %zu-byte "
                    "ring buffer; stopping capture\n",
                    n * sizeof(int16_t), state->circbuf.size);
            circbuf_cancel(&state->circbuf);
        }
        return -1;
    }
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
 *      set to the time the block's last sample arrived from the SDR
 *      (from the transfer arrival records), not the time it was
 *      dequeued: samples can wait in the ring for up to a second, and
 *      cross-receiver matching compares these timestamps.
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
    unsigned stalled_ms = 0;
    for (;;) {
        circbuf_status_t got = circbuf_get_timeout(
            &state->circbuf, (char *)(dst + history_size * 2), needed,
            AIRSPY_POLL_MS);
        if (got == CIRCBUF_OK) {
            break;
        }
        if (got == CIRCBUF_CANCELLED) {
            /* A requested stop (Ctrl-C / SIGTERM) ends the stream
             * cleanly; anything else cancelling the ring is a failure. */
            return atomic_load(&state->cancelled) ? 1 : -1;
        }
        /* No samples for a while.  An unplugged Airspy (or a USB error)
         * just stops calling back, which used to leave this read waiting
         * forever: the process stayed up, recorded nothing, and nothing
         * restarted it. */
        stalled_ms += AIRSPY_POLL_MS;
        if (airspy_is_streaming(state->device) != AIRSPY_TRUE) {
            fprintf(stderr, "airspy: device stopped streaming (unplugged "
                    "or USB error)\n");
            return -1;
        }
        if (stalled_ms >= AIRSPY_STALL_LIMIT_MS) {
            fprintf(stderr, "airspy: no samples for %u s; giving up\n",
                    stalled_ms / 1000);
            return -1;
        }
    }
    state->consumed_pairs += new_len;

    /* 3. Update block metadata. */
    if (state->output != NULL) {
        state->output->index++;
        if (!stamp_queue_time_of(&state->stamps, state->consumed_pairs,
                                 &state->output->timestamp)) {
            gettimeofday(&state->output->timestamp, NULL);
        }
    }
    return 0;
}


static int _airspy_reader_next(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    return _reader_read_next(state->reader);
}

static int _airspy_reader_start(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    atomic_store(&state->running, 1);
    int ret = airspy_start_rx(state->device, _airspy_callback, state);
    if (ret != AIRSPY_SUCCESS) {
        atomic_store(&state->running, 0);
        fprintf(stderr, "airspy_start_rx() failed: %s\n",
                airspy_error_name(ret));
        return -1;
    }
    return 0;
}

static int _airspy_reader_stop(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    atomic_store(&state->running, 0);
    /* Unblock a callback thread waiting in circbuf_put() BEFORE calling
     * airspy_stop_rx(): stop_rx joins the USB consumer thread, so if the
     * callback is blocked on a full ring buffer (slow consumer) the join
     * would never return.  Cancel first, then stop. */
    circbuf_cancel(&state->circbuf);
    if (state->device) {
        airspy_stop_rx(state->device);
    }
    return 0;
}

static void _airspy_reader_cancel(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    atomic_store(&state->cancelled, 1);
    atomic_store(&state->running, 0);
    circbuf_cancel(&state->circbuf);
}

static void _airspy_reader_free(void *context)
{
    airspy_state_t *state = (airspy_state_t *)context;
    if (!state) return;
    atomic_store(&state->running, 0);
    /* Same ordering hazard as _airspy_reader_stop: airspy_stop_rx()
     * joins the USB consumer thread, which may be blocked in
     * circbuf_put() on a full ring buffer.  Cancel first. */
    circbuf_cancel(&state->circbuf);
    if (state->device) {
        airspy_stop_rx(state->device);
        airspy_close(state->device);
    }
    circbuf_destroy(&state->circbuf);
    stamp_queue_destroy(&state->stamps);
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

    /* -d <index>: select by enumeration order via the board serial.
     * Previously this option was silently ignored and the first device
     * was always opened. */
    if (config->device_index > 0) {
        uint64_t serials[32];
        int found = airspy_list_devices(serials, 32);
        if (found < 0) {
            fprintf(stderr, "airspy_list_devices() failed: %s\n",
                    airspy_error_name(found));
            free(state);
            return -1;
        }
        /* Defensive clamp: entries beyond the buffer were not written. */
        if (found > 32) {
            found = 32;
        }
        /* Compare unsigned: casting device_index to int would let a
         * value >= 2^31 wrap negative, bypass the check, and index
         * serials[] out of bounds. */
        if (config->device_index >= (uint32_t)found) {
            fprintf(stderr,
                    "airspy device index %u out of range: only %d "
                    "device(s) found\n",
                    config->device_index, found);
            free(state);
            return -1;
        }
        ret = airspy_open_sn(&state->device,
                             serials[config->device_index]);
        if (ret != AIRSPY_SUCCESS) {
            fprintf(stderr, "airspy_open_sn() failed: %s\n",
                    airspy_error_name(ret));
            free(state);
            return -1;
        }
    } else {
        ret = airspy_open(&state->device);
        if (ret != AIRSPY_SUCCESS) {
            fprintf(stderr, "airspy_open() failed: %s\n",
                    airspy_error_name(ret));
            free(state);
            return -1;
        }
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

    size_t circbuf_size = _ring_size(config->sample_rate);
    if (!circbuf_init(&state->circbuf, circbuf_size)) {
        fprintf(stderr, "circbuf_init failed: could not allocate %zu bytes\n",
                circbuf_size);
        airspy_close(state->device);
        free(state);
        return -1;
    }
    if (!stamp_queue_init(&state->stamps, AIRSPY_STAMP_CAPACITY,
                          config->sample_rate)) {
        fprintf(stderr, "stamp_queue_init failed\n");
        circbuf_destroy(&state->circbuf);
        airspy_close(state->device);
        free(state);
        return -1;
    }

    /* The device is configured but not streaming yet: reader->start
     * (called by fastcard_start) begins RX after FFT planning and after
     * the CLI has installed its signal handlers, so no samples pile up
     * or get dropped while the process is still initialising. */
    state->reader          = reader;
    reader->context        = state;
    reader->read_next  = _reader_read_next;
    reader->sample_format = SAMPLE_FORMAT_INT16;
    reader->next = (reader_func_t)_airspy_reader_next;
    reader->start = (reader_func_t)_airspy_reader_start;
    reader->stop = (reader_func_t)_airspy_reader_stop;
    reader->cancel = (reader_func_void_t)_airspy_reader_cancel;
    reader->free = (reader_func_void_t)_airspy_reader_free;

    return 0;

err:
    fprintf(stderr, "airspy device configuration failed: %s\n",
            airspy_error_name(ret));
    airspy_close(state->device);
    free(state);
    return -1;
}


/* Note: there is deliberately no separate airspy_reader_close() —
 * teardown goes through the reader_t vtable (reader->free ==
 * _airspy_reader_free).  A standalone close combined with reader_free
 * would double-free the state. */

void airspy_reader_print_stats(reader_t *reader, FILE *out)
{
    if (!reader || !reader->context || !out) return;
    airspy_state_t *state = (airspy_state_t *)reader->context;

    unsigned long long dropped = atomic_load(&state->dropped_samples);
    unsigned overflows = circbuf_overflows(&state->circbuf);
    fprintf(out,
            "airspy reader: dropped samples (libairspy): %llu; "
            "ring-buffer overflow events: %u\n",
            dropped, overflows);
    if (dropped > 0 || overflows > 0) {
        fprintf(out,
                "WARNING: the sample stream had discontinuities; "
                "block/SoA indices after the first drop are "
                "unreliable for TDOA use\n");
    }

    unsigned *histogram = circbuf_histogram(&state->circbuf);
    if (histogram != NULL) {
        fprintf(out, "ring-buffer occupancy histogram:");
        for (int i = 0; i < CIRCBUF_HISTOGRAM_LEN; ++i) {
            fprintf(out, " %u", histogram[i]);
        }
        fprintf(out, "\n");
    }
}
