#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <sys/time.h>

#include "parse.h"
#include "fargs.h"

/* Block geometry defaults follow thriftyx capture
 * (settings._auto_adjust_block_params): start from 16384 / 4920 and,
 * when the template of the longest supported code (11 bits, 2047 chips
 * at the Thrifty chip rate -- what the upstream Thrifty transmitters
 * send) does not fit that history, use the template + HISTORY_MARGIN as
 * history and the next power of two that holds template + history and
 * at least twice the history.  2.4M keeps 16384 / 4920; 2.5M 16384 /
 * 5182; 3M 16384 / 6206; 6M 32768 / 12349; 10M 65536 / 20539. */
#define DEFAULT_BLOCK_LEN           16384
#define DEFAULT_HISTORY_LEN         4920
#define CHIP_RATE                   999707.0
#define CODE_LENGTH                 2047
#define HISTORY_MARGIN              64
#define MAX_BLOCK_LEN               65536

/* Airspy sample rates (Mini 3M/6M, R2 2.5M/10M).  libairspy reads a
 * "rate" below 100 as an index into its rate table, so a typo such as
 * "-s 6" would select some rate while the card header recorded 6. */
#define MIN_SDR_SAMPLE_RATE         1000000
#define MAX_SDR_SAMPLE_RATE         10000000
/* R820T2 tuning range. */
#define MIN_SDR_FREQ                24000000.0
#define MAX_SDR_FREQ                1800000000.0
#define DEFAULT_THRESHOLD_CONST     100
#define DEFAULT_THRESHOLD_SNR       2
#define DEFAULT_CARRIER_FREQ_MIN    0
#define DEFAULT_CARRIER_FREQ_MAX    -1
#define DEFAULT_SKIP                1
#define DEFAULT_INPUT_FILE          "-"
#define DEFAULT_WISDOM_FILE         NULL

#define DEFAULT_SDR_FREQ            433830000
#define DEFAULT_SDR_SAMPLE_RATE     6000000
#define DEFAULT_SDR_GAIN            0
#define DEFAULT_SDR_INDEX           0

static char* default_input_file = DEFAULT_INPUT_FILE;
static char* default_wisdom_file = DEFAULT_WISDOM_FILE;


#define ARGP_KEY_CARD 0x01

// note: remember to update FARGS_NUM_OPTIONS
// this argp stuff is a mess
const fargs_option_t fargs_options[] = {
    // I/O
    {0, 0, 0, 0, "I/O settings:", 1},
    {"input",  'i', "<FILE>", 0,
        "Input file with samples "
        "\n('-' for stdin, 'airspy' for libairspy)\n[default: stdin]",
        1},
    {"card", ARGP_KEY_CARD, 0, 0,
        "Input is a .card file instead of binary data", 1},
    {"wisdom-file", 'm', "<FILE>", 0,
        "Wisfom file to use for FFT calculation"
        "\n[default: don't use wisdom file]", 1},

    // Blocks
    {0, 0, 0, 0, "Block settings:", 2},
    {"block-len", 'b', "<length>", 0,
        "Length of fixed-sized blocks, a power of two up to 65536 "
        "[default: derived from the sample rate: 32768 at 6M]", 2},
    {"history", 'h', "<length>", 0,
        "The number of samples at the beginning of a block that should be "
        "copied from the end of the previous block "
        "[default: 4920, or the 11-bit template + 64 when that is "
        "longer: 12349 at 6M]", 2},
    {"skip", 'k', "<num_blocks>", 0,
        "Number of blocks to skip while waiting for the SDR to stabilize "
        "[default: 1]", 2},

    // Tuner
    {0, 0, 0, 0, "Tuner settings (if input is 'airspy'):", 3},
    {"frequency", 'f', "<hz>", 0,
        "Frequency to tune to [default: 433.83M]", 3},
    {"sample-rate", 's', "<sps>", 0,
        "Sample rate: Mini 3M or 6M, R2 2.5M or 10M [default: 6M]", 3},
    {"gain", 'g', "<index>", 0,
        "LNA gain index (0-14) [default: 0]", 3},
    {"mixer-gain", 'M', "<index>", 0,
        "Mixer gain index (0-15) [default: 0]", 3},
    {"vga-gain", 'V', "<index>", 0,
        "VGA/IF gain index (0-15) [default: 0]", 3},
    {"bias-tee", 'B', 0, 0,
        "Enable bias tee voltage on antenna port", 3},
    {"device-index", 'd', "<index>", 0,
        "Airspy device index [default: 0]", 3},

    // Carrier detection
    {0, 0, 0, 0, "Carrier detection settings:", 4},
    {"carrier-window", 'w', "<min>-<max>", 0,
        "Window of frequency bins used for carrier detection "
        "[default: no window (0--1)]", 4},
    {"threshold", 't', "<constant>c<snr>s", 0,
        "Carrier detection theshold [default: 100c2s]", 4},

    // Misc
    {0, 0, 0, 0, "Miscellaneous:", -1},
    {"quiet", 'q', 0, 0, "Shhh", -1},
    {0, 0, 0, 0, 0, 0}
};

fargs_t* fargs_new() {
    fargs_t* fargs = malloc(sizeof(fargs_t));
    if (fargs == NULL) {
        return NULL;
    }
    
    fargs->block_len = 0;       /* derived in fargs_finalize */
    fargs->history_len = 0;
    fargs->block_len_set = false;
    fargs->history_len_set = false;
    
    fargs->threshold_const = DEFAULT_THRESHOLD_CONST;
    fargs->threshold_snr = DEFAULT_THRESHOLD_SNR;
    fargs->carrier_freq_min = DEFAULT_CARRIER_FREQ_MIN;
    fargs->carrier_freq_max = DEFAULT_CARRIER_FREQ_MAX;
    fargs->skip = DEFAULT_SKIP;
    
    fargs->input_file = default_input_file;
    fargs->wisdom_file = default_wisdom_file;
    fargs->input_card = false;

    fargs->sdr_freq = DEFAULT_SDR_FREQ;
    fargs->sdr_sample_rate = DEFAULT_SDR_SAMPLE_RATE;
    fargs->sdr_gain = DEFAULT_SDR_GAIN;
    fargs->sdr_mixer_gain = 0;
    fargs->sdr_vga_gain = 0;
    fargs->sdr_bias_tee = 0;
    fargs->sdr_dev_index = DEFAULT_SDR_INDEX;

    fargs->silent = false;

    return fargs;
}

/* Parse a decimal count min..max.  strtoul alone takes "-1" for
 * ULONG_MAX -- a -h that made fargs_finalize loop forever, a -k that
 * skipped the whole run -- so anything but digits is refused. */
static bool parse_count(const char* arg, unsigned long min,
                        unsigned long max, unsigned long* out) {
    if (*arg < '0' || *arg > '9') {
        return false;
    }
    char* endptr;
    errno = 0;
    unsigned long value = strtoul(arg, &endptr, 10);
    if (errno != 0 || *endptr != '\0' || value < min || value > max) {
        return false;
    }
    *out = value;
    return true;
}

/* Parse a gain index 0..max, printing why when it is not one. */
static bool parse_gain_index(const char* arg, int max, const char* stage,
                             int* out) {
    char* endptr;
    long value = strtol(arg, &endptr, 10);
    if (*arg == '\0' || *endptr != '\0' || value < 0 || value > max) {
        fprintf(stderr, "invalid %s gain index '%s': expected 0-%d\n",
                stage, arg, max);
        return false;
    }
    *out = (int)value;
    return true;
}

int fargs_finalize(fargs_t *fa) {
    size_t template_len = (size_t)(fa->sdr_sample_rate / CHIP_RATE
                                   * CODE_LENGTH);
    if (!fa->history_len_set) {
        fa->history_len = DEFAULT_HISTORY_LEN;
        if (fa->history_len + 1 < template_len) {
            fa->history_len = template_len + HISTORY_MARGIN;
        }
    }
    if (!fa->block_len_set) {
        size_t min_block = template_len + fa->history_len + 1;
        if (2 * fa->history_len > min_block) {
            min_block = 2 * fa->history_len;
        }
        size_t block = DEFAULT_BLOCK_LEN;
        while (block < min_block && block <= MAX_BLOCK_LEN) {
            block *= 2;
        }
        fa->block_len = block;
    }
    if (fa->block_len > MAX_BLOCK_LEN) {
        fprintf(stderr, "block length %zu exceeds %d; set -b/-h "
                "explicitly\n", fa->block_len, MAX_BLOCK_LEN);
        return FARGS_INVALID_VALUE;
    }
    if (fa->history_len >= fa->block_len) {
        fprintf(stderr, "history length %zu must be smaller than the "
                "block length %zu\n", fa->history_len, fa->block_len);
        return FARGS_INVALID_VALUE;
    }
    return 0;
}

int fargs_parse_opt(fargs_t *fargs,
                    int key,
                    char *arg) {
    switch (key) {
        case ARGP_KEY_CARD:
            fargs->input_card = true;
            break;
        case 'i': fargs->input_file = arg; break;
        case 'm': fargs->wisdom_file = arg; break;
        case 'w':
            if (!parse_carrier_str(arg,
                                   &fargs->carrier_freq_min,
                                   &fargs->carrier_freq_max)) {
                return FARGS_INVALID_VALUE;
            }
            break;
        case 't':
            if (!parse_theshold_str(arg,
                                    &fargs->threshold_const,
                                    &fargs->threshold_snr)) {
                return FARGS_INVALID_VALUE;
            }
            break;
        case 'b': {
            /* fastdet's correlation peak index is a uint16_t
             * (corr_detector.cpp), so an FFT longer than 65536 bins
             * would silently wrap peak indices.  Also enforce the
             * documented power-of-two requirement (FFT length). */
            unsigned long len;
            if (!parse_count(arg, 1, MAX_BLOCK_LEN, &len)
                    || (len & (len - 1)) != 0) {
                fprintf(stderr, "invalid block length '%s': expected a "
                        "power of two up to %d\n", arg, MAX_BLOCK_LEN);
                return FARGS_INVALID_VALUE;
            }
            fargs->block_len = len;
            fargs->block_len_set = true;
            break;
        }
        case 'h': {
            unsigned long len;
            if (!parse_count(arg, 1, MAX_BLOCK_LEN - 1, &len)) {
                fprintf(stderr, "invalid history length '%s': expected "
                        "1-%d\n", arg, MAX_BLOCK_LEN - 1);
                return FARGS_INVALID_VALUE;
            }
            fargs->history_len = len;
            fargs->history_len_set = true;
            break;
        }
        case 'k': {
            unsigned long skip;
            if (!parse_count(arg, 0, UINT_MAX, &skip)) {
                fprintf(stderr, "invalid number of blocks to skip '%s': "
                        "expected 0-%u\n", arg, UINT_MAX);
                return FARGS_INVALID_VALUE;
            }
            fargs->skip = (unsigned)skip;
            break;
        }
        case 'q':
            fargs->silent = true;
            break;
        case 'f': {
            double freq = parse_si_float(arg);
            if (!(freq >= MIN_SDR_FREQ && freq <= MAX_SDR_FREQ)) {
                fprintf(stderr, "invalid frequency '%s': the Airspy tunes "
                        "24M-1.8G\n", arg);
                return FARGS_INVALID_VALUE;
            }
            fargs->sdr_freq = (uint32_t)freq;
            break;
        }
        case 'g':
            if (!parse_gain_index(arg, 14, "LNA", &fargs->sdr_gain)) {
                return FARGS_INVALID_VALUE;
            }
            break;
        case 'M': {
            int gain;
            if (!parse_gain_index(arg, 15, "mixer", &gain)) {
                return FARGS_INVALID_VALUE;
            }
            fargs->sdr_mixer_gain = (uint8_t)gain;
            break;
        }
        case 'V': {
            int gain;
            if (!parse_gain_index(arg, 15, "VGA", &gain)) {
                return FARGS_INVALID_VALUE;
            }
            fargs->sdr_vga_gain = (uint8_t)gain;
            break;
        }
        case 'B':
            fargs->sdr_bias_tee = 1;
            break;
        case 's': {
            double rate = parse_si_float(arg);
            if (!(rate >= MIN_SDR_SAMPLE_RATE
                    && rate <= MAX_SDR_SAMPLE_RATE)) {
                fprintf(stderr, "invalid sample rate '%s': expected Mini "
                        "3M or 6M, R2 2.5M or 10M\n", arg);
                return FARGS_INVALID_VALUE;
            }
            fargs->sdr_sample_rate = (uint32_t)(rate + 0.5);
            break;
        }
        case 'd': {
            unsigned long index;
            if (!parse_count(arg, 0, UINT32_MAX, &index)) {
                fprintf(stderr, "invalid device index '%s'\n", arg);
                return FARGS_INVALID_VALUE;
            }
            fargs->sdr_dev_index = (uint32_t)index;
            break;
        }
        default:
            return FARGS_UNKNOWN;
    }
    return 0;
}

void fargs_print_summary(fargs_t *fa, FILE* out, bool sdr) {
    fprintf(out, "block size: %zu; history length: %zu\n",
            fa->block_len, fa->history_len);
    fprintf(out, "carrier bin window: min = %d; max = %d\n",
            fa->carrier_freq_min, fa->carrier_freq_max);
    fprintf(out, "threshold: constant = %g; snr = %g\n\n",
            fa->threshold_const, fa->threshold_snr);

    if (sdr) {
        fprintf(out,
                "tuner:\n"
                "  center freq = %.06f MHz\n"
                "  sample rate = %.06f Msps\n"
                "  LNA gain = %d, mixer gain = %u, VGA gain = %u\n"
                "  bias tee = %s\n\n",
                fa->sdr_freq / 1e6,
                fa->sdr_sample_rate / 1e6,
                fa->sdr_gain,
                (unsigned)fa->sdr_mixer_gain,
                (unsigned)fa->sdr_vga_gain,
                fa->sdr_bias_tee ? "on" : "off");
    }
}

void fargs_print_card_header(fargs_t *fa,
                             FILE* out,
                             bool sdr,
                             const char* tool) {
    /* v2 machine-readable header: parsed by thriftyx/block_data.card_reader
     * to select int16 (12-bit Airspy) sample decoding automatically.
     * endian/block_size/block_history mirror the Python write_card_header
     * so detect can reproduce the block geometry without a config file.
     * sample_rate=0 means "unknown" (file input); readers ignore it. */
    fprintf(out, "#v2 bit_depth=12 sample_rate=%u endian=little "
            "block_size=%zu block_history=%zu\n",
            sdr ? fa->sdr_sample_rate : 0, fa->block_len, fa->history_len);
    fprintf(out,
            "# arguments: { carrier_bin: '%d-%d', threshold: '%gc+%gs', "
            "block_size: %zu, history_size: %zu }\n",
            fa->carrier_freq_min, fa->carrier_freq_max,
            fa->threshold_const, fa->threshold_snr,
            fa->block_len, fa->history_len);
    if (sdr) {
        fprintf(out, "# tuner: { freq: %u; sample_rate: %u; "
                "lna_gain: %d; mixer_gain: %u; vga_gain: %u; bias_tee: %u }\n",
                fa->sdr_freq, fa->sdr_sample_rate,
                fa->sdr_gain,
                (unsigned)fa->sdr_mixer_gain,
                (unsigned)fa->sdr_vga_gain,
                (unsigned)fa->sdr_bias_tee);
    }
    fprintf(out, "# tool: '%s'\n", tool);

    struct timeval tv;
    gettimeofday(&tv, NULL);
    fprintf(out, "# start_time: %ld.%06ld\n", tv.tv_sec, tv.tv_usec);
    fflush(out);
}
