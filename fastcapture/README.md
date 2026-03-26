# fastcapture

High-performance carrier detection library for Airspy SDR hardware.

Replaces the original `fastcard` library (RTL-SDR based) with libairspy support.

## Changes from fastcard

| Aspect | fastcard (original) | fastcapture (Thrifty-X) |
|--------|---------------------|-------------------------|
| SDR Library | librtlsdr | libairspy |
| Sample Type | uint8 (8-bit unsigned) | int16 (12-bit signed) |
| Sample Conversion | `(val - 127.4) / 128.0` | `val / 2048.0` |
| DC Offset | Yes (127.4 subtraction) | No (Airspy has none) |
| Gain Control | Single tuner_gain | LNA + Mixer + VGA (3-stage) |
| Bias Tee | Not supported | Supported |
| Max Sample Rate | ~2.4 MSPS | 3/6 MSPS (Mini), 2.5/10 MSPS (R2) |

## Hardware-Independent Components (unchanged)

These components operate on float FFT data and have no hardware dependency:

- `cardet.c/h` — Carrier detection (Dirichlet kernel-based)
- `fft.c/h` — FFTW3f wrapper (pure math)
- `circbuf.c/h` — Generic circular buffer
- `card_reader.c/h` — Updated to support v2 .card format (int16 base64)

## Dependencies

- [libairspy](https://github.com/airspy/airspyone_host) — Airspy SDR library
- [FFTW3f](http://www.fftw.org/) — Single-precision FFT
- pthreads — POSIX threads

## Building

```bash
mkdir build && cd build
cmake ..
make -j$(nproc)
sudo make install
```

Expected output (without libairspy):
```
-- Could NOT find libairspy (missing: AIRSPY_LIBRARIES)
```
This confirms that CMakeLists.txt correctly references libairspy.

## .card File Format

fastcapture writes v2 .card format with metadata header:
```
#v2 bit_depth=12 sample_rate=6000000
<timestamp> <block_idx> <base64-encoded int16 I/Q data>
```

v1 .card files (uint8 from original Thrifty/fastcard) are still readable
by the Python thriftyx package for backward compatibility.
