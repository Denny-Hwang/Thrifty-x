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


import contextlib
import logging
import math
import sys
from collections import namedtuple

from thriftyx import setting_parsers
from thriftyx.exceptions import ConfigError, ConfigSyntaxError, SettingKeyError


# Setting definition
Definition = namedtuple('SettingDefinition', 'args parser default description')

DEFINITIONS = {
    'sample_rate': Definition(
        ['--sample-rate', '-s'],
        setting_parsers.metric_float,
        '2.4M',
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
        str,
        'airspy_mini',
        "SDR device type ('airspy_mini' or 'airspy_r2')"
    ),

    'bit_depth': Definition(
        ['--bit-depth'],
        int,
        '8',
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
        str,
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
        "Enable libairspy 12-bit USB packing (saves ~33% bandwidth; "
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
        str,
        'integer',
        "Frequency shift method: 'integer' (fast, ~1.07m RMSE) or "
        "'time_domain' (slow, ~1.04m RMSE)"
    ),

    'soa_interpolation': Definition(
        ['--soa-interpolation'],
        str,
        'parabolic',
        "SOA interpolation method: 'parabolic', 'gaussian', or 'none'"
    ),
}

DEFAULT_CODE_LENGTH = 1023  # 10-bit Gold code (2^10 - 1)


def compute_block_params(sample_rate, chip_rate,
                         code_length=DEFAULT_CODE_LENGTH):
    """Compute appropriate block_history and block_size for given rates.

    The returned ``block_size`` is guaranteed to satisfy
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
        Gold code length (default: 1023 for 10-bit register).

    Returns
    -------
    block_size : int
        Recommended block size (power of 2).
    block_history : int
        Recommended block history (~ 2x template length).
    template_len : int
        Expected template length in samples.
    """
    sps = sample_rate / chip_rate
    template_len = int(sps * code_length)
    block_history = template_len * 2
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


def _auto_adjust_block_params(values):
    """Auto-adjust block_history and block_size when too small for sample rate.

    Called after parsing all settings.  Only enlarges parameters that are
    insufficient for the estimated template length; explicitly large values
    set by the user are left untouched.
    """
    sample_rate = values.get('sample_rate')
    chip_rate = values.get('chip_rate')
    if sample_rate is None or chip_rate is None:
        return values

    _, rec_history, template_len = compute_block_params(sample_rate, chip_rate)

    block_history = values.get('block_history')
    if block_history is not None and block_history < template_len - 1:
        old = block_history
        values['block_history'] = rec_history
        logging.info(
            "Auto-adjusted block_history %d -> %d "
            "(template_len=%d at %.1f Msps)",
            old, rec_history, template_len, sample_rate / 1e6)

    block_history = values.get('block_history', rec_history)
    block_size = values.get('block_size')
    if block_size is not None:
        min_block = max(template_len + block_history + 1,
                        2 * block_history)
        if block_size < min_block:
            new_size = 1
            while new_size < min_block:
                new_size *= 2
            logging.info(
                "Auto-adjusted block_size %d -> %d "
                "(template_len=%d, block_history=%d)",
                block_size, new_size, template_len, block_history)
            values['block_size'] = new_size

    return values


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


def add_argparse_arguments(parser, keys, definitions=None):
    """Generate argparse arguments for the settings with the given keys."""
    if definitions is None:
        definitions = DEFINITIONS
    for key in keys:
        if key not in definitions:
            raise SettingKeyError("Unknown key: {}".format(key))
        setting = definitions[key]
        if len(setting.args):
            help_str = str(setting.description)
            if setting.default is not None:
                help_str += " [default: {}]".format(setting.default)
            parser.add_argument(*setting.args, dest=key,
                                type=str,
                                help=help_str)


def load(args=None, config_file=None, definitions=None):
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

    Returns
    -------
    dict
        Map of setting keys to setting values.

    Raises
    ------
    IOError
        If the input file cannot be read.
    ConfigSyntaxError
        If the syntax of the config file is incorrect.
    SettingKeyError
        If a non-existing setting was specified in the config file or in args.
    ValueError
        If a string could not be converted to a settings value.
    """

    if definitions is None:
        definitions = DEFINITIONS

    # Default values
    strings = {key: setting.default
               for key, setting in definitions.items()
               if setting.default is not None}

    # Load config
    if config_file is not None:
        config_settings = parse_kvconfig(config_file)
        for key in config_settings:
            if key not in definitions:
                raise SettingKeyError("Unknown setting: {}".format(key))
        strings.update(config_settings)

    # Override values from arguments
    if args is not None:
        for key in args:
            if key not in definitions:
                raise SettingKeyError("Unknown setting: {}".format(key))
        strings.update(args)

    # Parse
    values = {k: definitions[k].parser(v) for k, v in strings.items()}

    # Auto-adjust block parameters for higher sample rates
    values = _auto_adjust_block_params(values)

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

        settings = load(key_args, config_file, definitions)
        subset = {k: v for k, v in settings.items() if k in keys}

    settings_obj = Namespace(subset)
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
