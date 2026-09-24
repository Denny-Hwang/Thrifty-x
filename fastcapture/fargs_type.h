#ifndef FARGS_TYPE_H
#define FARGS_TYPE_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    size_t block_len;
    size_t history_len;
    bool block_len_set;     /* given with -b (else derived from the rate) */
    bool history_len_set;   /* given with -h (else derived from the rate) */
    
    float threshold_const;
    float threshold_snr;
    int carrier_freq_min;
    int carrier_freq_max;
    unsigned skip;
    
    const char *input_file;
    const char *wisdom_file;
    bool input_card;

    uint32_t sdr_freq;
    uint32_t sdr_sample_rate;
    int sdr_gain;         /* LNA gain index (0-14; R820T2 on both models) */
    uint8_t sdr_mixer_gain; /* Mixer gain index (0-15) */
    uint8_t sdr_vga_gain;   /* VGA/IF gain index (0-15) */
    uint8_t sdr_bias_tee;   /* Bias tee enable (0/1) */
    uint32_t sdr_dev_index;

    bool silent;

    /* sdr_sample_rate is known, not the default: given with -s, or
     * recorded by the input card (fastcard_new).  Card headers record
     * it.  Last, so the fields before it keep their offsets. */
    bool sdr_sample_rate_set;
} fargs_t;

#ifdef __cplusplus
}
#endif

#endif /* FARGS_TYPE_H */
