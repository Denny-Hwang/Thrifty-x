#!/usr/bin/env python3
"""Generate a synthetic v2 .card file that mimics the canonical Airspy
INT16_IQ path used by ``thriftyx capture``.

Used by the diagnostic scripts as a *control*: we know the format by
construction, so any FFT / range analysis that disagrees with the
control points at the diagnostic, not the data.

Two variants are written so Phase B/C can compare them side-by-side:

* ``diag/synth_int16_iq.card`` — what the code currently emits.
* ``diag/synth_float32_misread.card`` — float32-IQ raw bytes presented as
  the v2 .card format would *if* libairspy silently fell back to
  FLOAT32_IQ but the HAL still passed the buffer through as int16
  (i.e. scenario 1-bis from Phase A).
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import numpy as np


def _add_workspace_to_path() -> None:
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    sys.path.insert(0, str(repo_root))


def _make_iq(num_samples: int, sample_rate: float,
             carrier_offset_hz: float) -> np.ndarray:
    """Return a complex64 IQ array containing a single tone plus weak
    additive noise — enough SNR to produce a ~40 dB peak when FFT'd."""
    rng = np.random.default_rng(0xC0FFEE)
    t = np.arange(num_samples, dtype=np.float64) / sample_rate
    phasor = np.exp(1j * 2 * np.pi * carrier_offset_hz * t)
    noise = (rng.standard_normal(num_samples)
             + 1j * rng.standard_normal(num_samples)) * 0.01
    iq = (phasor + noise).astype(np.complex64)
    return iq


def main() -> int:
    _add_workspace_to_path()
    from thriftyx.block_data import (write_card_header, complex_to_raw)

    out_dir = Path(__file__).resolve().parent
    sample_rate = 10_000_000           # R2 sample rate
    block_size = 65_536                # bytes_per_int16 sample * 2 (I+Q)
    carrier_offset = 3_050.0           # match the ~3 kHz bin from the prompt

    iq = _make_iq(block_size, sample_rate, carrier_offset)

    # --- 1. Canonical INT16_IQ path -----------------------------------
    int16_card = out_dir / "synth_int16_iq.card"
    with int16_card.open("w") as fh:
        write_card_header(fh, bit_depth=12, sample_rate=sample_rate)
        raw_i16 = complex_to_raw(iq, bit_depth=12)
        encoded = base64.b64encode(raw_i16.tobytes()).decode("ascii")
        fh.write(f"{0.0:.6f} {0} {encoded}\n")
    print(f"Wrote {int16_card} "
          f"(raw bytes: {len(raw_i16)*2}, dtype=int16, "
          f"min={raw_i16.min()}, max={raw_i16.max()})")

    # --- 2. Mis-interpreted FLOAT32_IQ path --------------------------
    # Simulate: libairspy delivered FLOAT32_IQ samples (because
    # set_sample_type silently failed), but the HAL passed the buffer
    # through to disk verbatim and labelled it as int16 in the v2
    # header.  This is exactly Phase A scenario 1-bis.
    f32 = np.empty(block_size * 2, dtype=np.float32)
    f32[0::2] = iq.real
    f32[1::2] = iq.imag
    misread_card = out_dir / "synth_float32_misread.card"
    with misread_card.open("w") as fh:
        write_card_header(fh, bit_depth=12, sample_rate=sample_rate)
        # f32.tobytes() is twice the size of the int16 path on purpose:
        # this is what would land on disk if the byte stream wasn't
        # converted at the HAL boundary.
        encoded = base64.b64encode(f32.tobytes()).decode("ascii")
        fh.write(f"{0.0:.6f} {0} {encoded}\n")
    print(f"Wrote {misread_card} "
          f"(raw bytes: {f32.nbytes}, dtype=float32 presented as int16)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
