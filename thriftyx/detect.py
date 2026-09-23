# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""Detect presence of positioning signals and estimate sample-of-arrival."""


import argparse
import logging
import sys
from collections import namedtuple

import numpy as np

from thriftyx import gold
from thriftyx import settings as settings_module
from thriftyx.settings import load_args
from thriftyx import toads_data
from thriftyx import util
from thriftyx.block_data import block_reader, card_reader, peek_card_header
from thriftyx.exceptions import FileFormatError, TemplateError
from thriftyx.carrier_sync import DefaultSynchronizer
from thriftyx.setting_parsers import normalize_freq_range
from thriftyx.soa_estimator import SoaEstimator


DetectorSettings = namedtuple('DetectorSettings', [
    'block_len',
    'history_len',
    'carrier_len',
    'carrier_thresh',
    'carrier_window',
    'template',
    'corr_thresh',
    'freq_shift_method',
    'soa_interpolation'],
    defaults=['integer', 'parabolic'])


class Detector:
    """Detect positioning signals and estimate sample-of-arrival.

    All-in-one signal detection and sample-of-arrival estimation. Find carrier,
    synchronise to carrier, correlate with template, and estimate SoA.
    """
    def __init__(self, settings, blocks=None, rxid=-1, yield_data=False):
        self.settings = settings
        self.blocks = iter(blocks) if blocks is not None else None
        self.rxid = rxid
        self.yield_data = yield_data

        self.sync = DefaultSynchronizer(
            thresh_coeffs=settings.carrier_thresh,
            window=settings.carrier_window,
            block_len=settings.block_len,
            carrier_len=settings.carrier_len,
            freq_shift_method=settings.freq_shift_method)

        self.soa_estimate = SoaEstimator(
            template=settings.template,
            thresh_coeffs=settings.corr_thresh,
            block_len=settings.block_len,
            history_len=settings.history_len,
            interpolation_method=settings.soa_interpolation)

        self.new_len = settings.block_len - settings.history_len

    def detect(self, timestamp, block_idx, block):
        """Process the given block of data."""
        if len(block) != self.settings.block_len:
            raise FileFormatError(_block_length_message(
                block_idx, len(block), self.settings.block_len))
        shifted_fft, carrier_info = self.sync(block)

        if shifted_fft is not None:  # detected
            detected, corr_info, corr = self.soa_estimate(shifted_fft)
            soa = (self.new_len * block_idx
                   + corr_info.sample
                   + corr_info.offset)
        else:
            detected, corr_info, soa, corr = False, None, None, None

        result = toads_data.DetectionResult(timestamp, block_idx, soa,
                                            carrier_info, corr_info, self.rxid)
        if self.yield_data:
            return detected, result, shifted_fft, corr
        else:
            return detected, result

    def next(self):
        """Process the next block of data."""
        return self.detect(*next(self.blocks))

    def __call__(self, timestamp, block_idx, block):
        return self.detect(timestamp, block_idx, block)

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()


def load_template(path, sample_rate=None, chip_rate=None):
    """Load a template (``.npy``) and check which code it holds.

    A template generated for a different sample rate, or for another
    code or code family than the transmitters send, still "works" -- it
    just correlates poorly and nothing is detected.  So when the sample
    rate is known the template's code is identified
    (:func:`thriftyx.gold.identify`): logged at INFO, so every run
    records which code it searches for, and a warning when no code
    matches at this rate (code lengths double with the register length,
    so a template for half or twice the rate can have a valid length).

    Raises
    ------
    TemplateError
        When *path* is not a 1-D ``.npy`` array.
    """
    try:
        template = np.load(path)
    except (ValueError, EOFError) as exc:
        raise TemplateError(
            "cannot load template {!r}: {}".format(path, exc)) from None
    if getattr(template, 'ndim', None) != 1 or len(template) == 0:
        raise TemplateError(
            "template {!r} is not a 1-D sample array".format(path))
    if chip_rate is None:
        chip_def = settings_module.DEFINITIONS['chip_rate']
        chip_rate = chip_def.parser(chip_def.default)
    if sample_rate:
        results = gold.identify(template, sample_rate, chip_rate)
        if gold.is_clear_match(results):
            best = results[0]
            logging.info(
                "template %s holds the %d-bit code %d of the %s family "
                "(correlation %.2f)", path, best['bits'], best['index'],
                best['family'], abs(best['correlation']))
            chips = 2 ** best['bits'] - 1
            if min(best['shift'], chips - best['shift']) > 1:
                logging.warning(
                    "template %s starts at chip %d of its code, not chip "
                    "0; a transmission is one code period from chip 0, so "
                    "the correlation peak splits", path, best['shift'])
        else:
            logging.warning(
                "template %s (%d samples) matches no code at %gM sps and "
                "%g chips/s. Was it generated for a different sample rate "
                "(thriftyx template_generate --sample-rate ...), or is it "
                "not a single transmitter's code? Check it with `thriftyx "
                "gold --identify %s --sample-rate %gM`.", path,
                len(template), sample_rate / 1e6, chip_rate, path,
                sample_rate / 1e6)
    return template


def _block_length_message(block_idx, actual, expected):
    """Explain a block whose length does not match the detector's."""
    msg = (f"block {block_idx} holds {actual} samples but the detector "
           f"is configured for block_size={expected}.")
    if actual == 2 * expected:
        msg += (" Exactly twice the expected length is what a 12-bit "
                "card looks like when its '#v2' header is missing and it "
                "is decoded as 8-bit; set --bit-depth 12.")
    else:
        msg += (" Headerless (v1) cards do not record their block "
                "geometry; set --block-size and --history to the values "
                "used for the capture (--device-type rtlsdr for "
                "original RTL-SDR cards, or --bit-depth 12 if it is an "
                "Airspy card that lost its '#v2' header).")
    return msg


def open_card(stream, config):
    """Open a .card input, adopting the capture settings in its header.

    The ``#v2`` header records the capture's sample rate, block
    geometry and bit depth; these override *config* (with a warning when
    they contradict an explicit setting), so a card can be processed
    without a config file that repeats how it was captured.

    Headerless (v1) cards are the original 8-bit RTL-SDR format; they
    decode as 8-bit unless ``bit_depth`` was set explicitly.

    Returns
    -------
    blocks : iterator
        As yielded by :func:`thriftyx.block_data.card_reader`.
    config : Namespace
        *config* with the header's values applied.
    """
    header, stream = peek_card_header(stream)
    config = settings_module.apply_card_header(config, header)
    explicit = getattr(config, 'explicit_keys', frozenset())
    fallback_bit_depth = (config['bit_depth']
                          if 'bit_depth' in config and 'bit_depth' in explicit
                          else None)
    blocks = card_reader(stream, bit_depth=fallback_bit_depth,
                         expected_sample_rate=config.get('sample_rate'))
    return blocks, config


def _carrier_freq(carrier_info, block_len, sample_rate):
    """Convert carrier bin and offset to frequency value in Hertz."""
    bin_freq = sample_rate / block_len
    idx = util.fft_bin(carrier_info.bin, block_len)
    pos = idx + carrier_info.offset
    freq = pos * bin_freq
    return freq


class SummaryLineFormatter:
    """Generate a one-line summary of a detection."""
    def __init__(self, sample_rate, block_len, add_dt=False):
        self.sample_rate = sample_rate
        self.block_len = block_len
        self.add_dt = add_dt
        if add_dt:
            # Store previous SoAs for different frequency bins to output time
            # interval between subsequent transmissions from the same
            # transmitter.
            self.prev_soas = {}

    def __call__(self, detected, result):
        """Summarize detection results."""
        # if self.add_dt:
        #     # Calculate time interval between subsequent transmissions
        #     dt_idx = result.carrier_info.bin // 2
        #     prev_soa = self.prev_soas.get(dt_idx, result.soa)
        #     if detected:
        #         self.prev_soas[dt_idx] = result.soa
        #     time_diff = (result.soa - prev_soa) / self.sample_rate
        #     time_diff_str = " (+{:.1f}s)".format(time_diff)
        # else:
        #     time_diff_str = ""
        time_diff_str = ""

        carrier_detect = result.corr_info is not None
        carrier_freq = _carrier_freq(result.carrier_info,
                                     self.block_len,
                                     self.sample_rate)
        snr = util.snr(result.carrier_info.energy, result.carrier_info.noise)
        info = ("blk={blk}; carrier: {det} @ {freq:.3f} kHz"
                " / {idx:>3.0f}:{offset:+.2f}, "
                "SNR = {ampl:>4.0f} / {noise:>2.0f} = {snr:>5.2f} dB"
                .format(blk=result.block,
                        det="yes" if carrier_detect else "no ",
                        freq=carrier_freq / 1e3,
                        idx=result.carrier_info.bin,
                        offset=result.carrier_info.offset,
                        ampl=result.carrier_info.energy,
                        noise=result.carrier_info.noise,
                        snr=snr))

        if carrier_detect:
            snr = util.snr(result.corr_info.energy, result.corr_info.noise)
            info += ("; corr: {det} @ {idx:>4}{offset:+.3f}{dt}"
                     ", SNR = {ampl:>4.0f}/{noise:>2.0f} = {snr:>5.2f} dB"
                     .format(det="yes" if detected else "no ",
                             idx=result.corr_info.sample,
                             offset=result.corr_info.offset,
                             dt=time_diff_str,
                             ampl=result.corr_info.energy,
                             noise=result.corr_info.noise,
                             snr=snr))

        return info


def detector_cli(detector_class, parser=None, extra_args=None):
    # pylint: disable=too-many-locals
    if parser is None:
        parser = argparse.ArgumentParser(
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('input',
                        type=argparse.FileType('rb'), default='-',
                        help="input data ('-' streams from stdin)")
    parser.add_argument('--raw', dest='raw', action='store_true',
                        help="input data is raw binary data")
    parser.add_argument('--quiet', dest='quiet', action='store_true',
                        help="do not write anything to standard output")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-o', '--output', dest='output',
                       type=argparse.FileType('w'),
                       help="Output file (.toad) ('-' for stdout)")
    group.add_argument('-a', '--append', dest='append',
                       type=argparse.FileType('a'),
                       help="Output file to append to (.toad)")

    setting_keys = ['device_type', 'sample_rate', 'block_size', 'block_history',
                    'carrier_window', 'carrier_threshold',
                    'corr_threshold', 'template', 'rxid',
                    'bit_depth', 'freq_shift_method', 'soa_interpolation',
                    'chip_rate']
    config, args = load_args(parser, setting_keys)

    kwargs = {}
    if extra_args is not None:
        kwargs = {arg: args[arg] for arg in extra_args}

    output_file = args.output if args.append is None else args.append
    info_out = sys.stderr if output_file == sys.stdout else sys.stdout

    if args.raw:
        blocks = block_reader(args.input, config.block_size,
                              config.block_history,
                              bit_depth=config.bit_depth)
    else:
        blocks, config = open_card(args.input, config)

    bin_freq = config.sample_rate / config.block_size
    window = normalize_freq_range(config.carrier_window, bin_freq)
    template = load_template(config.template, config.sample_rate,
                             config.get('chip_rate'))

    settings = DetectorSettings(block_len=config.block_size,
                                history_len=config.block_history,
                                carrier_len=len(template),
                                carrier_thresh=config.carrier_threshold,
                                carrier_window=window,
                                template=template,
                                corr_thresh=config.corr_threshold,
                                freq_shift_method=config.freq_shift_method,
                                soa_interpolation=config.soa_interpolation)
    detections = detector_class(settings, blocks, rxid=config.rxid, **kwargs)
    summary_liner = SummaryLineFormatter(config.sample_rate,
                                         config.block_size,
                                         add_dt=True)

    carriers = correlated = 0
    for detected, result in detections:
        carriers += result.corr_info is not None
        correlated += bool(detected)
        if detected and output_file is not None:
            print(result.serialize(), file=output_file)

        if not args.quiet:
            # Output summary line
            print(summary_liner(detected, result), file=info_out)
    _check_yield(carriers, correlated, config.template,
                 getattr(args.input, 'name', None))


# Carrier detections without a single correlation peak, beyond which a
# run is more likely using the wrong template than seeing only noise.
_MISMATCH_CARRIERS = 20


def _check_yield(carriers, correlated, template_path, input_name):
    """Warn when carriers were found but nothing correlated.

    The signature of a template for another code, code family or sample
    rate: the carrier detector does not depend on the code, the
    correlator does.
    """
    if correlated or carriers < _MISMATCH_CARRIERS:
        return
    card = input_name if input_name and not str(input_name).startswith(
        '<') else 'CAPTURE.card'
    logging.warning(
        "%d blocks had a carrier but none correlated with template %s. "
        "Check that it holds the code the transmitters send, at this "
        "sample rate: `thriftyx gold --identify %s` reports the code in "
        "a capture (8- and 10-bit codes exist in two families; see "
        "--family).", carriers, template_path, card)


def _main():
    detector_cli(Detector)


if __name__ == '__main__':
    _main()
