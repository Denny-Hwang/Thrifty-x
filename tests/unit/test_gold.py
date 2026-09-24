# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for Gold code generation."""

import numpy as np
from thriftyx.gold import gold, TAPS


def test_gold_length():
    """Gold code length should be 2^n - 1."""
    for nbits in [5, 6, 7]:
        if nbits in TAPS:
            seq = gold(nbits, 0)
            assert len(seq) == 2**nbits - 1


def test_gold_values_binary():
    """Gold code should consist of 0s and 1s."""
    seq = gold(5, 0)
    assert set(seq).issubset({0, 1})


def test_gold_different_codes():
    """Different code indices should produce different sequences."""
    seq0 = gold(5, 0)
    seq1 = gold(5, 1)
    assert not np.array_equal(seq0, seq1)


# --- known answers ------------------------------------------------------------

import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from thriftyx.gold import LEGACY_TAPS, identify  # noqa: E402
from thriftyx.template_generate import generate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _periodic_corr(a, b):
    """Periodic cross-correlation of two 0/1 sequences as +/-1 (all lags)."""
    x, y = np.where(a, 1.0, -1.0), np.where(b, 1.0, -1.0)
    return np.rint(np.real(np.fft.ifft(np.fft.fft(x) * np.conj(
        np.fft.fft(y))))).astype(int)


def _bound(bits):
    return 1 + 2 ** ((bits + 2) // 2)


@pytest.mark.parametrize('bits, family', [(b, 'gold') for b in sorted(TAPS)]
                         + [(b, 'legacy') for b in sorted(LEGACY_TAPS)])
@pytest.mark.parametrize('idx', [0, 1])
def test_registers_are_m_sequences(bits, family, idx):
    """Each register is maximal-length: periodic autocorrelation is
    2^n - 1 at lag 0 and -1 at every other lag."""
    code = gold(bits, idx, family)
    corr = _periodic_corr(code, code)
    assert corr[0] == 2 ** bits - 1
    assert set(corr[1:]) == {-1}


@pytest.mark.parametrize('bits', sorted(TAPS))
def test_every_pair_is_preferred(bits):
    """A preferred pair's cross-correlation takes only the values
    {-1, -t, t - 2}, t = 1 + 2^floor((n + 2) / 2): the Gold property.
    (Before this was fixed, the 10-bit pair reached 97.)"""
    t = _bound(bits)
    assert set(_periodic_corr(gold(bits, 0, 'gold'),
                              gold(bits, 1, 'gold'))) <= {-1, -t, t - 2}


@pytest.mark.parametrize('bits', [10, 11])
def test_family_bound_holds_across_codes(bits):
    """Within a Gold family every cross-correlation, and every off-peak
    autocorrelation, stays within t(n) -- checked on a spread of codes."""
    t = _bound(bits)
    indices = list(range(0, 2 ** bits + 1, 2 ** bits // 16))[:17]
    codes = [gold(bits, i, 'gold') for i in indices]
    for i, a in enumerate(codes):
        auto = _periodic_corr(a, a)
        assert max(abs(auto[1:])) <= t
        for b in codes[i + 1:]:
            assert max(abs(_periodic_corr(a, b))) <= t


def test_legacy_ten_bit_family_is_not_gold():
    """The tap set Thrifty used for 10 bits: cross-correlation, and the
    off-peak autocorrelation of its XOR codes, reach 97 (-20.5 dB)
    instead of 65 (-23.9 dB).  Kept only for transmitters programmed
    from it."""
    assert max(abs(_periodic_corr(gold(10, 0, 'legacy'),
                                  gold(10, 1, 'legacy')))) == 97
    code = gold(10, 3, 'legacy')
    assert max(abs(_periodic_corr(code, code)[1:])) > 65


# IS-GPS-200 Table 3-Ia: PRN -> (G2 phase-select taps, G2 delay in chips,
# first 10 chips in octal).
GPS_CA = {
    1: ((2, 6), 5, '1440'), 2: ((3, 7), 6, '1620'),
    3: ((4, 8), 7, '1710'), 4: ((5, 9), 8, '1744'),
    5: ((1, 9), 17, '1133'), 6: ((2, 10), 18, '1455'),
    7: ((1, 8), 139, '1131'), 8: ((2, 9), 140, '1454'),
    9: ((3, 10), 141, '1626'), 10: ((2, 3), 251, '1504'),
    11: ((3, 4), 252, '1642'), 12: ((5, 6), 254, '1750'),
    13: ((6, 7), 255, '1764'), 14: ((7, 8), 256, '1772'),
    15: ((8, 9), 257, '1775'), 16: ((9, 10), 258, '1776'),
    17: ((1, 4), 469, '1156'), 18: ((2, 5), 470, '1467'),
    19: ((3, 6), 471, '1633'), 20: ((4, 7), 472, '1715'),
    21: ((5, 8), 473, '1746'), 22: ((6, 9), 474, '1763'),
    23: ((1, 3), 509, '1063'), 24: ((4, 6), 512, '1706'),
    25: ((5, 7), 513, '1743'), 26: ((6, 8), 514, '1761'),
    27: ((7, 9), 515, '1770'), 28: ((8, 10), 516, '1774'),
    29: ((1, 6), 859, '1127'), 30: ((2, 7), 860, '1453'),
    31: ((3, 8), 861, '1625'), 32: ((4, 9), 862, '1712'),
}


def _gps_ca(prn):
    """Reference C/A generator: two 10-stage registers, all ones, G1
    feedback 3,10, G2 feedback 2,3,6,8,9,10, output G1[10] ^ G2 taps."""
    g1, g2 = [1] * 10, [1] * 10
    (a, b), _, _ = GPS_CA[prn]
    chips = []
    for _ in range(1023):
        chips.append(g1[9] ^ g2[a - 1] ^ g2[b - 1])
        f1 = g1[2] ^ g1[9]
        f2 = g2[1] ^ g2[2] ^ g2[5] ^ g2[7] ^ g2[8] ^ g2[9]
        g1, g2 = [f1] + g1[:9], [f2] + g2[:9]
    return np.array(chips, dtype=bool)


@pytest.mark.parametrize('prn', sorted(GPS_CA))
def test_ten_bit_family_contains_the_gps_ca_codes(prn):
    """External known answer: the reference generator reproduces the
    standard's first chips, and each GPS C/A code is gold(10, 1025 - d)."""
    code = _gps_ca(prn)
    _, delay, octal = GPS_CA[prn]
    first = format(int(octal, 8), '010b')
    assert ''.join(str(int(c)) for c in code[:10]) == first
    np.testing.assert_array_equal(gold(10, 1025 - delay, 'gold'), code)


# SHA-256 of np.packbits(code): the 11-bit codes the upstream Thrifty
# transmitters send (example/template.npy is gold(11, 0)), the legacy
# 8- and 10-bit families, and the lengths both families share must never
# change -- transmitters are programmed from them.  (The 5-9-bit values
# are those of the generator before the families were split.)
_PINNED = {
    (5, 3, 'gold'): '0281fd7202efd813',
    (6, 3, 'gold'): '934a882bb19d37f9',
    (7, 3, 'gold'): '06742324585167f8',
    (9, 3, 'gold'): '0ccc9bda31e1e20e',
    (8, 3, 'legacy'): 'fe7e5a907eef7e84',
    (11, 0, 'gold'): 'd4697ab59743e6c2',
    (11, 1, 'gold'): 'c0372684f1c0a0fe',
    (11, 2, 'gold'): 'b96faad0e409a013',
    (11, 3, 'gold'): '9352bbe881c916d4',
    (10, 0, 'legacy'): '305a8437993f00b6',
    (10, 1, 'legacy'): 'cd67a2ed65025bfa',
    (10, 2, 'legacy'): 'f37a7f7889171bd5',
    (10, 3, 'legacy'): '5078a1d8f7f64873',
}


@pytest.mark.parametrize('key', sorted(_PINNED))
def test_pinned_codes_are_unchanged(key):
    bits, idx, family = key
    digest = hashlib.sha256(
        np.packbits(gold(bits, idx, family)).tobytes()).hexdigest()
    assert digest.startswith(_PINNED[key])


def test_legacy_matches_gold_where_the_taps_did_not_change():
    for bits in (5, 11):
        for idx in (0, 1, 7):
            np.testing.assert_array_equal(gold(bits, idx, 'legacy'),
                                          gold(bits, idx))


@pytest.mark.parametrize('call, message', [
    (lambda: gold(8, 0, 'gold'), 'no Gold codes exist'),
    (lambda: gold(8, 0), 'two families'),
    (lambda: gold(10, 0), 'two families'),
    (lambda: gold(10, 1025, 'gold'), 'out of range'),
    (lambda: gold(10, -1, 'legacy'), 'out of range'),
    (lambda: gold(11, 2049), 'out of range'),
    (lambda: gold(12, 0), 'no code family'),
    (lambda: gold(10, 0, 'gps'), 'unknown code family'),
])
def test_invalid_requests_are_rejected(call, message):
    with pytest.raises(ValueError, match=message):
        call()


# --- identify ------------------------------------------------------------------

def test_shipped_template_is_the_eleven_bit_code_zero():
    """example/template.npy was captured from upstream Thrifty
    transmitters: it holds gold(11, 0), not a 10-bit code."""
    template = np.load(REPO / 'example' / 'template.npy')
    for rate in (2.4e6, None):
        best, runner = identify(template, rate)[:2]
        assert (best['family'], best['bits'], best['index']) == \
            ('gold', 11, 0)
        assert best['correlation'] > 0.85
        assert abs(runner['correlation']) < 0.2


@pytest.mark.parametrize('bits, idx, family, rate', [
    (10, 3, 'gold', 6e6), (10, 3, 'legacy', 6e6), (11, 700, 'gold', 10e6),
    (9, 77, 'gold', 3e6)])
def test_identify_noisy_shifted_templates(bits, idx, family, rate):
    rng = np.random.default_rng(bits * 1000 + idx)
    template = generate(bits, idx, rate / 0.999707e6, family).astype(float)
    template = np.roll(template + rng.normal(0, 0.8, len(template)), 37)
    for given in (rate, None):
        best, runner = identify(template, given)[:2]
        assert (best['family'], best['bits'], best['index']) == \
            (family, bits, idx)
        assert abs(best['correlation']) > 2.5 * abs(runner['correlation'])


# --- identify from a capture, and the command line ----------------------------

def _ook_card(path, bits, idx, family, rate=6e6, block=32768):
    """A 12-bit card of noisy on-off keyed bursts of one code."""
    from thriftyx.block_data import card_writer, write_card_header
    rng = np.random.default_rng(bits * 100 + idx)
    envelope = (generate(bits, idx, rate / 0.999707e6, family) + 1) / 2
    with open(path, 'w') as card:
        write_card_header(card, bit_depth=12, sample_rate=int(rate),
                          block_size=block, block_history=12349)
        for k in range(3):
            start = 3000 + 1000 * k
            signal = np.zeros(block, complex)
            signal[start:start + len(envelope)] = envelope * np.exp(
                2j * np.pi * 30e3 / rate * np.arange(len(envelope)))
            noise = np.array([1, 1j]) @ rng.normal(size=(2, block)) / np.sqrt(2)
            signal = 3 * signal + noise
            card_writer(card, 100.0 + k, k,
                        (0.5 * signal / np.abs(signal).max()).astype(
                            np.complex64), bit_depth=12)


@pytest.mark.parametrize('bits, idx, family', [
    (11, 0, 'gold'), (10, 3, 'legacy'), (10, 3, 'gold')])
def test_identify_card_needs_no_template(tmp_path, bits, idx, family):
    from thriftyx.gold import identify_card, is_clear_match
    path = tmp_path / 'rx0.card'
    _ook_card(path, bits, idx, family)
    with open(path, 'rb') as card:
        results, segment, rate = identify_card(card)
    assert rate == 6e6
    assert len(segment) == int(6e6 / 0.999707e6 * (2 ** bits - 1))
    assert (results[0]['family'], results[0]['bits'],
            results[0]['index']) == (family, bits, idx)
    assert is_clear_match(results)


def test_identify_card_without_a_burst(tmp_path):
    from thriftyx.block_data import card_writer, write_card_header
    from thriftyx.gold import identify_card
    path = tmp_path / 'noise.card'
    rng = np.random.default_rng(3)
    with open(path, 'w') as card:
        write_card_header(card, bit_depth=12, sample_rate=6_000_000,
                          block_size=32768, block_history=12349)
        noise = np.array([1, 1j]) @ rng.normal(size=(2, 32768)) * 0.05
        card_writer(card, 1.0, 0, noise.astype(np.complex64), bit_depth=12)
    with open(path, 'rb') as card:
        assert identify_card(card)[0] == []


def _run(monkeypatch, module, argv):
    import sys
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(SystemExit) as excinfo:
        module._main()
    return excinfo.value.code


def test_gold_cli_identifies_a_card(tmp_path, monkeypatch, capsys):
    from thriftyx import gold as gold_module
    path = tmp_path / 'rx0.card'
    _ook_card(path, 10, 3, 'legacy')
    assert _run(monkeypatch, gold_module,
                ['gold', '--identify', str(path)]) == 0
    out = capsys.readouterr().out
    assert 'best match: 10-bit legacy (not Gold) code 3' in out
    assert 'template_generate 10 3 --family legacy --sample-rate 6M' in out
    assert 'cyclic shift' not in out     # meaningless for a cut burst
    # Restricting the search to the other family finds nothing clear.
    assert _run(monkeypatch, gold_module,
                ['gold', '10', '--family', 'gold', '--identify',
                 str(path)]) == 1
    assert 'no code matches clearly' in capsys.readouterr().out


def test_gold_cli_reads_tpl_and_reports_shift(tmp_path, monkeypatch,
                                              capsys):
    """fastdet nodes keep only template.tpl (an int16 count + float32)."""
    from thriftyx import gold as gold_module
    template = np.roll(generate(11, 9, 6e6 / 0.999707e6, 'gold'), 600)
    path = tmp_path / 'template.tpl'
    with open(path, 'wb') as tpl:
        np.int16(len(template)).tofile(tpl)
        template.astype(np.float32).tofile(tpl)
    assert _run(monkeypatch, gold_module,
                ['gold', '--identify', str(path), '-s', '6M']) == 0
    out = capsys.readouterr().out
    assert 'best match: 11-bit Gold code 9' in out
    assert 'not at chip 0' in out


@pytest.mark.parametrize('argv, message', [
    (['10', '3'], 'two families'),
    (['8', '0', '--family', 'gold'], 'no Gold codes exist'),
    (['11', '2049'], 'out of range'),
])
def test_template_generate_rejects_before_writing(tmp_path, monkeypatch,
                                                  capsys, argv, message):
    from thriftyx import template_generate
    monkeypatch.chdir(tmp_path)
    output = tmp_path / 'template.npy'
    output.write_bytes(b'keep')
    assert _run(monkeypatch, template_generate,
                ['template_generate', *argv, '-o', str(output),
                 '--sample-rate', '6M']) == 2
    assert message in capsys.readouterr().err
    assert output.read_bytes() == b'keep'


def test_template_generate_legacy_is_the_old_output(tmp_path, monkeypatch):
    from thriftyx import template_generate
    monkeypatch.chdir(tmp_path)
    output = tmp_path / 'template.npy'
    _run_ok = ['template_generate', '10', '3', '--family', 'legacy', '-o',
               str(output), '--sample-rate', '6M']
    import sys
    monkeypatch.setattr(sys, 'argv', _run_ok)
    template_generate._main()
    samples = np.load(output)
    assert len(samples) == 6139
    np.testing.assert_array_equal(
        samples, generate(10, 3, 6e6 / 0.999707e6, 'legacy'))


def _stream_card(path, bits, idx, family, rate, amplitude=4.0, seed=0):
    """A card cut from one continuous stream with the default geometry,
    as capture writes it: every block overlaps the next, so each burst
    is whole in one block and cut in its neighbour."""
    from thriftyx.block_data import card_writer, write_card_header
    from thriftyx.settings import compute_block_params
    rng = np.random.default_rng(seed)
    size, history, _ = compute_block_params(rate, 0.999707e6)
    envelope = (generate(bits, idx, rate / 0.999707e6, family) + 1) / 2
    total = 12 * size
    stream = (rng.normal(size=total) + 1j * rng.normal(size=total)) / 2 ** .5
    period = int(0.8 * size)                # bursts land anywhere
    for start in range(int(rng.integers(0, period)), total - len(envelope),
                       period):
        stream[start:start + len(envelope)] += amplitude * envelope * np.exp(
            2j * np.pi * (20e3 / rate * np.arange(len(envelope))
                          + rng.uniform()))
    stream *= 0.5 / np.abs(stream).max()
    step = size - history
    with open(path, 'w') as card:
        write_card_header(card, bit_depth=12, sample_rate=int(rate),
                          block_size=size, block_history=history)
        for k in range((total - size) // step):
            card_writer(card, float(k), k, stream[k * step:k * step + size]
                        .astype(np.complex64), bit_depth=12)


@pytest.mark.parametrize('bits, idx, family, rate, seed', [
    (11, 469, 'gold', 2.5e6, 1), (11, 1284, 'gold', 6e6, 2),
    (10, 37, 'legacy', 6e6, 3), (9, 8, 'gold', 3e6, 4),
    (5, 15, 'gold', 6e6, 5)])
def test_identify_card_ignores_bursts_cut_by_block_edges(
        tmp_path, bits, idx, family, rate, seed):
    """A burst cut by a block edge used to win about half the time and
    name a shorter register length."""
    from thriftyx.gold import identify_card, is_clear_match
    path = tmp_path / 'rx0.card'
    _stream_card(path, bits, idx, family, rate, seed=seed)
    with open(path, 'rb') as card:
        results = identify_card(card)[0]
    assert (results[0]['family'], results[0]['bits'],
            results[0]['index']) == (family, bits, idx)
    assert is_clear_match(results)


@pytest.mark.parametrize('bits, idx, rate', [
    *[(5, i, 2.4e6) for i in range(33)], (5, 13, 2.5e6), (5, 15, 3e6),
    (6, 44, 2.4e6), (6, 3, 6e6)])
def test_short_codes_are_identified(bits, idx, rate):
    """31- and 63-chip codes: one sample per chip middle at ~2.4
    samples per chip, and runner-ups near 0.5."""
    from thriftyx.gold import is_clear_match
    template = generate(bits, idx, rate / 0.999707e6, 'gold').astype(float)
    for given in (rate, None):
        results = identify(template, given)
        assert (results[0]['bits'], results[0]['index']) == (bits, idx)
        assert is_clear_match(results)


def test_shared_codes_belong_to_the_legacy_family_too(monkeypatch, capsys):
    from thriftyx import gold as gold_module
    template = str(REPO / 'example' / 'template.npy')
    assert _run(monkeypatch, gold_module,
                ['gold', '11', '--family', 'legacy', '--identify',
                 template]) == 0
    assert 'best match: 11-bit Gold code 0' in capsys.readouterr().out
    assert _run(monkeypatch, gold_module,
                ['gold', '10', '--family', 'legacy', '--identify',
                 template, '-s', '2.4M']) == 1
    assert 'no code of the requested' in capsys.readouterr().out


@pytest.mark.parametrize('name, content, message', [
    ('bad.npy', b'not an array', 'cannot load template'),
    ('empty.tpl', b'', 'not a .tpl template'),
    ('short.tpl', np.int16(100).tobytes() + b'\0' * 40, 'truncated'),
    ('missing.npy', None, 'No such file'),
])
def test_identify_rejects_bad_files_cleanly(tmp_path, monkeypatch, capsys,
                                            name, content, message):
    from thriftyx import gold as gold_module
    path = tmp_path / name
    if content is not None:
        path.write_bytes(content)
    assert _run(monkeypatch, gold_module,
                ['gold', '--identify', str(path)]) == 2
    assert message in capsys.readouterr().err


def test_detect_reports_its_template_code(tmp_path):
    from thriftyx import detect
    path = tmp_path / 'template.npy'
    np.save(path, generate(10, 3, 6e6 / 0.999707e6, 'legacy'))
    lines = []
    detect.load_template(str(path), 6e6, report=lines.append)
    assert lines == ['template {} holds the 10-bit legacy (not Gold) code '
                     '3 (correlation 1.00)'.format(path)]
