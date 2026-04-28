#include <stdlib.h>

#include "reader.h"

int reader_next(reader_t* reader) {
    if (reader->next) {
        return reader->next(reader->context);
    }
    return 0;
}

int reader_start(reader_t* reader) {
    if (reader->start) {
        return reader->start(reader->context);
    }
    return 0;
}

int reader_stop(reader_t* reader) {
    if (reader->stop) {
        return reader->stop(reader->context);
    }
    return 0;
}

void reader_cancel(reader_t* reader) {
    if (reader->cancel) {
        reader->cancel(reader->context);
    }
}

void reader_free(reader_t* reader) {
    if (reader == NULL) {
        return;
    }
    if (reader->free) {
        reader->free(reader->context);
    }
    free(reader);
}

block_t * reader_block_new(size_t len) {
    block_t *block;
    block = malloc(sizeof(block_t));
    if (block == NULL) {
        return NULL;
    }
    // Allocate a few extra bytes in case a reader terminates the string
    // or base64 decoding writes extra bytes.
    block->raw_samples = malloc(len * 2 * sizeof(int16_t) + 5);
    if (block->raw_samples == NULL) {
        free(block);
        return NULL;
    }

    // Initial values: zero out the *entire* buffer (I and Q for every
    // sample, len * 2 int16 values) so the first block read does not
    // expose uninitialised memory through the history-overlap region.
    block->index = -1;
    block->timestamp.tv_sec = 0;
    block->timestamp.tv_usec = 0;
    for (size_t i = 0; i < len * 2; ++i) {
        block->raw_samples[i] = 0;
    }

    return block;
}

void reader_block_free(block_t *block) {
    if (block == NULL) {
        return;
    }
    free(block->raw_samples);
    free(block);
}
