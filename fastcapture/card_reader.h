// Read data from a .card file

#ifndef CARD_READER_H
#define CARD_READER_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include <stdio.h>

#include "reader.h"

reader_t * card_reader_new(reader_settings_t settings,
                           FILE* file);

/// Read the card's leading header (its #v2 line, and the comment lines
/// after it until the block history is known) and check it against the
/// settings.  Returns 0, or, after printing why the card cannot be
/// replayed with them:
///   -7  the header records another block size or history;
///   -8  nothing records the block history (a card thriftyx capture
///       wrote before it recorded one, or no header at all) and the
///       settings do not give one (history_size_set).
/// *sample_rate (when not NULL) is the rate the #v2 line records, 0
/// when it records none.  Call before the first reader_next(), which
/// otherwise reads the header itself; a later call returns the same.
int card_reader_read_header(reader_t* reader, uint32_t* sample_rate);

#ifdef __cplusplus
}
#endif

#endif /* CARD_READER_H */
