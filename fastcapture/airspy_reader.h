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
 * @file airspy_reader.h
 * @brief Airspy SDR reader for fastcapture.
 *
 * Replaces rtlsdr_reader.h (librtlsdr/uint8) with libairspy/int16 reader.
 */

#ifndef AIRSPY_READER_H
#define AIRSPY_READER_H

#include <stdint.h>
#include "reader.h"

/**
 * Airspy reader configuration.
 */
typedef struct {
    uint32_t sample_rate;   /**< Sample rate in Hz (3000000 or 6000000) */
    uint32_t center_freq;   /**< Center frequency in Hz */
    uint8_t  lna_gain;      /**< LNA gain index (0-14) */
    uint8_t  mixer_gain;    /**< Mixer gain index (0-15) */
    uint8_t  vga_gain;      /**< VGA/IF gain index (0-15) */
    uint8_t  bias_tee;      /**< Bias tee enable (0/1) */
} airspy_reader_config_t;

/**
 * Open an Airspy device and initialize the reader.
 *
 * @param config  Device configuration.
 * @param reader  Output: initialized reader_t instance.
 * @return 0 on success, negative on error.
 */
int airspy_reader_open(const airspy_reader_config_t *config,
                       reader_t *reader);

/**
 * Close the Airspy device and free resources.
 *
 * @param reader  Reader instance to close.
 */
void airspy_reader_close(reader_t *reader);

#endif /* AIRSPY_READER_H */
