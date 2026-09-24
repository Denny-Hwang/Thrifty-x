#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

#include "card_reader.h"
#include "lib/base64.h"

typedef struct {
    FILE* file;
    char* base64;
    size_t base64_len;
    reader_settings_t settings;
    bool header_read;           /* the leading header has been read */
    int header_status;          /* what reading it returned */
    bool history_known;         /* recorded by the current header, or set */
    bool rate_seen;             /* a #v2 line has been read */
    unsigned long long sample_rate;  /* the first #v2 line's; 0: none */
} card_reader_t;

/* The number after `key` in `line`.  False when the line has none. */
static bool recorded_value(const char* line, const char* key,
                           unsigned long long* value) {
    const char* field = strstr(line, key);
    if (field == NULL) {
        return false;
    }
    const char* start = field + strlen(key);
    if (*start < '0' || *start > '9') {
        return false;
    }
    *value = strtoull(start, NULL, 10);
    return true;
}

/* The block geometry a card was captured with decides how block indices
 * map to samples: SoA = (block_size - history) * index + peak.  Replaying
 * a card with other -b/-h values used to shift every SoA silently, so a
 * geometry the header records (the #v2 line, or the "# arguments" line
 * of older fastcapture/fastdet cards) must match this run's.  Returns 0
 * when `line` does not record it, 1 when it records `expected`, or -7
 * after explaining the mismatch. */
static int check_recorded(const char* line, const char* key,
                          size_t expected, const char* flag) {
    unsigned long long recorded;
    if (!recorded_value(line, key, &recorded)) {
        return 0;
    }
    if (recorded == (unsigned long long)expected) {
        return 1;
    }
    fprintf(stderr, "card_reader: the card was captured with %s%llu, but "
            "this run uses %zu; rerun with %s %llu\n",
            key + (key[0] == ' '), recorded, expected, flag, recorded);
    return -7;
}

/* The history thriftyx capture used before its cards recorded one
 * (settings._pre_header_block_params, which thriftyx detect assumes for
 * them): 4920, or twice the 1023-chip template when that did not fit. */
static size_t pre_header_history(unsigned long long sample_rate) {
    size_t template_len = (size_t)(sample_rate / 999707.0 * 1023);
    size_t history = 4920;
    if (history + 1 < template_len) {
        history = 2 * template_len;
    }
    return history;
}

/* Without the history the SoA of every block is a guess, and a card
 * re-emitted from it (fastcapture -o, fastdet -x) would record the
 * guess as fact.  Returns -8 after saying which -h to rerun with. */
static int history_unknown(const card_reader_t* state) {
    if (!state->rate_seen) {
        fprintf(stderr, "card_reader: the card has no #v2 header recording "
                "its block history; rerun with -h <the history it was "
                "captured with>\n");
    } else if (state->sample_rate == 0) {
        fprintf(stderr, "card_reader: the card's header records no block "
                "history; rerun with -h <the history it was captured "
                "with>\n");
    } else {
        size_t history = pre_header_history(state->sample_rate);
        fprintf(stderr, "card_reader: the card's header records no block "
                "history; thriftyx capture used %zu at %llu sps before its "
                "cards recorded it: rerun with -h %zu if the card is one "
                "of those, or with -h <the history it was captured with>\n",
                history, state->sample_rate, history);
    }
    return -8;
}

/* Check one comment line.  A #v2 line starts a header: whether the
 * history is known is decided anew by what it (or an "# arguments"
 * line after it) records. */
static int check_header_line(card_reader_t* state, const char* line) {
    const reader_settings_t* s = &state->settings;
    if (strncmp(line, "#v2", 3) == 0) {
        unsigned long long rate = 0;
        recorded_value(line, " sample_rate=", &rate);
        if (!state->rate_seen) {
            state->rate_seen = true;
            state->sample_rate = rate;
        } else if (rate != state->sample_rate) {
            /* Joined captures: a re-emitted card has one header. */
            fprintf(stderr, "card_reader: a header records sample_rate="
                    "%llu, but an earlier one %llu; replay the captures "
                    "separately\n", rate, state->sample_rate);
            return -7;
        }
        int ret = check_recorded(line, " block_size=", s->block_size, "-b");
        if (ret < 0) {
            return ret;
        }
        ret = check_recorded(line, " block_history=", s->history_size,
                             "-h");
        if (ret < 0) {
            return ret;
        }
        state->history_known = ret == 1 || s->history_size_set;
        return 0;
    }
    if (strncmp(line, "# arguments:", 12) == 0) {
        int ret = check_recorded(line, " block_size: ", s->block_size, "-b");
        if (ret < 0) {
            return ret;
        }
        ret = check_recorded(line, "history_size: ", s->history_size, "-h");
        if (ret < 0) {
            return ret;
        }
        if (ret == 1) {
            state->history_known = true;
        }
    }
    return 0;
}

/* Read the comment lines at the file position, checking what the
 * header lines record.  `header` (the leading header): stop once the
 * #v2 line has been read and the history is known, so a card arriving
 * on a pipe is not held up until its first block. */
static int read_comment_lines(card_reader_t* state, bool header) {
    bool v2_seen = false;
    int first;
    while ((first = fgetc(state->file)) == '#') {
        char line[1024];
        line[0] = '#';
        if (fgets(line + 1, sizeof(line) - 1, state->file) == NULL) {
            first = EOF;
            break;
        }
        size_t line_len = strlen(line);
        if (line[line_len - 1] != '\n') {      // longer than the buffer
            int rest;
            while ((rest = fgetc(state->file)) != EOF && rest != '\n') {
            }
        }
        v2_seen = v2_seen || strncmp(line, "#v2", 3) == 0;
        int ret = check_header_line(state, line);
        if (ret != 0) {
            return ret;
        }
        if (header && v2_seen && state->history_known) {
            return 0;
        }
    }
    if (first != EOF) {
        ungetc(first, state->file);
    }
    // Blocks follow (or a header without them): their SoAs need it.
    if (!state->history_known && (v2_seen || first != EOF)) {
        return history_unknown(state);
    }
    return 0;
}

int card_reader_read_header(reader_t* reader, uint32_t* sample_rate) {
    card_reader_t* state = (card_reader_t*)reader->context;
    if (!state->header_read) {
        state->header_read = true;
        state->header_status = read_comment_lines(state, true);
    }
    if (sample_rate != NULL) {
        *sample_rate = state->sample_rate <= UINT32_MAX
            ? (uint32_t)state->sample_rate : 0;
    }
    return state->header_status;
}

void card_reader_free(card_reader_t* state) {
    if (state != NULL && state->base64 != NULL) {
        free(state->base64);
    }
    free(state);  // will handle state == NULL
}

int card_reader_next(card_reader_t* state) {
    // FIXME: don't write to stderr

    if (!state->header_read) {
        state->header_read = true;
        state->header_status = read_comment_lines(state, true);
    }
    if (state->header_status != 0) {
        return state->header_status;
    }

    // copy history
    block_t* output = state->settings.output;
    size_t history_size = state->settings.history_size;

    size_t new_len = state->settings.block_size - history_size;

    // raw_samples is int16_t I/Q: one pair = 2 values (4 bytes).
    // memmove: source and destination overlap when history > new data.
    memmove(output->raw_samples,
            output->raw_samples + new_len * 2,
            history_size * 2 * sizeof(int16_t));

    // Skip comment lines, checking the geometry the header records.
    int ret_comments = read_comment_lines(state, false);
    if (ret_comments != 0) {
        return ret_comments;
    }

    // Read new data
    int read = fscanf(state->file,
                      " %ld.%ld %" PRId64 " ",
                      &output->timestamp.tv_sec,
                      &output->timestamp.tv_usec,
                      &output->index);
    if (read != 3) {
        if (feof(state->file)) {
            return 1;
        }
        fprintf(stderr, "card_reader: failed to read metadata\n");
        return -2;
    }
    char* ret = fgets(state->base64, state->base64_len + 2, state->file);
    if (ret == NULL) {
        fprintf(stderr, "card_reader: failed to read base64 data\n");
        return -3;
    }
    size_t len = strlen(ret);
    if (len < state->base64_len + 1) {
        fprintf(stderr, "card_reader: line too short\n");
        return -4;
    }
    if (ret[len-1] != '\n') {
        fprintf(stderr, "card_reader: line too long\n");
        return -5;
    }

    // fgets will terminate string
    // v2 card format: block_size I/Q pairs of int16 = 4 bytes per pair.
    // Base64decode needs headroom beyond the payload for its trailing
    // NUL; raw_samples is allocated with 5 spare bytes (reader.c).
    size_t block_bytes = 2 * state->settings.block_size * sizeof(int16_t);
    long num = Base64decode((char*)output->raw_samples, state->base64,
                            block_bytes + 4);
    if (num < 0 || (size_t)num != block_bytes) {
        fprintf(stderr, "card_reader: block length is %ld, expected %zu\n",
                num,
                block_bytes);
        return -6;
    }

    return 0;
}

reader_t * card_reader_new(reader_settings_t settings,
                          FILE* file) {
    // calloc: base64 is NULL for the fail path, nothing read yet.
    card_reader_t* state = calloc(1, sizeof(card_reader_t));
    reader_t* reader = malloc(sizeof(reader_t));
    if (state == NULL || reader == NULL) {
        goto fail;
    }
    state->settings = settings;
    state->file = file;
    state->history_known = settings.history_size_set;
    // v2 card format: base64 of block_size int16 I/Q pairs (4 bytes/pair)
    state->base64_len = (2*settings.block_size*sizeof(int16_t)+2)/3*4;
    // base64 buffer: leave space for \n and \0
    state->base64 = (char*) malloc(state->base64_len + 2);
    if (state->base64 == NULL) {
        goto fail;
    }

    reader->context = state;
    reader->next = (reader_func_t)&card_reader_next;
    reader->start = NULL;
    reader->stop = NULL;
    reader->cancel = NULL;
    reader->free = (reader_func_void_t)&card_reader_free;

    return reader;

fail:
    if (state != NULL) {
        free(state->base64);
    }
    free(state);
    free(reader);
    return NULL;
}
