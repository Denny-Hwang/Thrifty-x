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
    int sdr_gain;           /* LNA gain index (0-14 Mini, 0-15 R2) */
    uint8_t sdr_mixer_gain; /* Mixer gain index (0-15) */
    uint8_t sdr_vga_gain;   /* VGA/IF gain index (0-15) */
    uint8_t sdr_bias_tee;   /* Bias tee enable (0/1) */
    uint32_t sdr_dev_index;

    bool silent;
} fargs_t;

#ifdef __cplusplus
}
#endif

#endif /* FARGS_TYPE_H */
