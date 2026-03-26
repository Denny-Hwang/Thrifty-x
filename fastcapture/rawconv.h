/*
 * fastcapture/rawconv.h
 *
 * Convert raw int16 I/Q samples from an Airspy SDR to an array of complex
 * values.
 *
 * Changed from fastcard/rawconv.h:
 *   - Input parameter type: uint16_t* -> int16_t*
 *   - RAWCONV_ZERO removed (no DC offset for Airspy)
 *   - LUT field removed from rawconv_t (not applicable for int16 direct conv)
 */

#ifndef RAWCONV_H
#define RAWCONV_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include <stdlib.h>

#include "fft.h"  /* for fcomplex */

typedef struct {
    /* No LUT needed for int16 direct conversion */
    int _placeholder;
} rawconv_t;

void rawconv_init(rawconv_t *rawconv);
void rawconv_to_complex(rawconv_t *rawconv,
                        fcomplex* output,
                        int16_t* input,
                        size_t len);

#ifdef __cplusplus
}
#endif

#endif /* RAWCONV_H */
