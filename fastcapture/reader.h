// Common interface for all readers
//
// Changed from fastcard/reader.h:
//   - raw_samples type: uint16_t* -> int16_t* (Airspy 12-bit signed)
//   - Added sample_format_t enum (SAMPLE_FORMAT_INT16 / SAMPLE_FORMAT_UINT8)
//   - Added sample_format field to reader_t
//   - Added block_size, raw_samples, ctx, read_next fields to reader_t to
//     support the new airspy_reader interface

#ifndef READER_H
#define READER_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include <stddef.h>
#include <sys/time.h>

/**
 * Sample format used by the reader.
 */
typedef enum {
    SAMPLE_FORMAT_INT16 = 0,  /* Airspy 12-bit signed */
    SAMPLE_FORMAT_UINT8 = 1,  /* RTL-SDR 8-bit unsigned (legacy) */
} sample_format_t;

typedef struct {
    struct timeval timestamp;
    int64_t index;
    int16_t *raw_samples;  /* Changed from uint16_t* to int16_t* for Airspy */
} block_t;

typedef struct {
    block_t* output;
    size_t block_size;
    size_t history_size;
} reader_settings_t;

typedef struct reader_t reader_t;
typedef int (*reader_func_t)(void* context);
typedef int (*reader_read_next_t)(reader_t* reader);
typedef void (*reader_func_void_t)(void* context);
struct reader_t {
    void* context;           /* Context pointer (used by all readers) */
    reader_func_t next;
    reader_func_t start;
    reader_func_t stop;
    reader_func_void_t cancel;
    reader_func_void_t free;
    reader_read_next_t read_next;  /* Called to fill raw_samples */
    size_t block_size;             /* Number of I/Q sample pairs per block */
    int16_t *raw_samples;          /* Output buffer for raw int16 I/Q data */
    sample_format_t sample_format; /* Format of raw_samples */
};

int reader_next(reader_t* reader);
int reader_start(reader_t* reader);
int reader_stop(reader_t* reader);
void reader_cancel(reader_t* reader);
void reader_free(reader_t* reader);

block_t * reader_block_new(size_t len);  // new "clean" block
void reader_block_free(block_t *block);

#ifdef __cplusplus
}
#endif

#endif /* READER_H */
