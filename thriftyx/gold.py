# Original work Copyright (C) 2016-2017 Schalk Willem Krüger
# Modified work Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
#
# This file is part of Thrifty-X, a fork of Thrifty
# (https://github.com/swkrueger/Thrifty).
#
# SPDX-License-Identifier: GPL-3.0-only

"""
Generate Gold codes, and identify the code in a template.

A Gold family of register length n holds 2**n + 1 codes of 2**n - 1
chips: the two m-sequences of a *preferred pair* of LFSRs (indices 0 and
1) and their XOR at every relative shift (indices 2 ... 2**n).  Any two
codes of a family periodically cross-correlate at most
t(n) = 1 + 2**floor((n+2)/2) (65 of 1023 or 2047 chips for 10 and 11
bits), and a code's off-peak periodic autocorrelation stays within the
same bound.

A Thrifty transmitter sends one code period as an on-off keyed burst,
which the detector correlates with a +/-1 template: an aperiodic
correlation, whose sidelobes the periodic bound does not limit.  Over
300 codes per family the burst's peak sidelobe (median / worst) is
-19.5 / -13.7 dB for the legacy 10-bit codes, -20.2 / -15.3 dB for the
10-bit Gold codes and -22.8 / -19.7 dB for the 11-bit codes (cross-
correlation between codes is within 1 dB of the same figures).  Longer
codes gain more than a better family does, and within a family the
worst codes are about 5 dB below the median.

Implementation is based on the gold code module in Matthew Baker's Signal
Processing Library (https://mubeta06.github.io/python/sp/) (LGPL license).
"""


import operator

import numpy as np


# Preferred pairs of LFSR taps, in lfsr()'s convention (tap k stands for
# the x^(n-k) term).  tests/unit/test_gold.py checks every pair is
# three-valued.  10 bits is the GPS C/A pair, G1 = 1 + x^3 + x^10 and
# G2 = 1 + x^2 + x^3 + x^6 + x^8 + x^9 + x^10, so gold(10, 1025 - d) is
# GPS PRN code with G2 delay d (an external known answer).  No preferred
# pair exists for 8 bits (n divisible by 4).
TAPS = {
    5: [[2], [1, 2, 3]],
    6: [[5], [1, 4, 5]],
    7: [[4], [4, 5, 6]],
    9: [[5], [3, 5, 6]],
    10: [[7], [1, 2, 4, 7, 8]],
    11: [[9], [3, 6, 9]],
}

# Tap sets Thrifty (and Thrifty-X before this was fixed) used for 8 and
# 10 bits, kept bit-exact for transmitters programmed from that output.
# Each pair is two m-sequences but not a preferred pair: the 10-bit
# family's periodic cross-correlation, and the off-peak periodic
# autocorrelation of all but its two m-sequences, reach 97 (-20.5 dB)
# instead of 65 (-23.9 dB); for single bursts the difference is about
# 1 dB (see the module docstring).  The 8-bit pair is four-valued, at
# most 31.  Every other register length was already a preferred pair
# and is the same in both families.  (The template captured from the
# upstream Thrifty transmitters, example/template.npy, holds the 11-bit
# gold(11, 0); which code a deployed fleet sends is for
# `thriftyx gold --identify` to tell.)
LEGACY_TAPS = {
    8: [[1, 2, 3, 6, 7], [1, 2, 7]],
    10: [[2, 5, 9], [3, 4, 6, 8, 9]],
}

FAMILIES = ('gold', 'legacy')


def taps_for(bits, family=None):
    """Return the LFSR tap pair for *bits* in *family*.

    *family* may be None only for register lengths where both families
    are the same (all but 8 and 10 bits): for those two, a default would
    silently turn an existing transmitter's template into a different
    code.

    Raises
    ------
    ValueError
        For an unknown family or register length, a missing family for 8
        or 10 bits, and 8-bit Gold codes, which cannot exist.
    """
    bits = operator.index(bits)
    if family is None:
        if bits in LEGACY_TAPS:
            raise ValueError(
                "{0}-bit codes exist in two families: the Gold family and "
                "the legacy codes Thrifty generated before, which differ "
                "for 8 and 10 bits. Choose one (--family gold or --family "
                "legacy); `thriftyx gold --identify CAPTURE.card` tells "
                "which one a transmitter sends.".format(bits))
        family = 'gold'
    if family not in FAMILIES:
        raise ValueError("unknown code family {!r} (expected one of: {})"
                         .format(family, ', '.join(FAMILIES)))
    if family == 'legacy' and bits in LEGACY_TAPS:
        return LEGACY_TAPS[bits]
    if bits == 8:
        raise ValueError(
            "no Gold codes exist for an 8-bit register (no preferred pair "
            "exists when n is divisible by 4); the old 8-bit codes are "
            "available with --family legacy")
    if bits not in TAPS:
        raise ValueError("no code family for {}-bit registers (supported: "
                         "{})".format(bits, ', '.join(map(str, sorted(
                             set(TAPS) | set(LEGACY_TAPS))))))
    return TAPS[bits]


def gold(bits, idx, family=None):
    """Generate the idx-th code of length 2^bits - 1.

    Parameters
    ----------
    bits : int
        Length of LFSR. The length of the code will be
        :math:`2^{\\mathtt{bits}} - 1`.
    idx : int
        Index of the code within its family:
        :math:`0 \\le \\mathtt{idx} \\le 2^{\\mathtt{bits}}`.
    family : {'gold', 'legacy'} or None
        'gold': Gold codes from a preferred pair.  'legacy': the 8- and
        10-bit tap sets Thrifty used before (not Gold codes); identical
        to 'gold' for every other length.  Required for 8 and 10 bits
        (see :func:`taps_for`); None means 'gold' otherwise.

    Returns
    -------
    numpy.ndarray of bool
    """
    seq1, seq2 = _registers(bits, family)
    idx = operator.index(idx)
    count = 2 ** operator.index(bits) + 1
    if not 0 <= idx < count:
        raise ValueError("code index {} out of range: {}-bit families have "
                         "codes 0-{}".format(idx, bits, count - 1))
    if idx == 0:
        return seq1
    elif idx == 1:
        return seq2
    else:
        return np.logical_xor(seq1, np.roll(seq2, -idx + 2))


def _registers(bits, family):
    """The two register sequences of a family (all-ones seed)."""
    taps = taps_for(bits, family)
    seed = np.ones(operator.index(bits), dtype=bool)
    return lfsr(taps[0], seed), lfsr(taps[1], seed)


def lfsr(taps, init):
    """Generate a sequence using a linear feedback shift register (LSFR).

    Adapted from https://git.io/vKPF1.

    Parameters
    ----------
    taps: list or np.array
        List of polynomial exponents for non-zero terms other than 1 and n.
    init: list or np.array
        List of buffer initialisation values as 1's and 0's or booleans.

    Returns
    -------
    seq : np.array
    """
    nbits = len(init)
    init = np.array(init, dtype='bool')

    seq_len = (2**nbits) - 1
    seq = np.zeros(seq_len, dtype='bool')
    seq[:len(init)] = init

    for i in range(len(init), seq_len):
        seq[i] = seq[i - len(init)]
        for tap in taps:
            seq[i] ^= seq[i - len(init) + tap]

    return seq


def _decode_chips(template, sps, chips):
    """Chips of a sampled code as +1 / -1 (mean of each chip's middle).

    The chip timing is searched over a fraction of a chip.  Several
    timings can decide every chip equally firmly (at ~2 samples per chip
    a chip's middle is a single sample); the middle of that run is then
    the one aligned with the chips.
    """
    guard = 0.25 * sps
    starts = np.arange(chips) * sps
    phases = np.linspace(-0.5, 0.5, 17) * sps
    scores, decoded = [], []
    for phase in phases:
        lo = np.clip(np.ceil(starts + phase + guard).astype(int),
                     0, len(template) - 1)
        hi = np.floor(starts + phase + sps - guard).astype(int)
        hi = np.clip(np.maximum(hi, lo + 1), 1, len(template))
        means = np.array([template[a:b].mean()
                          for a, b in zip(lo, hi, strict=True)])
        scores.append(np.mean(np.abs(means)))
        decoded.append(np.where(means >= 0, 1.0, -1.0))
    scores = np.array(scores)
    tied = np.flatnonzero(scores >= scores.max() - 1e-9)
    return decoded[int(tied[len(tied) // 2])]


# Relative samples-per-chip errors identify() tries at sample level.
_RATE_ERRORS = (0.0, -3e-4, 3e-4, -6e-4, 6e-4, -1e-3, 1e-3)


def identify(template, sample_rate=None, chip_rate=0.999707e6,
             families=FAMILIES, candidates=3):
    """Find which code a template holds.

    Every code of each fitting register length is ranked by correlation
    with the template's decoded chips; the best *candidates* per family
    are then sampled the way ``template_generate`` does and circularly
    correlated with the template itself -- what the detector's matched
    filter will see -- which gives the reported correlation.

    Parameters
    ----------
    template : array_like
        A sampled code, e.g. from ``template_extract`` (a capture of a
        transmitter) or ``template_generate``.
    sample_rate : float or None
        Sample rate of the template.  When None, every register length
        that puts 1-20 samples on a chip is tried.
    chip_rate : float
        Code chip rate.
    families : iterable of str
        Families to search.
    candidates : int
        Codes per register length and family scored at sample level.

    Returns
    -------
    list of dict
        Scored codes, best first, each with ``family``, ``bits``,
        ``index``, ``shift`` (cyclic, in chips), ``correlation``
        (normalised, -1 ... 1; negative means inverted polarity) and
        ``samples_per_chip``.  A code that is the same in several
        families is listed once, under 'gold'.
    """
    template = np.asarray(template, dtype=float)
    template = template - template.mean()
    size = len(template)
    norm = np.linalg.norm(template)
    results = []
    if norm == 0:
        return results
    spectrum = np.conj(np.fft.fft(template))
    for bits in range(5, 12):
        chips = 2 ** bits - 1
        # A template is one code period, so its length gives the
        # samples per chip -- also for one made at a tuned chip rate.
        sps = size / chips
        if sample_rate:
            if abs(sps / (sample_rate / chip_rate) - 1) > 0.02:
                continue
        elif not 1 <= sps <= 20:
            # Thrifty samples ~1 Mchip/s codes at 1-20 Msps; at far
            # higher oversampling a short code "matches" anything.
            continue
        decoded = np.conj(np.fft.fft(_decode_chips(template, sps, chips)))
        # Sample index -> chip, as template_generate.resample does, for
        # a few chip-rate errors: an extracted template can hold a
        # transmitter whose chip rate differs from the one its length
        # was cut for, which lowers the correlation by ~0.2 per
        # 300 ppm over 2047 chips.
        chip_maps = [(np.arange(size) / (sps * (1 + err))).astype(int)
                     % chips for err in _RATE_ERRORS]
        for family in families:
            if family != 'gold' and bits not in LEGACY_TAPS:
                continue            # same codes as 'gold'
            try:
                seq1, seq2 = _registers(bits, family)
            except ValueError:
                continue            # e.g. 8-bit Gold codes
            one, two = np.where(seq1, 1.0, -1.0), np.where(seq2, 1.0, -1.0)
            # All codes at once: row 0 and 1 are the registers, row i >= 2
            # is their XOR (minus the product in the True -> +1 mapping)
            # at relative shift i - 2.
            shifts = (np.arange(chips)[None, :]
                      + np.arange(chips)[:, None]) % chips
            codes = np.vstack([one, two, -one[None, :] * two[shifts]])
            chip_corr = np.real(np.fft.ifft(np.fft.fft(codes, axis=1)
                                            * decoded, axis=1))
            ranking = np.argsort(-np.max(np.abs(chip_corr), axis=1))
            for idx in ranking[:candidates]:
                best = None
                for err, chip_of in zip(_RATE_ERRORS, chip_maps, strict=True):
                    sampled = codes[idx][chip_of]
                    corr = np.real(np.fft.ifft(np.fft.fft(sampled)
                                               * spectrum))
                    lag = int(np.argmax(np.abs(corr)))
                    value = corr[lag] / norm / np.linalg.norm(sampled)
                    if best is None or abs(value) > abs(best[0]):
                        best = (value, lag, err)
                value, lag, err = best
                results.append({
                    'family': family, 'bits': bits, 'index': int(idx),
                    'shift': int(round(lag / sps)) % chips,
                    'correlation': float(value),
                    'samples_per_chip': float(sps * (1 + err))})
    results.sort(key=lambda r: -abs(r['correlation']))
    return results


def _find_burst(envelope, sps):
    """Locate one complete on-off keyed burst in a block's envelope.

    Returns ``(start, stop, strength)`` -- sample bounds and the burst
    level over the noise level -- or None when the block holds no clear
    burst that starts and ends inside it.
    """
    width = max(int(8 * sps), 1)               # ~8 chips
    smooth = np.convolve(envelope, np.ones(width) / width, mode='same')
    noise = np.median(smooth)
    peak = smooth.max()
    if noise <= 0 or peak < 3 * noise:
        return None
    on = np.flatnonzero(smooth > noise + (peak - noise) / 2)
    start, stop = on[0] - width // 2, on[-1] + width // 2
    if start <= 0 or stop >= len(envelope) - 1:
        return None                             # cut by the block edge
    return int(start), int(stop), float((peak - noise) / noise)


def identify_card(stream, sample_rate=None, chip_rate=0.999707e6,
                  max_blocks=200):
    """Find which code the transmitter in a ``.card`` capture sends.

    Needs no template.  A card holds only blocks in which a carrier was
    detected, and a Thrifty transmitter keys its carrier on and off with
    the code, so the code is the envelope of a burst: the strongest
    complete burst among the first *max_blocks* blocks is cut out at the
    code length that fits its duration and passed to :func:`identify`.

    Returns
    -------
    (results, segment, sample_rate)
        :func:`identify`'s candidate list (empty when no burst was
        found), the burst envelope it was computed from, and the sample
        rate used.
    """
    from thriftyx.block_data import card_reader, peek_card_header

    header, stream = peek_card_header(stream)
    rate = sample_rate or float(header.get('sample_rate') or 0) or None
    if not rate:
        raise ValueError("the card does not record its sample rate; give "
                         "it with --sample-rate")
    bit_depth = int(header['bit_depth']) if 'bit_depth' in header else None
    sps = rate / chip_rate
    best = None
    for count, (_, _, block) in enumerate(
            card_reader(stream, bit_depth=bit_depth)):
        if count >= max_blocks:
            break
        envelope = np.abs(np.asarray(block))
        burst = _find_burst(envelope, sps)
        if burst is not None and (best is None or burst[2] > best[0]):
            best = (burst[2], envelope, burst[0], burst[1])
    if best is None:
        return [], np.array([]), rate
    _, envelope, start, stop = best
    chips = (stop - start) / sps
    bits = min(range(5, 12), key=lambda n: abs(chips / (2 ** n - 1) - 1))
    length = int(sps * (2 ** bits - 1))
    segment = envelope[start:start + length]
    if len(segment) < length:                   # burst estimate ran long
        segment = envelope[len(envelope) - length:]
    return identify(segment, rate, chip_rate), segment, rate


def is_clear_match(results):
    """Whether :func:`identify`'s best candidate stands out.

    A matched template correlates 0.7-1.0 (noise and chip-edge shape
    lower it); every other code of a Gold family stays near 65 / 2047
    ... 0.15.
    """
    if not results:
        return False
    best = abs(results[0]['correlation'])
    runner = abs(results[1]['correlation']) if len(results) > 1 else 0.0
    return best >= 0.5 and best >= 2.5 * runner


def load_template_file(path):
    """Read a template: ``.npy``, or fastdet's ``.tpl`` (an int16 sample
    count followed by float32 samples, see scripts/npy_to_tpl.py)."""
    if str(path).endswith('.tpl'):
        with open(path, 'rb') as tpl:
            count = int(np.fromfile(tpl, dtype=np.int16, count=1)[0])
            return np.fromfile(tpl, dtype=np.float32, count=count)
    return np.load(path)


def _print_identification(path, template, results, sample_rate=None,
                          chip_rate=0.999707e6, from_card=False):
    """Print :func:`identify`'s verdict; True for a clear match.

    *from_card*: *template* is a burst cut from a capture, whose start is
    only known to a few chips, so its cyclic shift means nothing.
    """
    what = "burst" if from_card else "template"
    print("{}: {} of {} samples".format(path, what, len(template)))
    if not results:
        print("  no code found: no complete burst in the card, or no "
              "register length fits this template's length")
        return False
    best = results[0]
    runner = results[1] if len(results) > 1 else None

    def describe(r):
        family = "Gold" if r['family'] == 'gold' else "legacy (not Gold)"
        polarity = ", inverted" if r['correlation'] < 0 else ""
        shift = ("" if from_card
                 else " at cyclic shift {}".format(r['shift']))
        return ("{}-bit {} code {}: correlation {:.3f}{}{} "
                "({:.3f} samples/chip)".format(
                    r['bits'], family, r['index'], abs(r['correlation']),
                    shift, polarity, r['samples_per_chip']))
    print("  best match: " + describe(best))
    if runner is not None:
        print("  runner-up:  " + describe(runner))
    if not is_clear_match(results):
        print("  no code matches clearly: is this a single transmitter's "
              "burst or template, at the given sample rate?")
        return False
    chips = 2 ** best['bits'] - 1
    if not from_card and min(best['shift'], chips - best['shift']) > 1:
        # One burst is one code period from chip 0; a template starting
        # elsewhere splits the correlation peak in two.
        print("  the template starts at chip {} of this code, not at chip "
              "0: extract it again from a capture, or regenerate the code "
              "below".format(best['shift']))
    if sample_rate:
        rate = "{:g}M".format(sample_rate / 1e6)
    else:
        rate = "RATE  (this template: ~{:.2f} Msps)".format(
            best['samples_per_chip'] * chip_rate / 1e6)
    print("  template for this code: thriftyx template_generate {} {} "
          "--family {} --sample-rate {}".format(
              best['bits'], best['index'], best['family'], rate))
    return True


def plot(seq):
    """Plot the autocorrelation of the given sequence."""
    import matplotlib.pyplot as plt

    bipolar = np.where(seq, 1.0, -1.0)
    autocorr = np.correlate(bipolar, bipolar, 'same')

    plt.figure()
    plt.title("Length {} Gold code autocorrelation".format(len(seq)))
    xdata = np.arange(len(seq)) - len(seq) // 2
    plt.plot(xdata, autocorr, '.-')
    plt.show()


def _print_stats(seq):
    bipolar = np.where(seq, 1.0, -1.0)
    autocorr = np.correlate(bipolar, bipolar, 'same')

    peaks = np.sort(np.abs(autocorr))
    peak = peaks[-1]
    noise = np.sqrt(np.mean(peaks[:-1]**2))

    peak_to_peak2 = peak / peaks[-2]
    peak_to_noise = peak / noise

    print("Peak amplitude: {:.0f}".format(peak))
    print("Largest non-peak amplitude: {:.0f}".format(peaks[-2]))
    print("Peak-to-max: {:.2f}".format(peak_to_peak2))
    print("Peak-to-noise: {:.2f}".format(peak_to_noise))


def _main():
    import argparse
    import sys

    from thriftyx.setting_parsers import metric_float

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('length', type=int, nargs='?',
                        help="Register length -- code length will be 2^n-1")
    parser.add_argument('index', nargs='?', type=int, default=0,
                        help="Code index -- which code of the family "
                             "(0 ... 2^n)")
    parser.add_argument('--family', choices=FAMILIES, default=None,
                        help="code family; required for 8 and 10 bits, "
                             "where the Gold codes differ from the legacy "
                             "(non-Gold) codes Thrifty generated before")
    parser.add_argument('-p', '--plot', action='store_true',
                        help="Plot autocorrelation function")
    parser.add_argument('--stats', action='store_true',
                        help="Don't print the sequence, but print some stats "
                             "about the sequence.")
    parser.add_argument('--identify', metavar='FILE',
                        help="report which code a transmitter sends, from a "
                             ".card capture (no template needed) or a "
                             "template (.npy), in either family")
    parser.add_argument('-s', '--sample-rate', type=metric_float,
                        default=None,
                        help="sample rate for --identify [default: the "
                             "card's header; for a template, inferred from "
                             "its length]")
    parser.add_argument('--chip-rate', type=metric_float,
                        default=0.999707e6,
                        help="code chip rate for --identify [default: "
                             "0.999707M]")
    args = parser.parse_args()

    if args.identify:
        rate = args.sample_rate
        if args.identify.endswith(('.npy', '.tpl')):
            template = load_template_file(args.identify)
            results = identify(template, args.sample_rate, args.chip_rate)
        else:
            with open(args.identify, 'rb') as card:
                try:
                    results, template, rate = identify_card(
                        card, args.sample_rate, args.chip_rate)
                except ValueError as exc:
                    parser.error(str(exc))
        if args.length is not None:
            results = [r for r in results if r['bits'] == args.length]
        if args.family is not None:
            results = [r for r in results if r['family'] == args.family]
        sys.exit(0 if _print_identification(
            args.identify, template, results, rate, args.chip_rate,
            from_card=not args.identify.endswith(('.npy', '.tpl')))
            else 1)
    if args.length is None:
        parser.error("the register length is required (or --identify)")

    try:
        seq = gold(args.length, args.index, args.family)
    except ValueError as exc:
        parser.error(str(exc))

    if args.stats:
        _print_stats(seq)
    else:
        print(list(map(int, list(seq))))

    if args.plot:
        plot(seq)


if __name__ == '__main__':
    _main()
