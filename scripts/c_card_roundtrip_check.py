#!/usr/bin/env python3
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Functional round-trip check for the C `fastcapture` binary.

Verifies the int16 (v2 card) data path end-to-end, guarding against the
class of bugs where the C file readers still used the legacy fastcard
8-bit layout (half-sized reads that corrupt every block):

1. raw int16 I/Q file -> fastcapture -> .card: every block after the
   skipped one must be emitted and exactly match the corresponding int16
   samples of the input stream, including the history overlap between
   consecutive blocks.  The header records the -s rate given.
2. .card -> fastcapture --card -> .card, without -k: the C card reader
   must re-read fastcapture's own output and skip none of it (a card
   input overrides the default -k 1); the re-emitted card must have the
   same blocks, byte-identical, and the same #v2 header (sample rate and
   geometry passed on).
3. A card whose header records no block history (Python cards before
   it was recorded) is refused without -h -- before any header naming
   a guessed history is written -- and replayed with it.

Standard library only (runs in the CI C-build job without numpy).

Usage: c_card_roundtrip_check.py --binary path/to/fastcapture
Exits non-zero with a diagnostic on any mismatch.
"""

import argparse
import base64
import random
import os
import struct
import subprocess
import sys
import tempfile

BLOCK_LEN = 1024      # I/Q pairs per block
HISTORY = 256         # I/Q pairs carried over between blocks
NUM_BLOCKS = 8        # blocks of *new* data to generate
SKIP = 1              # blocks fastcapture skips (its default); the
                      # emitted indices restart at 0 after the skip
RATE = 3000000        # -s given to pass 1, recorded in its header
OLD_BLOCK = 16384     # pass 3: a thriftyx capture card at RATE from
OLD_HISTORY = 4920    # before cards recorded the history it used


def generate_raw(path):
    """Write seeded pseudorandom int16 I/Q samples.

    Aperiodic on purpose: every block must then be unique, so a
    misaligned or half-sized read cannot accidentally match the
    expected samples (a pure carrier tone is periodic and would).
    Broadband noise trips the 1c0s carrier threshold in every block.
    """
    new_len = BLOCK_LEN - HISTORY
    total_pairs = new_len * (NUM_BLOCKS + SKIP)
    rng = random.Random(20260828)
    values = [rng.randint(-2048, 2047) for _ in range(total_pairs * 2)]
    with open(path, 'wb') as f:
        f.write(struct.pack(f'<{len(values)}h', *values))
    return values  # flat interleaved int16 list


def write_old_card(path, num_blocks=3):
    """Write a card whose #v2 header records no block_history.

    Returns {index: bytes} of its blocks (seeded noise, like
    generate_raw, so each trips the 1c0s carrier threshold).
    """
    rng = random.Random(20260924)
    blocks = {}
    with open(path, 'w') as f:
        f.write(f"#v2 bit_depth=12 sample_rate={RATE} endian=little "
                f"block_size={OLD_BLOCK}\n")
        for index in range(num_blocks):
            values = [rng.randint(-2048, 2047) for _ in range(OLD_BLOCK * 2)]
            payload = struct.pack(f'<{len(values)}h', *values)
            blocks[index] = payload
            encoded = base64.b64encode(payload).decode('ascii')
            f.write(f"1000.{index:06d} {index} {encoded}\n")
    return blocks


def parse_card(path):
    """Return (header_line, {index: bytes}) for a v2 card file."""
    header = None
    blocks = {}
    with open(path, 'rb') as f:
        for line in f:
            text = line.decode('ascii').rstrip('\n')
            if text.startswith('#'):
                if text.startswith('#v2 '):
                    header = text
                continue
            if not text:
                continue
            timestamp, index, payload = text.split(' ')
            blocks[int(index)] = base64.b64decode(payload)
    return header, blocks


def expected_block(raw_values, index):
    """Interleaved int16 bytes the block with this index must contain.

    fastcapture reads `new_len` fresh pairs per block and prepends the
    last HISTORY pairs of the previous block.  Emitted indices restart
    at 0 after the SKIP skipped blocks, so emitted block `index` covers
    pairs [(index+SKIP)*new_len - HISTORY, (index+SKIP+1)*new_len).
    """
    new_len = BLOCK_LEN - HISTORY
    start_pair = (index + SKIP) * new_len - HISTORY
    end_pair = (index + SKIP + 1) * new_len
    vals = raw_values[start_pair * 2:end_pair * 2]
    return struct.pack(f'<{len(vals)}h', *vals)


def run(binary, args, workdir, block=BLOCK_LEN, history=HISTORY,
        check=True):
    """Run fastcapture with -b *block* and -h *history* (unless None)."""
    cmd = [binary, '-b', str(block), '-t', '1c0s', '-q'] + args
    if history is not None:
        cmd += ['-h', str(history)]
    res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if check and res.returncode != 0:
        fail(f"{' '.join(cmd)} exited {res.returncode}:\n"
             f"{res.stdout}\n{res.stderr}")
    return res


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True,
                        help='path to the built fastcapture binary')
    opts = parser.parse_args()
    binary = os.path.abspath(opts.binary)

    with tempfile.TemporaryDirectory() as workdir:
        raw_path = os.path.join(workdir, 'input.bin')
        card1 = os.path.join(workdir, 'pass1.card')
        card2 = os.path.join(workdir, 'pass2.card')

        raw_values = generate_raw(raw_path)

        # Pass 1: raw int16 -> card (default skip=SKIP discards the
        # first block, whose history region is not seeded from the
        # file; emitted indices restart at 0 afterwards).
        run(binary, ['-i', raw_path, '-o', card1, '-s', str(RATE)],
            workdir)
        header, blocks = parse_card(card1)

        if header is None or 'bit_depth=12' not in header:
            fail(f"pass 1 card lacks a '#v2 bit_depth=12' header: {header!r}")
        for field in (f'sample_rate={RATE}', f'block_size={BLOCK_LEN}',
                      f'block_history={HISTORY}'):
            if field not in header.split():
                fail(f"pass 1 header does not record {field}: {header!r}")
        if sorted(blocks) != list(range(NUM_BLOCKS)):
            fail(f"pass 1 emitted blocks {sorted(blocks)}, expected "
                 f"0-{NUM_BLOCKS - 1} (carrier not detected, or a "
                 f"skip/index regression)")

        block_bytes = BLOCK_LEN * 2 * 2  # pairs * 2 int16 * 2 bytes
        for index, payload in sorted(blocks.items()):
            if len(payload) != block_bytes:
                fail(f"block {index}: decoded {len(payload)} bytes, "
                     f"expected {block_bytes} (int16 layout regression)")
            expect = expected_block(raw_values, index)
            if payload != expect:
                fail(f"block {index}: samples differ from the input stream "
                     f"(history overlap or read-size regression)")
        print(f"pass 1 OK: {len(blocks)} blocks, "
              f"{block_bytes} bytes each, samples exact")

        # Pass 2: card -> card via the C card reader, with the default
        # -k: a card input must skip nothing (the card already excludes
        # the unseeded block), and no -s: the rate comes from the card.
        run(binary, ['--card', '-i', card1, '-o', card2], workdir)
        check_replay("pass 2", card2, header, blocks)

        # Pass 3: a card whose header records no history, as thriftyx
        # capture wrote them before it did.  Refused without -h (with
        # the history capture used then as the hint), before a header
        # with a guessed history is written; replayed with -h, which the
        # re-emitted header then records.
        card3_in = os.path.join(workdir, 'nohistory.card')
        card3 = os.path.join(workdir, 'pass3.card')
        old_blocks = write_old_card(card3_in)
        res = run(binary, ['--card', '-i', card3_in, '-o', card3], workdir,
                  block=OLD_BLOCK, history=None, check=False)
        if res.returncode == 0 or f'-h {OLD_HISTORY}' not in res.stderr:
            fail(f"a card that records no history was not refused with "
                 f"a hint of -h {OLD_HISTORY} (exit {res.returncode}):\n"
                 f"{res.stderr}")
        if os.path.exists(card3) and parse_card(card3) != (None, {}):
            fail(f"the refused replay wrote to its card: "
                 f"{parse_card(card3)[0]!r}")
        run(binary, ['--card', '-i', card3_in, '-o', card3], workdir,
            block=OLD_BLOCK, history=OLD_HISTORY)
        check_replay("pass 3", card3,
                     f"#v2 bit_depth=12 sample_rate={RATE} endian=little "
                     f"block_size={OLD_BLOCK} block_history={OLD_HISTORY}",
                     old_blocks)

    print("C card round-trip check PASSED")


def check_replay(name, path, header, blocks):
    """The card at *path* re-emits *blocks* under the same *header*."""
    header2, blocks2 = parse_card(path)
    if header2 != header:
        fail(f"{name} header {header2!r} differs from {header!r} (the "
             f"rate or geometry of the card replayed is not passed on)")
    if set(blocks2) != set(blocks):
        fail(f"{name} emitted blocks {sorted(blocks2)}, expected "
             f"{sorted(blocks)}: the C card reader skipped or invented "
             f"blocks")
    for index, payload in sorted(blocks2.items()):
        if payload != blocks[index]:
            fail(f"block {index}: {name} payload differs from pass 1")
    print(f"{name} OK: {len(blocks2)}/{len(blocks)} blocks re-read "
          f"byte-identically, header kept")


if __name__ == '__main__':
    main()
