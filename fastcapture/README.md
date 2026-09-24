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
| Streaming starts | `reader_start` | `reader_start`, after FFT planning |
| Block timestamp | Arrival of the block's last sample (stamped in the USB callback) | Same: per-transfer arrival times are recorded in the callback (`stamp_queue.c`), not the time the block leaves the ring |
| Ctrl-C / SIGTERM | Clean stop | Clean stop: exit status 0 and capture statistics printed. Signals are taken by a dedicated thread (`sigthread.c`), not a signal handler; a second Ctrl-C exits at once |
| Block geometry default | 16384 / 4920 | Enlarged for `-s` exactly as `thriftyx capture` does, to hold an 11-bit (2047-chip) template + 64 samples: 16384 / 5182 at 2.5M, 16384 / 6206 at 3M, 32768 / 12349 at 6M, 65536 / 20539 at 10M (`-b`/`-h` override) |
| Replaying a `.card` (`fastdet --card`) | Block geometry from the arguments | Refused, with the `-b`/`-h` to rerun with, when the card's `#v2` header (or the `# arguments` line of older cards) records a different geometry: it decides every SoA. A card that records no history (Python cards from before it was recorded, or no header) is refused unless `-h` is given; the message names the history `thriftyx capture` used then for the recorded rate. A card re-emitted from it (`-o`, `fastdet -x`) records the replayed card's sample rate |
| Device unplugged | Hangs | The reader notices within 1 s (`airspy_is_streaming`), or after 10 s without samples, and exits non-zero so a supervisor restarts it |
| Output write fails | — | Full disk or a closed pipe ends the run with an error (SIGPIPE is ignored) |
| Argument checks | — | `-s` must be a rate (1M-10M: libairspy reads values below 100 as a rate *index*), `-f` 24M-1.8G, `-g` 0-14, `-M`/`-V` 0-15, `-b` a power of two up to 65536, `-h` 1-65535, `-k` 0-4294967295; counts take digits only (`-h -1` used to hang, `-k -1` to skip the whole run) |

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
cmake -S . -B build          # Release (optimised) unless CMAKE_BUILD_TYPE is set
cmake --build build -j
ctest --test-dir build --output-on-failure   # ring-buffer, timestamp, argument, card-reader and carrier-search unit tests
sudo cmake --install build
```

Replaying a card through `--card` needs the geometry it was captured
with: pass `-b`/`-h` (see its `#v2` header) when it differs from the
default for `-s`, and `-h` when the header records no history.  A card
input never skips blocks (`-k` does not apply).

Configuration fails if libairspy, FFTW3f or volk are not found by
pkg-config.

## .card File Format

fastcapture writes v2 .card format with metadata header:
```
#v2 bit_depth=12 sample_rate=6000000 endian=little block_size=32768 block_history=12349
<timestamp> <block_idx> <base64-encoded int16 I/Q data>
```

For a file input, `sample_rate` is the rate the replayed card records,
or `-s` when given (a `-s` that disagrees with the card's is refused);
`sample_rate=0` means the rate is unknown, and readers ignore it.

v1 .card files (uint8 from original Thrifty/fastcard) are still readable
by the Python thriftyx package for backward compatibility.
