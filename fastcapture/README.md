# fastcapture

High-performance carrier detection library for Airspy SDR hardware.

Replaces the original `fastcard` library (RTL-SDR based) with libairspy support.

## Changes from fastcard

| Aspect | fastcard (original) | fastcapture (Thrifty-X) |
|--------|---------------------|-------------------------|
| SDR Library | librtlsdr | libairspy |
| Sample Type | uint8 (8-bit unsigned) | int16 I/Q from libairspy (ADC full scale ≈ ±16384) |
| Sample Conversion | `(val - 127.4) / 128.0` | `val / 16384.0`: ADC full scale maps to 1.0 on both (see `rawconv.c`) |
| DC Offset | Yes (127.4 subtraction) | No (Airspy has none) |
| Gain Control | Single tuner_gain | LNA + Mixer + VGA (3-stage) |
| Bias Tee | Not supported | Supported |
| Max Sample Rate | ~2.4 MSPS | 3/6 MSPS (Mini), 2.5/10 MSPS (R2) |
| Sample ring buffer | 32 MiB fixed | max(1 s of samples, 32 MiB); libairspy delivers 256 KiB per USB transfer |
| Streaming starts | `reader_start` | `reader_start`, after FFT planning and signal-handler setup |
| Block timestamp | Arrival of the block's last sample (stamped in the USB callback) | Same: per-transfer arrival times are recorded in the callback (`stamp_queue.c`), not the time the block leaves the ring |
| Ctrl-C / SIGTERM | Clean stop | Clean stop: exit status 0 and capture statistics printed |

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
cmake -S . -B build
cmake --build build -j
ctest --test-dir build --output-on-failure   # ring-buffer and timestamp unit tests
sudo cmake --install build
```

Configuration fails if libairspy, FFTW3f or volk are not found by
pkg-config.

## .card File Format

fastcapture writes v2 .card format with metadata header:
```
#v2 bit_depth=12 sample_rate=6000000 endian=little block_size=32768 block_history=12278
<timestamp> <block_idx> <base64-encoded int16 I/Q data>
```

`sample_rate=0` means the rate is unknown (file input); readers ignore it.

v1 .card files (uint8 from original Thrifty/fastcard) are still readable
by the Python thriftyx package for backward compatibility.
