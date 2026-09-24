// A quick-n-dirty proof-of-concept fast C++ implementation of Thrifty detect.
// This is a mess. This should be refactored.

#include <iostream>
#include <cmath>
#include <mutex>
#include <string>
#include <vector>
#include <stdexcept>
#include <memory>

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <argp.h>

#include <parse.h>
#include <base64.h>
#include <sigthread.h>

#include "corr_detector.h"
#include "configuration.h"

using namespace std;


//// CLI stuff
// TODO: use proper command-line parser (e.g. tclap)

const char *argp_program_version = "fastdet " VERSION_STRING;
static const char doc[] = "FastDet: Fast Detector\n\n"
    "Like Thrifty, but faster.";

#define NUM_EXTRA_OPTIONS 6
static struct argp_option extra_options[] = {
    {"output", 'o', "<FILE>", 0,
        "Output card file\n('-' for stdout)\n[default: no output]", 1},
    {"card-output", 'x', "<FILE>", 0,
     "Write block to card file on detect\n('-' for stdout)\n[default: no output]", 1},

    // Correlator
    {0, 0, 0, 0, "Correlator settings:", 5},
    {"corr-threshold", 'u', "<constant>c<snr>s", 0,
        "Correlation detection theshold\n[default: 15s]", 5},
    {"template", 'z', "<FILE>", 0,
        "Load template from a .tpl file\n[default: template.tpl]", 5},
    {"rxid", 'r', "<int>", 0,
        "This receiver's unique identifier\n[default: -1]", 5}
};

unique_ptr<fargs_t, decltype(free)*> args = {NULL, free};
std::string output_file;
std::string card_output_file;
std::string template_file = "template.tpl";
float arg_corr_thresh_const = 0;
float arg_corr_thresh_snr = 15;
int rxid = -1;

static error_t parse_opt (int key, char *arg, struct argp_state *state) {
    if (key == 'o') {
        output_file = arg;
    } else if (key == 'x') {
        card_output_file = arg;
    } else if (key == 'u') {
        if (!parse_theshold_str(arg,
                                &arg_corr_thresh_const,
                                &arg_corr_thresh_snr)) {
            argp_usage(state);
        }
    } else if (key == 'z') {
        template_file = arg;
    } else if (key == 'r') {
        rxid = atoi(arg);
    } else if (key == ARGP_KEY_ARG) {
        // We don't take any arguments
        argp_usage(state);
    } else {
        int result = fargs_parse_opt(args.get(), key, arg);
        if (result == FARGS_UNKNOWN) {
            return ARGP_ERR_UNKNOWN;
        } else if (result == FARGS_INVALID_VALUE) {
            argp_usage(state);
        }
    }

    return 0;
}

// The detector the signal thread stops.  The lock keeps it from calling
// cancel() on a detector main() is destroying.
static std::mutex carrier_det_lock;
static std::unique_ptr<CarrierDetector> carrier_det;
static bool stop_requested = false;

// Whether a template of `samples` samples is a whole code (2^n - 1
// chips, n = 5 ... 11, at the Thrifty chip rate) at `sample_rate`.
static bool template_fits_rate(size_t samples, double sample_rate) {
    double chips = samples * 999707.0 / sample_rate;
    for (int bits = 5; bits <= 11; ++bits) {
        if (fabs(chips / ((1 << bits) - 1) - 1) < 0.01) {
            return true;
        }
    }
    return false;
}

// Runs on the signal thread (sigthread.h), not in a signal handler.
static void on_stop_signal(int signo, void* ctx) {
    (void)signo;
    (void)ctx;
    std::lock_guard<std::mutex> lock(carrier_det_lock);
    stop_requested = true;
    if (carrier_det) {
        carrier_det->cancel();
    }
}

static void release_carrier_det() {
    std::unique_ptr<CarrierDetector> det;
    {
        std::lock_guard<std::mutex> lock(carrier_det_lock);
        det = std::move(carrier_det);
    }
    // det (and the capture it owns) is destroyed here, outside the lock.
}

// A failed write (full disk, or a closed pipe now that SIGPIPE is
// ignored) ends the run instead of silently dropping detections.
static void check_written(CFile& file, const char* what) {
    if (file.failed()) {
        throw std::runtime_error(std::string("Failed to write ") + what
                                 + ": " + strerror(errno));
    }
}


int main(int argc, char **argv) {
    // Argument parsing mess
    struct argp_option options[FARGS_NUM_OPTIONS + NUM_EXTRA_OPTIONS];
    memcpy(options,
           extra_options,
           sizeof(struct argp_option)*NUM_EXTRA_OPTIONS);
    memcpy(options + NUM_EXTRA_OPTIONS,
           fargs_options,
           sizeof(struct argp_option)*FARGS_NUM_OPTIONS);
    struct argp argp = {options, parse_opt, NULL,
                        doc, NULL, NULL, NULL};

    args.reset(fargs_new());
    argp_parse(&argp, argc, argv, 0, 0, 0);
    if (fargs_finalize(args.get()) != 0) {
        return 64;  // EX_USAGE, as argp exits for a bad option
    }
    if (output_file == "-" && card_output_file == "-") {
        // Interleaved .toad and card lines: neither reader can use them.
        cerr << "fastdet: -o and -x cannot both write to stdout ('-')"
             << endl;
        return 64;
    }

    // Before any library creates a thread, so all of them inherit the
    // blocked signal mask.
    if (sigthread_start(on_stop_signal, NULL) != 0) {
        perror("Failed to set up signal handling");
        return -1;
    }

    int exit_code = 0;
    try {
        CFile out(output_file);
        CFile card(card_output_file);
        CFile info;
        if (!args->silent) {
            // Status lines mixed into a .toad ('-o -') or a card
            // ('-x -') on stdout make it unreadable, as fastcapture
            // knows (fastcard_cli.c).
            info.open((out.file() == stdout || card.file() == stdout)
                      ? stderr : stdout);
        }

        vector<float> template_samples = load_template(template_file);
        bool live = args->input_file
            && strcmp(args->input_file, "airspy") == 0;
        if (live && !template_fits_rate(template_samples.size(),
                                        args->sdr_sample_rate)) {
            cerr << "warning: template '" << template_file << "' has "
                 << template_samples.size() << " samples, which is no "
                 << "2^n-1-chip code at " << args->sdr_sample_rate / 1e6
                 << " Msps: it was made for another sample rate, so "
                 << "nothing will correlate (thriftyx template_generate "
                 << "--sample-rate ...)" << endl;
        }
        if (template_samples.size() > args->block_len) {
            throw std::runtime_error(
                "template '" + template_file + "' has "
                + std::to_string(template_samples.size())
                + " samples, more than the block length "
                + std::to_string(args->block_len)
                + "; it was made for a higher sample rate");
        }
        {
            std::unique_ptr<CarrierDetector> det(
                new CarrierDetector(args.get()));
            std::lock_guard<std::mutex> lock(carrier_det_lock);
            carrier_det = std::move(det);
            if (stop_requested) {
                carrier_det->cancel();
            }
        }
        CorrDetector corr_detect(template_samples,
                                 args->block_len,
                                 args->history_len,
                                 arg_corr_thresh_const,
                                 arg_corr_thresh_snr);

        // v2 card format: block_len int16 I/Q pairs = 4 bytes per pair
        vector<char> base64((2*args->block_len*sizeof(int16_t)+2)/3*4 + 10);

        // print header
        bool input_from_sdr = false;
        if (args->input_file) {
            // fastcapture's live-SDR sentinel (fastcard.c) — the legacy
            // "rtlsdr" sentinel no longer exists; matching it here left
            // card headers with sample_rate=0 on every live capture.
            input_from_sdr = (strcmp(args->input_file, "airspy") == 0);
        }
        if (info.file() != NULL) {
            fargs_print_summary(args.get(), info.file(), input_from_sdr);
            info.printf("receiver id: %d\n", rxid);
            info.printf("corr threshold: constant = %g; snr = %g\n",
                       arg_corr_thresh_const, arg_corr_thresh_snr);
            info.printf("template: %s\n\n", template_file.c_str());
            info.flush();
        }

        // Also on stdout ('-x -'): a piped card without the '#v2'
        // header would be silently decoded as 8-bit by the Python
        // card_reader.  Header lines are '#' comments, so they are
        // safe to interleave on a pipe.
        if (card.file() != NULL) {
            fargs_print_card_header(args.get(), card.file(),
                                    input_from_sdr, argp_program_version);
        }

        // Start detection!
        unsigned skip = args->skip;
        unsigned cnt = 0;
        if (skip > 0) {
            info.printf("\nSkipping %u block(s)... ", args->skip);
            info.flush();
        }

        carrier_det->start();

        while (carrier_det->process_next()) {
            if (skip > 0) {
                --skip;
                if (skip == 0) {
                    info.printf("done\n\n");
                    info.flush();
                }
                continue;
            }
            ++cnt;

            const fastcard_data_t& carrier = carrier_det->data();
            if (!carrier.detected) {
                continue;
            }

            // TODO: Don't block, but use a producer / consumer queue to
            // perform correlation detection async

            const CorrDetection corr = corr_detect.detect(carrier);

            int64_t block_idx = carrier.block->index;
            double soa = ((args->block_len - args->history_len) *
                          block_idx + corr.peak_idx) + corr.peak_offset;

            if (corr.detected) {
                // output toad
                if (out.file() != NULL) {
                    out.printf("%d %ld.%06ld %" PRId64 " %.8f"
                               " %u %.12f %f %f %u %f %f %f\n",
                               rxid,
                               carrier.block->timestamp.tv_sec,
                               carrier.block->timestamp.tv_usec,
                               carrier.block->index,
                               soa,
                               corr.peak_idx,
                               corr.peak_offset,
                               sqrt(corr.peak_power),
                               sqrt(corr.noise_power),
                               carrier.detection.argmax,
                               corr.carrier_offset,
                               sqrt(carrier.detection.max),
                               sqrt(carrier.detection.noise)
                               );
                    out.flush();
                }

                if (card.file() != NULL) {
                    Base64encode(base64.data(),
                                 (const char*) carrier.block->raw_samples,
                                 args->block_len * 2 * sizeof(int16_t));
                    card.printf("%ld.%06ld %" PRId64 " %s\n",
                                carrier.block->timestamp.tv_sec,
                                carrier.block->timestamp.tv_usec,
                                carrier.block->index,
                                base64.data());
                }
            }

            if (info.file() != NULL) {
                // The noise estimate is clamped at 0 for very strong
                // carriers (cardet.c); display a saturated 99 dB
                // instead of the mathematically-correct but noisy
                // looking "inf dB".
                float carrier_snr_db =
                    (carrier.detection.noise > 0)
                        ? 10 * log10(carrier.detection.max /
                                     carrier.detection.noise)
                        : 99.0f;

                info.printf("block #%" PRId64 ": carrier @ %3u %+.1f = "
                            "%4.0f / %2.0f [>%2.0f] = %2.0f dB",
                            block_idx,
                            carrier.detection.argmax,
                            corr.carrier_offset,
                            sqrt(carrier.detection.max),
                            sqrt(carrier.detection.noise),
                            sqrt(carrier.detection.threshold),
                            carrier_snr_db);

                if (corr.detected) {
                    float corr_snr_db = 10 * log10(corr.peak_power /
                                                   corr.noise_power);
                    info.printf("; corr = %4.0f / %2.0f [>%2.0f] = %2.0f dB",
                                sqrt(corr.peak_power),
                                sqrt(corr.noise_power),
                                sqrt(corr.threshold),
                                corr_snr_db);
                }

                info.printf("\n");
            }

            check_written(out, "the .toad output");
            check_written(card, "the card output");
            check_written(info, "status output");
        }

        if (info.file() != NULL) {
            info.printf("\nRead %d blocks.\n", cnt);
            carrier_det->print_stats(info.file());
        }

    } catch (FastcardException& e) {
        cerr << e.what() << endl;
        exit_code = e.getCode();
    } catch (std::exception& e) {
        cerr << e.what() << endl;
        exit_code = -1;
    }

    release_carrier_det();
    return exit_code;
}
