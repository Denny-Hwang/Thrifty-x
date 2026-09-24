# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""
Load common settings from a config file and / or command-line arguments.

Example:
    parser = argparse.ArgumentParser()
    config, args = settings.load_args(parser, ['sample_rate', 'chip_rate'])
"""


import argparse
import contextlib
import logging
import sys
from collections import namedtuple

from thriftyx import setting_parsers
from thriftyx.exceptions import (ConfigSyntaxError, ConfigValidationError,
                                 FileFormatError, SettingKeyError)
from thriftyx.hal.profiles import DEFAULT_DEVICE_TYPE, PROFILES, get_profile


# Setting definition
Definition = namedtuple('Definition', 'args parser default description')

DEFINITIONS = {
    # Default derived from device_type (see DEVICE_DERIVED_KEYS).
    'sample_rate': Definition(
        ['--sample-rate', '-s'],
        setting_parsers.metric_float,
        None,
        "Sample rate (sps)"
    ),

    'chip_rate': Definition(
        ['--chip-rate', '-p'],
        setting_parsers.metric_float,
        '0.999707M',
        "Rate at which the code is being transmitted (bps)"
    ),

    'tuner_freq': Definition(
        ['--freq', '-f'],
        setting_parsers.metric_float,
        '433.83M',
        "Tuner center frequency (Hz)"
    ),

    'tuner_gain': Definition(
        ['--gain', '-g'],
        float,
        '0',
        "Tuner gain (dB)"
    ),

    'capture_skip': Definition(
        ['--skip', '-k'],
        int,
        '1',
        "Number of blocks to skip before starting capturing from the SDR"
    ),

    'block_size': Definition(
        ['--block-size', '-b'],
        int,
        '16384',
        "Length of fixed-sized blocks, which should be a power of two "
        "(samples)"
    ),

    'block_history': Definition(
        ['--history', '-y'],
        int,
        '4920',
        "The number of samples at the end of a block that should be repeated "
        "at the start of the next block (samples)"
    ),

    'carrier_window': Definition(
        ['--carrier-window', '-w'],
        setting_parsers.freq_range,
        '0--1',
        "Range of frequencies or frequency bins to look for carrier"
    ),

    'carrier_threshold': Definition(
        ['--carrier-threshold', '-t'],
        setting_parsers.threshold,
        '15*snr',
        "Threshold formula for carrier detector"
    ),

    'corr_threshold': Definition(
        ['--corr-threshold', '-u'],
        setting_parsers.threshold,
        '15*snr',
        "Threshold formula for correlation peak detector"
    ),

    'template': Definition(
        ['--template', '-z'],
        str,
        'template.npy',
        "Load template from a Numpy .npy file"
    ),

    'rxid': Definition(
        ['--rxid', '-r'],
        int,
        -1,
        "Unique identifier of this receiver"
    ),

    'device_type': Definition(
        ['--device-type'],
        setting_parsers.one_of(*PROFILES),
        DEFAULT_DEVICE_TYPE,
        "SDR device type ({}); sets the default sample rate and bit "
        "depth".format(', '.join(repr(k) for k in PROFILES))
    ),

    # Default derived from device_type (see DEVICE_DERIVED_KEYS).
    'bit_depth': Definition(
        ['--bit-depth'],
        setting_parsers.bit_depth,
        None,
        "ADC bit depth (8 for RTL-SDR, 12 for Airspy)"
    ),

    'bias_tee': Definition(
        ['--bias-tee'],
        setting_parsers.parse_bool,
        'false',
        "Enable bias tee voltage on antenna port"
    ),

    'airspy_serial': Definition(
        ['--airspy-serial'],
        setting_parsers.airspy_serial,
        None,
        "Airspy device serial (hex or decimal). Selects a specific Airspy "
        "when multiple devices are connected. Takes precedence over "
        "--device-index."
    ),

    'gain_mode': Definition(
        ['--gain-mode'],
        str,
        'manual',
        "Airspy gain mode: 'manual' (lna/mixer/vga indices), 'linearity' "
        "(low-IMD profile), or 'sensitivity' (high-NF profile). "
        "Linearity/sensitivity require --combined-gain."
    ),

    'combined_gain': Definition(
        ['--combined-gain'],
        int,
        '0',
        "Combined gain index (0-21) used when gain_mode is 'linearity' "
        "or 'sensitivity'. Ignored in 'manual' mode."
    ),

    'lna_agc': Definition(
        ['--lna-agc'],
        setting_parsers.parse_bool,
        'false',
        "Engage Airspy LNA AGC loop (manual mode only)"
    ),

    'mixer_agc': Definition(
        ['--mixer-agc'],
        setting_parsers.parse_bool,
        'false',
        "Engage Airspy mixer AGC loop (manual mode only)"
    ),

    'ppm': Definition(
        ['--ppm'],
        float,
        '0.0',
        "Crystal frequency correction in parts-per-million applied to "
        "Airspy LO requests. Positive = crystal runs fast."
    ),

    'packing': Definition(
        ['--packing'],
        setting_parsers.parse_bool,
        'false',
        "Enable libairspy 12-bit USB packing (saves 25% bandwidth; "
        "useful at the highest sample rates on USB 2.0 hosts)"
    ),

    'lna_gain': Definition(
        ['--lna-gain'],
        int,
        '0',
        "LNA gain stage index (Airspy: 0-14)"
    ),

    'mixer_gain': Definition(
        ['--mixer-gain'],
        int,
        '0',
        "Mixer gain stage index (Airspy: 0-15)"
    ),

    'vga_gain': Definition(
        ['--vga-gain'],
        int,
        '0',
        "VGA/IF gain stage index (Airspy: 0-15)"
    ),

    'freq_shift_method': Definition(
        ['--freq-shift-method'],
        setting_parsers.one_of('integer', 'time_domain'),
        'integer',
        "Frequency shift method: 'integer' (fast, ~1.07m RMSE) or "
        "'time_domain' (slow, ~1.04m RMSE)"
    ),

    'soa_interpolation': Definition(
        ['--soa-interpolation'],
        setting_parsers.one_of('parabolic', 'gaussian', 'none'),
        'parabolic',
        "SOA interpolation method: 'parabolic', 'gaussian', or 'none'"
    ),
}

# Settings whose default comes from the device profile of the configured
# device_type rather than from DEFINITIONS, mapped to the profile field.
DEVICE_DERIVED_KEYS = {
    'sample_rate': 'default_sample_rate',
    'bit_depth': 'bit_depth',
}


def _device_default_help(key):
    """Describe a device-derived default for --help."""
    field = DEVICE_DERIVED_KEYS[key]
    parts = []
    for device_type, profile in PROFILES.items():
        value = getattr(profile, field)
        if key == 'sample_rate':
            value = "{:g}M".format(value / 1e6)
        parts.append("{} {}".format(device_type, value))
    return " [default: by --device-type: {}]".format(', '.join(parts))


def _apply_device_defaults(values, definitions):
    """Fill device-derived settings that were not set explicitly.

    Mutates *values*.  Raises ConfigValidationError for an unknown
    device_type, since no default can be derived for it.
    """
    missing = [key for key in DEVICE_DERIVED_KEYS
               if key in definitions and key not in values]
    if not missing:
        return
    profile = get_profile(values.get('device_type', DEFAULT_DEVICE_TYPE))
    for key in missing:
        values[key] = definitions[key].parser(
            str(getattr(profile, DEVICE_DERIVED_KEYS[key])))


# Longest transmitter code the default block geometry must fit: 11 bits,
# 2047 chips.  The upstream Thrifty transmitters send one (the template
# captured from them, example/template.npy, is gold(11, 0)), and every
# shorter code fits in the same overlap.
MAX_CODE_LENGTH = 2047
# Overlap beyond the template when the stock 4920 is too short for it.
# A template made for the transmitter's measured chip rate, rather than
# the nominal one, is longer by the rate error: 64 samples is 0.3-1 %
# of slack at 2.5-10 Msps and leaves every default block_size unchanged.
# (2.4 Msps keeps the upstream 4920, which holds the 4914-sample 11-bit
# template.)
HISTORY_MARGIN = 64
# The stock block_history (upstream Thrifty's, and the --history default).
STOCK_BLOCK_HISTORY = 4920
# Register lengths gold.py generates codes for.
CODE_BITS = range(5, 12)


# Samples per chip (sample_rate / chip_rate) a template can be built
# with.  Below one the code does not fit; far above, chip_rate is a typo
# (a lowercase 'm' means milli) whose template would size blocks in
# terabytes.  At 2.4-10 Msps and the transmitters' ~1 Mchip/s it is 2-10.
SAMPLES_PER_CHIP = (1, 100)


def _check_chip_rate(values):
    """Reject a chip_rate that gives no usable block geometry.

    Every command sizes its blocks by the template chip_rate implies
    (:func:`compute_block_params`), so a zero would crash each of them
    and ``0.999707m`` would ask for a 2**45-sample block.  Raises
    ConfigValidationError, which exits with status 78 like any other
    bad setting.
    """
    sample_rate = values.get('sample_rate')
    chip_rate = values.get('chip_rate')
    if chip_rate is None:
        return
    if not chip_rate > 0:
        raise ConfigValidationError(
            "chip_rate must be positive, got {}".format(_fmt(chip_rate)))
    if sample_rate is None:
        return
    low, high = SAMPLES_PER_CHIP
    sps = sample_rate / chip_rate
    if not low <= sps <= high:
        raise ConfigValidationError(
            "sample_rate {} / chip_rate {} is {:.3g} samples per chip; "
            "expected {}-{}.  chip_rate is in chips/s (the transmitters "
            "send 0.999707M), and a lowercase 'm' suffix means milli, "
            "not mega.".format(_fmt(sample_rate), _fmt(chip_rate), sps,
                               low, high))


def compute_block_params(sample_rate, chip_rate,
                         code_length=MAX_CODE_LENGTH):
    """Compute appropriate block_history and block_size for given rates.

    ``block_history`` is the stock 4920 when that holds the template
    (the correlation needs at least ``template_len - 1``), else the
    template plus ``HISTORY_MARGIN`` samples -- the rule fastcapture's
    fargs.c applies too.  The returned
    ``block_size`` is guaranteed to satisfy
    ``block_size >= 2 * block_history`` so that ``new_samples``
    (``block_size - block_history``) is always at least as large as
    ``block_history``.  This ensures that the history portion of a
    newly read block can be obtained by slicing the previous raw
    buffer without silent truncation.

    Parameters
    ----------
    sample_rate : float
    chip_rate : float
    code_length : int
        Code length in chips (default: 2047, the longest supported, so
        that any transmitter code fits).

    Returns
    -------
    block_size : int
        Recommended block size (power of 2).
    block_history : int
        Recommended block history.
    template_len : int
        Expected template length in samples.
    """
    sps = sample_rate / chip_rate
    template_len = int(sps * code_length)
    block_history = STOCK_BLOCK_HISTORY
    if block_history < template_len - 1:
        block_history = template_len + HISTORY_MARGIN
    # block_size must be large enough for template + history, AND must
    # ensure new_samples = block_size - block_history >= block_history
    # so that the raw read buffer always contains enough data for the
    # next history window.
    min_block = max(template_len + block_history + 1,
                    2 * block_history)
    block_size = 1
    while block_size < min_block:
        block_size *= 2
    return block_size, block_history, template_len


def longest_code_bits(block_history, sample_rate, chip_rate):
    """Longest register length whose template ``block_history`` can hold.

    Returns the largest n in ``CODE_BITS`` such that a code of
    ``2**n - 1`` chips, sampled at ``sample_rate``, needs at most
    ``block_history + 1`` samples; ``None`` if not even the shortest
    fits.
    """
    sps = sample_rate / chip_rate
    fitting = [n for n in CODE_BITS
               if int(sps * (2 ** n - 1)) - 1 <= block_history]
    return max(fitting) if fitting else None


def _apply_recorded_arguments(values, header, adopted, explicit):
    """Adopt the geometry older C cards record on "# arguments".

    fastcapture/fastdet wrote ``block_size`` and ``history_size`` there
    (see block_data.peek_card_header) before the #v2 line recorded
    them, including on cards whose #v2 line says sample_rate=0.  Like
    the #v2 values, they describe the data and override the config.
    """
    for key, field in (('block_size', 'arguments_block_size'),
                       ('block_history', 'history_size')):
        if field not in header or key not in values or key in adopted:
            continue
        try:
            recorded = int(header[field])
        except ValueError:
            continue
        if key in explicit and recorded != values[key]:
            logging.warning(
                "%s=%s recorded in the .card header overrides the "
                "configured %s=%s", key, recorded, key, _fmt(values[key]))
        values[key] = recorded
        adopted.add(key)


def _apply_pre_header_geometry(values, adopted, explicit):
    """Fill the geometry of a card that records no block_history at all.

    Python capture recorded nothing before the #v2 line carried it, and
    used ``_pre_header_block_params`` for the recorded rate; a recorded
    block_size that disagrees with that rule leaves the overlap a guess.
    Anything assumed is logged, since a wrong overlap shifts every SoA.
    """
    if 'block_history' not in values:
        return                              # a command without geometry
    chip_rate = values.get('chip_rate') or DEFINITIONS['chip_rate'].parser(
        DEFINITIONS['chip_rate'].default)
    rate = values['sample_rate']
    size = values.get('block_size')
    if 'block_history' in explicit:
        history = values['block_history']
    else:
        history = _pre_header_block_params(rate, chip_rate)[1]
        values['block_history'] = history
    old_size = _pre_header_block_params(rate, chip_rate, history)[0]
    if 'block_size' in values and 'block_size' not in adopted \
            and 'block_size' not in explicit:
        values['block_size'] = old_size
    if 'block_history' in explicit:
        return
    if 'block_size' in adopted and size != old_size:
        # Capture chose the overlap independently of a configured
        # block_size, so the old rule is still the best guess -- but not
        # a safe one.
        logging.warning(
            "the .card header records block_size=%s but not block_history, "
            "and %s is not what capture used by default at %s sps; the "
            "overlap is unknown -- assuming %d, the default history then. "
            "Set --history to the value used for the capture.", size,
            size, _fmt(rate), history)
        return
    logging.warning(
        "the .card header does not record block_history; assuming %d, what "
        "capture used at %s sps before it was recorded. Set --history if "
        "this card was captured with another value.", history, _fmt(rate))


def _pre_header_block_params(sample_rate, chip_rate, block_history=None):
    """Block geometry capture used before cards recorded block_history.

    The stock 16384 / 4920, enlarged when a 1023-chip template did not
    fit (history twice that template): 32768 / 12278 at 6 Msps, 65536 /
    20464 at 10 Msps.  A given *block_history* (one the user set) keeps
    its value and sizes the block for it.  Only for reading such files.
    """
    template_len = int(sample_rate / chip_rate * 1023)
    if block_history is None:
        block_history = 4920
        if block_history < template_len - 1:
            block_history = 2 * template_len
    min_block = max(template_len + block_history + 1, 2 * block_history)
    block_size = 16384
    while block_size < min_block:
        block_size *= 2
    return block_size, block_history


def _auto_adjust_block_params(values, explicit=None, recorded=frozenset()):
    """Auto-adjust block_history and block_size when too small for sample rate.

    Called after parsing all settings.  Only enlarges *default-derived*
    parameters that cannot hold the template of the longest supported
    code (``MAX_CODE_LENGTH`` chips) at the sample rate: a value the user
    set explicitly (config file or CLI) is never rewritten.  Instead the
    longest code it can still correlate is logged at WARNING: a capture
    made with it can never be re-blocked for a longer code.

    Adjusting defaults is logged at INFO (it happens on every Airspy
    run).

    Parameters
    ----------
    values : dict
        Parsed setting values (mutated in place).
    explicit : set or None
        Keys that were set explicitly rather than filled from defaults.
        ``None`` is treated as "nothing explicit" (legacy behavior).
    recorded : set
        Keys among *explicit* that a .card header recorded: facts about
        the data rather than settings, reported without config advice.
    """
    explicit = explicit if explicit is not None else frozenset()
    sample_rate = values.get('sample_rate')
    chip_rate = values.get('chip_rate')
    if sample_rate is None or chip_rate is None:
        return values

    _, rec_history, template_len = compute_block_params(sample_rate, chip_rate)

    block_history = values.get('block_history')
    if block_history is not None and block_history < template_len - 1:
        if 'block_history' in recorded:
            # The card is what it is; a template too long for it fails
            # loudly in the correlator.  Nothing to configure here.
            bits = longest_code_bits(block_history, sample_rate, chip_rate)
            logging.info(
                "this .card was captured with block_history %d, which at "
                "%.1f Msps holds codes up to %s bits", block_history,
                sample_rate / 1e6, bits)
        elif 'block_history' in explicit:
            # Captured blocks cannot be re-blocked later: a card recorded
            # with this overlap can never be correlated with a longer
            # code's template.  Hence a warning, even though it is right
            # when every transmitter sends a shorter code.
            bits = longest_code_bits(block_history, sample_rate, chip_rate)
            fits = ("codes up to {} bits".format(bits) if bits
                    else "no supported code")
            logging.warning(
                "block_history %d at %.1f Msps holds %s; an 11-bit code's "
                "%d-sample template needs %d, so its captures could never "
                "be correlated; keeping the configured value. Remove "
                "block_history (and block_size) from the config to use "
                "the default %d unless every transmitter sends a shorter "
                "code.", block_history, sample_rate / 1e6, fits,
                template_len, template_len - 1, rec_history)
        else:
            values['block_history'] = rec_history
            # Expected whenever the rate needs longer blocks than the
            # stock defaults (e.g. every Airspy run): INFO, not WARNING.
            logging.info(
                "Auto-adjusted default block_history %d -> %d "
                "(template_len=%d at %.1f Msps). Set block_history "
                "explicitly to override.",
                block_history, rec_history, template_len,
                sample_rate / 1e6)

    block_history = values.get('block_history', rec_history)
    block_size = values.get('block_size')
    if block_size is not None:
        min_block = max(template_len + block_history + 1,
                        2 * block_history)
        if block_size < min_block:
            if 'block_size' in recorded:
                pass                    # the card's geometry, see above
            elif 'block_size' in explicit:
                logging.warning(
                    "block_size %d is smaller than the recommended "
                    "minimum %d for block_history=%d and a %d-sample "
                    "template (%d chips); keeping the configured value.",
                    block_size, min_block, block_history, template_len,
                    MAX_CODE_LENGTH)
            else:
                new_size = 1
                while new_size < min_block:
                    new_size *= 2
                logging.info(
                    "Auto-adjusted default block_size %d -> %d "
                    "(template_len=%d, block_history=%d). This changes "
                    "the FFT length/bin width; set block_size explicitly "
                    "to override.",
                    block_size, new_size, template_len, block_history)
                values['block_size'] = new_size

    return values


# Settings a .card v2 header records.  They describe how the file was
# captured, so they are facts about the data rather than preferences:
# a detector configured differently would mis-map block indices to
# sample-of-arrival (block_size/block_history), mis-scale carrier
# frequencies and TDOAs (sample_rate), or mis-decode samples (bit_depth).
CARD_HEADER_KEYS = ('sample_rate', 'block_size', 'block_history',
                    'bit_depth')


def apply_card_header(config, header):
    """Adopt the capture parameters recorded in a .card ``#v2`` header.

    Each recorded key that *config* carries is replaced by the header's
    value.  When the user set that key explicitly (CLI or config file)
    and it disagrees, a warning names both values; the header still
    wins because it describes the data actually on disk.

    Older cards: a geometry the C writers recorded on the "# arguments"
    line is adopted like the #v2 values.  If the header records
    ``sample_rate`` but no history anywhere (Python cards written before
    it was recorded), block parameters that were filled from defaults
    are set to the geometry capture used back then for the recorded rate
    (``_pre_header_block_params``); the current defaults would give a
    different overlap than the file has.

    Parameters
    ----------
    config : Namespace
        Settings as returned by :func:`load_args`.
    header : dict
        Fields from :func:`thriftyx.block_data.peek_card_header`.

    Returns
    -------
    Namespace
        *config* itself when nothing was adopted, otherwise a new
        Namespace whose ``explicit_keys`` include the adopted keys.
    """
    explicit = getattr(config, 'explicit_keys', frozenset())
    values = dict(config)
    adopted = set()
    for key in CARD_HEADER_KEYS:
        if key not in header or key not in values:
            continue
        try:
            recorded = DEFINITIONS[key].parser(header[key])
        except ValueError as exc:
            if key == 'bit_depth':
                # Decoding with a guessed width would turn the whole
                # file into noise without saying so.
                raise FileFormatError(
                    ".card header records bit_depth={!r}: {}".format(
                        header[key], exc)) from None
            logging.warning("ignoring unparseable .card header value "
                            "%s=%r", key, header[key])
            continue
        if recorded < 0 or (recorded == 0 and key != 'block_history'):
            # fastcapture writes sample_rate=0 when re-emitting a file
            # input whose rate it does not know.  block_history=0 is a
            # valid geometry (no overlap).
            continue
        if key in explicit and recorded != values[key]:
            logging.warning(
                "%s=%s recorded in the .card header overrides the "
                "configured %s=%s", key, _fmt(recorded), key,
                _fmt(values[key]))
        values[key] = recorded
        adopted.add(key)
    if 'block_history' not in header:
        _apply_recorded_arguments(values, header, adopted, explicit)
    if not adopted:
        return config

    if 'sample_rate' in adopted and 'block_history' not in adopted:
        # Written before the #v2 line recorded block_history.  Re-deriving
        # it with today's rule could give a different overlap than the
        # file has and shift every sample-of-arrival, so use what the
        # writer used.
        _apply_pre_header_geometry(values, adopted, explicit)
    elif 'sample_rate' in adopted:
        # Defaults derived for the configured rate are stale now; derive
        # them again for the recorded rate.
        for key in ('block_size', 'block_history'):
            if key in values and key not in adopted and key not in explicit:
                definition = DEFINITIONS[key]
                values[key] = definition.parser(definition.default)
        added_chip_rate = 'chip_rate' not in values
        if added_chip_rate:
            chip_def = DEFINITIONS['chip_rate']
            values['chip_rate'] = chip_def.parser(chip_def.default)
        values = _auto_adjust_block_params(values, set(explicit) | adopted,
                                           recorded=adopted)
        if added_chip_rate:
            values.pop('chip_rate')

    new_config = Namespace(values)
    new_config.explicit_keys = frozenset(explicit) | adopted
    return new_config


def _fmt(value):
    """Format a setting value for a log message (6M, not 6000000.0)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


DEFAULT_CONFIG_PATH = 'detector.cfg'
CONFIG_COMMENT_CHAR = '#'
CONFIG_DELIMITER = ':'
CONFIG_DEST = 'config'


class Namespace(dict):
    """A hackish dict-like object with elements accessible as attributes.

    Similar to argparse's Namespace class."""
    # pylint: disable=no-member
    def __init__(self, dict_):
        dict.__init__(self, dict_)
        self.__dict__.update(dict_)


def _bool_flag_value(string):
    """argparse type for boolean settings: keep the word, reject non-bools.

    Returns the string unchanged (load() parses it like a config-file
    value).  A bare boolean flag followed by a positional argument would
    otherwise swallow it as its value; the error says how to avoid that.
    """
    try:
        setting_parsers.parse_bool(string)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected true/false, got {!r}; a bare flag must come after "
            "the positional arguments, or write it as --flag=true"
            .format(string)) from None
    return string


def add_argparse_arguments(parser, keys, definitions=None):
    """Generate argparse arguments for the settings with the given keys."""
    if definitions is None:
        definitions = DEFINITIONS
    for key in keys:
        if key not in definitions:
            raise SettingKeyError("Unknown key: {}".format(key))
        setting = definitions[key]
        if len(setting.args):
            # argparse %-formats help text; a literal '%' (e.g. "25%")
            # would crash --help.
            help_str = str(setting.description).replace('%', '%%')
            if setting.default is not None:
                help_str += " [default: {}]".format(setting.default)
            elif key in DEVICE_DERIVED_KEYS:
                help_str += _device_default_help(key)
            if setting.parser is setting_parsers.parse_bool:
                # `--packing` alone means true; `--packing false` and
                # `--packing=true` also work.
                parser.add_argument(*setting.args, dest=key, nargs='?',
                                    const='true', type=_bool_flag_value,
                                    help=help_str + " (a bare flag means "
                                    "true)")
            else:
                parser.add_argument(*setting.args, dest=key,
                                    type=str,
                                    help=help_str)


def load(args=None, config_file=None, definitions=None,
         return_explicit=False):
    """Load settings from config file and/or command-line arguments.

    Returns the default values if neither config_file nor args are specified.

    If config_file is None and args contains the key 'config', the value of
    'config' will be used as the path of the config file. If the value of
    'config' is None, the default config file will be used.

    Parameters
    ----------
    args : dict-like object
        Argument strings that should override config values.
    config_file : file-like object
        Key-value config file to load settings from.
    definitions : dict
        Setting definitions (defaults to DEFINITIONS).
    return_explicit : bool
        When True, also return the set of keys that were set explicitly
        (via config file or args) rather than filled from defaults.

    Returns
    -------
    dict
        Map of setting keys to setting values.  When ``return_explicit``
        is True, a ``(values, explicit_keys)`` tuple instead.

    Raises
    ------
    IOError
        If the input file cannot be read.
    ConfigSyntaxError
        If the syntax of the config file is incorrect.
    SettingKeyError
        If a non-existing setting was specified in the config file or in args.
    ConfigValidationError
        If a string could not be converted to a settings value, or
        chip_rate is impossible for the sample rate.
    """

    if definitions is None:
        definitions = DEFINITIONS

    # Default values
    strings = {key: setting.default
               for key, setting in definitions.items()
               if setting.default is not None}
    explicit = set()

    # Load config
    if config_file is not None:
        config_settings = parse_kvconfig(config_file)
        for key in config_settings:
            if key not in definitions:
                raise SettingKeyError("Unknown setting: {}".format(key))
        strings.update(config_settings)
        explicit.update(config_settings)

    # Override values from arguments
    if args is not None:
        for key in args:
            if key not in definitions:
                raise SettingKeyError("Unknown setting: {}".format(key))
        strings.update(args)
        explicit.update(args)

    # Parse
    values = {}
    for key, string in strings.items():
        try:
            values[key] = definitions[key].parser(string)
        except (ValueError, TypeError) as exc:
            raise ConfigValidationError(
                "invalid value for {}: {!r} ({})".format(key, string, exc)
            ) from None

    # Defaults that depend on the device (sample rate, bit depth).
    _apply_device_defaults(values, definitions)
    _check_chip_rate(values)

    # Auto-adjust block parameters for higher sample rates (defaults
    # only — explicitly-set values are respected, with a warning).
    values = _auto_adjust_block_params(values, explicit)

    if return_explicit:
        return values, explicit
    return values


def load_args(parser, keys, argv=None, definitions=None):
    """Convenience function for loading a subset of settings.

    Generate argparse arguments for the settings with the given keys, parse the
    arguments, load settings from config file specified by '--config' argument
    and parse settings.

    Parameters
    ----------
    parser : argparse.ArgumentParser object
    keys : list of strings
    argv : list of strings
        The command-line args (defaults to sys.argv).
    definitions : dict
        Setting definitions (defaults to DEFINITIONS).

    Returns
    -------
    settings : Namespace
        Map of the requested setting keys to setting values.
    extra_args : Namespace
        Any extra arguments that were added to the parser before this function
        got hold of it.
    """
    if definitions is None:
        definitions = DEFINITIONS
    if argv is None:
        argv = sys.argv[1:]

    parser.add_argument('-v', '--verbose', help="Increase output verbosity",
                        action="store_true")

    parser.add_argument('-c', '--config', dest=CONFIG_DEST,
                        type=str, default=None,
                        help="Config file to load settings from "
                             "[default: {}]".format(DEFAULT_CONFIG_PATH))
    add_argparse_arguments(parser, keys, definitions=definitions)
    args_namespace = parser.parse_args(argv)
    args = vars(args_namespace)

    if args['verbose']:
        logging.basicConfig(level=logging.DEBUG)

    # Load config file
    config_file = None
    config_arg = args[CONFIG_DEST]
    args.pop(CONFIG_DEST)

    with contextlib.ExitStack() as stack:
        if config_arg is None:
            try:
                config_file = stack.enter_context(
                    open(DEFAULT_CONFIG_PATH))
                logging.info("Loaded default config file from %s",
                             DEFAULT_CONFIG_PATH)
            except IOError:
                # Do not throw IOError if the config file has not been
                # specified explicitly.
                logging.warning("No config file found. Using default values.")
        else:
            config_file = stack.enter_context(open(config_arg))
            logging.info("Loaded config file from %s", config_arg)

        key_args = {k: v for k, v in args.items()
                    if k in keys and v is not None}
        extra_args = {k: v for k, v in args.items() if k not in keys}

        settings, explicit = load(key_args, config_file, definitions,
                                  return_explicit=True)
        subset = {k: v for k, v in settings.items() if k in keys}

    settings_obj = Namespace(subset)
    # Record which of the requested keys were set explicitly (CLI flag or
    # config file) so callers can distinguish them from filled defaults.
    # Stored as an attribute only — it does not appear in dict iteration.
    settings_obj.explicit_keys = frozenset(k for k in explicit if k in keys)
    args_obj = Namespace(extra_args)
    return settings_obj, args_obj


def parse_kvconfig(config_file):
    """A simple key:value config file parser."""
    settings = {}
    for line_no, line in enumerate(config_file):
        if CONFIG_COMMENT_CHAR in line:
            line, _ = line.split(CONFIG_COMMENT_CHAR, 1)
        if len(line.strip()) == 0:
            continue
        if CONFIG_DELIMITER not in line:
            raise ConfigSyntaxError(line_no + 1, 'No delimiter found')
        key, value = line.split(CONFIG_DELIMITER, 1)
        settings[key.strip()] = value.strip()
    return settings
