/*
 * fastcapture/rawconv.c
 *
 * Convert raw int16 I/Q samples from an Airspy SDR to an array of complex
 * values.
 *
 * Changed from fastcard/rawconv.c:
 *   - Input type: uint8_t (RTL-SDR, 8-bit unsigned) -> int16_t (Airspy,
 *     12-bit signed stored in 16-bit)
 *   - Conversion formula: (val - 127.4) / 128.0  ->  val / 16384.0
 *   - No DC-offset subtraction needed (Airspy hardware has none).
 *   - LUT removed: the 65536-entry uint16 LUT does not apply to signed int16.
 *     Direct per-sample conversion is used instead.
 */

#include <stdint.h>
#include <stdlib.h>

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
     * libairspy's AIRSPY_SAMPLE_INT16_IQ left-shifts each 12-bit ADC code
     * by 4 and converts the real stream to I/Q with a unity-gain
     * half-band filter, which halves a tone's amplitude: a tone of A ADC
     * codes arrives as |I + jQ| = 8 * A, so ADC full scale is 16384.
     * Dividing by 16384 maps full scale to |z| = 1, like RTL-SDR's
     * (val - 127.4) / 128.  Must match AIRSPY_INT16_FULL_SCALE in
     * thriftyx/block_data.py (a power of two, so both are bit-exact).
     */
    for (size_t i = 0; i < len; ++i) {
        output[i].real = input[2*i] / 16384.0f;
        output[i].imag = input[2*i+1] / 16384.0f;
    }
}
