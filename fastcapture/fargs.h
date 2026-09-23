// Parse Fastcard (CLI) arguments ("fargs") using argp

#ifndef FARGS_H
#define FARGS_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>

#include "fargs_type.h"

#define FARGS_OK
#define FARGS_UNKNOWN -1
#define FARGS_INVALID_VALUE -2

// like argp_option
typedef struct {
    const char* name;
    int key;
    const char* arg;
    int flags;
    const char* doc;
    int group;
} fargs_option_t;

extern const fargs_option_t fargs_options[];
#define FARGS_NUM_OPTIONS 22

fargs_t* fargs_new();
int fargs_parse_opt(fargs_t *fargs,
                    int key,
                    char *arg);

/// Complete the arguments after parsing: derive block_len/history_len
/// from the sample rate when they were not given (the correlation needs
/// history >= template length, which grows with the rate), and check
/// that the geometry is consistent.  Prints the problem and returns
/// FARGS_INVALID_VALUE when it is not; 0 otherwise.
int fargs_finalize(fargs_t *fargs);

// TODO: move print functions to another module
void fargs_print_summary(fargs_t *fa, FILE* out, bool sdr);
void fargs_print_card_header(fargs_t *fa,
                             FILE* out,
                             bool sdr,
                             const char* tool);

#ifdef __cplusplus
}
#endif

#endif /* FARGS_H */
