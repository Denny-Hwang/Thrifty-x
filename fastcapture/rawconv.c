/*
 * fastcapture/rawconv.c
 *
 * Convert raw int16 I/Q samples from an Airspy SDR to an array of complex
 * values.
 *
 * Changed from fastcard/rawconv.c:
 *   - Input type: uint8_t (RTL-SDR, 8-bit unsigned) -> int16_t (Airspy,
 *     12-bit signed stored in 16-bit)
 *   - Conversion formula: (val - 127.4) / 128.0  ->  val / 2048.0
 *   - No DC-offset subtraction needed (Airspy hardware has none).
 *   - LUT removed: the 65536-entry uint16 LUT does not apply to signed int16.
 *     Direct per-sample conversion is used instead.
 */

#include <stdint.h>
#include <stdlib.h>
#include <complex.h>

#include "rawconv.h"

void rawconv_init(rawconv_t *rawconv) {
    /* Nothing to initialize for int16 direct conversion. */
    (void)rawconv;
}

void rawconv_to_complex(rawconv_t *rawconv,
                        fcomplex* output,
                        int16_t* input,
                        size_t len) {
    (void)rawconv;
    /*
     * Airspy samples are 12-bit signed values stored in the lower 12 bits of
     * each int16.  Normalize to [-1.0, +1.0] by dividing by 2048.0 (2^11).
     * out[i] = input[2*i] / 2048.0f + I * (input[2*i+1] / 2048.0f);
     */
    for (size_t i = 0; i < len; ++i) {
        output[i] = input[2*i] / 2048.0f + I * (input[2*i+1] / 2048.0f);
    }
}
