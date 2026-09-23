/**
 * FastCapture: Fast Carrier Detection for Airspy SDR
 *
 * Features:
 *  - fast IO and raw-to-complex conversion (int16 I/Q from Airspy)
 *  - fast fft (fftw)
 *  - volk for abs
 *  - fast base64 encoding (with libb64)
 *
 *  Dependencies:
 *   - fftw3f
 *   - libvolk
 *   - libairspy
 **/

#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <string.h>

#include "configuration.h"
#include "lib/base64.h"

#include "fastcard.h"
#include "fargs.h"
#include "sigthread.h"

#include <argp.h>  // this should be last

const char *argp_program_version = "fastcapture " VERSION_STRING;
static const char doc[] = "FastCapture: Fast Carrier Detection for Airspy\n\n"
    "Takes a stream of 12-bit signed int16 I/Q samples from an Airspy SDR, "
    "splits it into fixed-sized blocks, and, if a carrier is detected in a "
    "block, outputs the block ID, timestamp and the block's raw samples "
    "encoded in base64.";

fargs_t* args;
char* output_file = NULL;

/* The capture the signal thread stops.  The lock keeps it from calling
 * fastcard_cancel on a capture main() is freeing. */
static pthread_mutex_t fastcard_lock = PTHREAD_MUTEX_INITIALIZER;
static fastcard_t* fastcard = NULL;
static bool stop_requested = false;

static error_t parse_opt (int key, char *arg, struct argp_state *state) {
    if (key == 'o') {
        output_file = arg;
        return 0;
    } else if (key == ARGP_KEY_ARG) {
        // We don't take any arguments
        argp_usage(state);
    }

    int result = fargs_parse_opt(args, key, arg);
    if (result == FARGS_UNKNOWN) {
        return ARGP_ERR_UNKNOWN;
    } else if (result == FARGS_INVALID_VALUE) {
        argp_usage(state);
    }

    return 0;
}

/* Runs on the signal thread (sigthread.h), not in a signal handler. */
static void on_stop_signal(int signo, void* ctx) {
    (void)signo;
    (void)ctx;
    pthread_mutex_lock(&fastcard_lock);
    stop_requested = true;
    if (fastcard != NULL) {
        fastcard_cancel(fastcard);
    }
    pthread_mutex_unlock(&fastcard_lock);
}

/* A failed write (full disk, or a closed pipe now that SIGPIPE is
 * ignored) ends the capture instead of silently dropping card lines. */
static bool stream_failed(FILE* stream, const char* what) {
    if (stream == NULL || !ferror(stream)) {
        return false;
    }
    fprintf(stderr, "Failed to write %s: %s\n", what, strerror(errno));
    return true;
}

static struct argp_option extra_options[] = {
    {"output", 'o', "<FILE>", 0,
        "Output card file ('-' for stdout)\n[default: no output]", 1}
};
#define NUM_EXTRA_OPTIONS 1


int main(int argc, char **argv) {
    struct argp_option options[FARGS_NUM_OPTIONS + NUM_EXTRA_OPTIONS];
    memcpy(options,
           extra_options,
           sizeof(struct argp_option)*NUM_EXTRA_OPTIONS);
    memcpy(options + NUM_EXTRA_OPTIONS,
           fargs_options,
           sizeof(struct argp_option)*FARGS_NUM_OPTIONS);
    struct argp argp = {options, parse_opt, NULL,
                        doc, NULL, NULL, NULL};

    //// Set the stage
    args = fargs_new();
    argp_parse(&argp, argc, argv, 0, 0, 0);
    if (fargs_finalize(args) != 0) {
        return 64;  /* EX_USAGE, as argp exits for a bad option */
    }

    // Before any library creates a thread, so all of them inherit the
    // blocked signal mask.
    if (sigthread_start(on_stop_signal, NULL) != 0) {
        perror("Failed to set up signal handling");
        return -1;
    }

    // variables
    FILE *out = NULL;
    FILE *info = NULL;
    char *base64 = NULL;
    fastcard_t *fc = NULL;
    int exit_code = 0;

    // open streams
    if (output_file != NULL) {
        if (strlen(output_file) == 0 || strcmp(output_file, "-") == 0) {
            out = stdout;
        } else {
            out = fopen(output_file, "w");
            if (out == NULL) {
                perror("Failed to open output file");
                return -1;
            }
        }
    }

    if (args->silent) {
        info = NULL;
    } else if (out == stdout) {
        info = stderr;
    } else {
        info = stdout;
    }

    // init stuff
    fc = fastcard_new(args);
    if (fc == NULL) {
        exit_code = -1;
        goto free;
    }
    pthread_mutex_lock(&fastcard_lock);
    fastcard = fc;
    bool stopped_early = stop_requested;
    pthread_mutex_unlock(&fastcard_lock);
    if (stopped_early) {
        goto free;
    }

    // Airspy: block_len I/Q pairs * 2 int16 values * 2 bytes = block_len * 4 bytes
    base64 = (char*) malloc((4*args->block_len+2)/3*4 + 1);
    if (base64 == NULL) {
        exit_code = -1;
        goto free;
    }

    bool sdr_input = false;
    if (args->input_file) {
        sdr_input = (strcmp(args->input_file, "airspy") == 0);
    }
    if (info != NULL) {
        fargs_print_summary(args, info, sdr_input);
        fflush(info);
    }
    /* Also on stdout ('-o -'): a piped card without the '#v2' header
     * would be silently decoded as 8-bit by the Python card_reader.
     * Header lines are '#' comments, so they are safe on a pipe. */
    if (out != NULL) {
        fargs_print_card_header(args, out, sdr_input, argp_program_version);
    }

    //// Start!
    exit_code = fastcard_start(fc);
    if (exit_code != 0) goto free;

    if (args->skip > 0 && info != NULL) {
        fprintf(info, "\nSkipping %u block(s)... ", args->skip);
        fflush(info);
    }
    unsigned skip = args->skip;
    unsigned cnt = 0;

    const fastcard_data_t* data;
    int ret = 0;
    while (true) {
        ret = fastcard_process_next(fc, &data);
        if (ret != 0) {
            break;
        }

        if (skip > 0) {
            --skip;
            if (skip == 0 && info != NULL) {
                fprintf(info, "done\n\n");
                fflush(info);
            }
            continue;
        }

        if (data->detected) {
            const block_t* block = data->block;
            const cardet_detection_t* det = &data->detection;
            if (info != NULL) {
                fprintf(info,
                        "block #%" PRId64 ": mag[%u] = %.1f "
                        "(thresh = %.1f, noise = %.1f)\n",
                         data->block->index,
                         det->argmax, sqrt(det->max),
                         sqrt(det->threshold), sqrt(det->noise));
            }

            if (out != NULL) {
                // Airspy: block_len I/Q pairs * 2 samples * sizeof(int16_t)
                Base64encode(base64,
                             (const char*) block->raw_samples,
                             args->block_len * 2 * sizeof(int16_t));
                fprintf(out,
                        "%ld.%06ld %" PRId64" %s\n",
                        block->timestamp.tv_sec,
                        block->timestamp.tv_usec,
                        block->index,
                        base64);
            }
            if (stream_failed(out, "card output")
                    || stream_failed(info, "status output")) {
                exit_code = -1;
                break;
            }
        }
        ++cnt;
    }

    if (info != NULL) {
        fprintf(info, "\nRead %u blocks.\n", cnt);
        fastcard_print_stats(fc, info);
    }

    if (exit_code == 0 && ret != 1) {
        // reader didn't stop gracefully
        exit_code = ret;
    }


    //// Free stuff
free:
    pthread_mutex_lock(&fastcard_lock);
    fc = fastcard;
    fastcard = NULL;
    pthread_mutex_unlock(&fastcard_lock);
    if (fc) {
        fastcard_free(fc);
    }
    if (base64) {
        free(base64);
    }
    if (info != NULL) {
        fflush(info);
    }
    if (out != NULL) {
        fflush(out);
    }
    if (out != NULL && out != stdout) {
        fclose(out);
    }
    if (args != NULL) {
        free(args);
    }

    return exit_code;
}
