// Char FIFO circular buffer
//
// A circular buffer for which the producer and consumer can operate on
// separate pthread threads.

#ifndef CIRCBUF_H
#define CIRCBUF_H

#include <pthread.h>
#include <stdbool.h>

#define CIRCBUF_HISTOGRAM_LEN 20

typedef struct {
    char *buf;                      // the buffer
    size_t size;                    // size of buffer
    size_t len;                     // number of bytes occupied in the buffer
    size_t head;                    // position of producer
    size_t tail;                    // position of consumer
    bool cancel;                    // cancel get and put operations
    unsigned *histogram;            // record buffer occupancy
    unsigned num_overflows;         // number of overflow events
    pthread_mutex_t mutex;          // protect circbuf_t data
    pthread_cond_t can_produce;     // signaled when items have been removed
    pthread_cond_t can_consume;     // signaled when items have been added
} circbuf_t;

/// Create a new circular buffer.
/// Returns NULL if an error occurred.
circbuf_t* circbuf_new(size_t size);

/// Allocate memory for a circular buffer.
/// Returns true on success.
bool circbuf_init(circbuf_t* circbuf, size_t size);

/// Release internal resources of a circular buffer (for embedded instances).
void circbuf_destroy(circbuf_t* circbuf);

/// Deallocate internal resources AND free the circbuf_t struct itself.
/// Only use for heap-allocated circbufs created with circbuf_new().
void circbuf_free(circbuf_t* circbuf);

/// Read exactly "len" bytes from the circular buffer.
/// Will wait for producer if enough data is not available.
bool circbuf_get(circbuf_t* circbuf, char* dest, size_t len);

typedef enum {
    CIRCBUF_OK = 0,
    CIRCBUF_CANCELLED,   // cancelled, or len > size (can never be read)
    CIRCBUF_TIMEOUT,     // not enough data arrived in time; nothing read
} circbuf_status_t;

/// Like circbuf_get, but give up after "timeout_ms" milliseconds without
/// enough data (0 waits forever).  On CIRCBUF_TIMEOUT nothing has been
/// consumed, so the call can simply be repeated.
circbuf_status_t circbuf_get_timeout(circbuf_t* circbuf, char* dest,
                                     size_t len, unsigned timeout_ms);

/// Write exactly "len" bytes to the circular buffer.
/// If the data does not fit in the free space, increases the overflow
/// counter and waits for the consumer.  A write may fill the buffer
/// completely.  Returns false when len > size (the write can never fit)
/// or when the buffer was cancelled.
bool circbuf_put(circbuf_t* circbuf, char* src, size_t len);

/// Return the number of overflow events that occurred.
unsigned circbuf_overflows(circbuf_t* circbuf);

/// Return the occupancy histogram
unsigned* circbuf_histogram(circbuf_t* circbuf);

/// Cancel get waiting for more data and put waiting for data to be consumed.
void circbuf_cancel(circbuf_t* circbuf);

#endif /* CIRCBUF_H */
