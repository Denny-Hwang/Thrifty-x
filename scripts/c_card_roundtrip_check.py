#!/usr/bin/env python3
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Functional round-trip check for the C `fastcapture` binary.

Verifies the int16 (v2 card) data path end-to-end, guarding against the
class of bugs where the C file readers still used the legacy fastcard
8-bit layout (half-sized reads that corrupt every block):

1. raw int16 I/Q file -> fastcapture -> .card: every emitted block must
   exactly match the corresponding int16 samples of the input stream,
   including the history overlap between consecutive blocks.
2. .card -> fastcapture --card -> .card: the C card reader must be able
   to re-read fastcapture's own output; re-emitted blocks must be
   byte-identical to the first pass.

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


def run(binary, args, workdir):
    cmd = [binary, '-b', str(BLOCK_LEN), '-h', str(HISTORY),
           '-t', '1c0s', '-q'] + args
    res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if res.returncode != 0:
        fail(f"{' '.join(cmd)} exited {res.returncode}:\n"
             f"{res.stdout}\n{res.stderr}")


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
        run(binary, ['-i', raw_path, '-o', card1], workdir)
        header, blocks = parse_card(card1)

        if header is None or 'bit_depth=12' not in header:
            fail(f"pass 1 card lacks a '#v2 bit_depth=12' header: {header!r}")
        if len(blocks) < NUM_BLOCKS - 1:
            fail(f"pass 1 emitted only {len(blocks)} blocks "
                 f"(carrier not detected? expected ~{NUM_BLOCKS})")

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

        # Pass 2: card -> card via the C card reader (skip=0: the card
        # already excludes the unseeded block).
        run(binary, ['--card', '-i', card1, '-o', card2, '-k', '0'], workdir)
        header2, blocks2 = parse_card(card2)

        if header2 is None or 'bit_depth=12' not in header2:
            fail(f"pass 2 card lacks a '#v2 bit_depth=12' header: {header2!r}")
        if not blocks2:
            fail("pass 2 emitted no blocks: the C card reader cannot "
                 "re-read fastcapture's own output")
        for index, payload in sorted(blocks2.items()):
            if index not in blocks:
                fail(f"pass 2 emitted unknown block index {index}")
            if payload != blocks[index]:
                fail(f"block {index}: pass 2 payload differs from pass 1")
        print(f"pass 2 OK: {len(blocks2)}/{len(blocks)} blocks re-read "
              f"byte-identically")

    print("C card round-trip check PASSED")


if __name__ == '__main__':
    main()
