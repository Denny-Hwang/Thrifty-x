# fastdet: fast detector

C++ carrier + correlation detector.  It links the `fastcapture` static
library for sample input (live Airspy, raw int16 files or `.card`
files) and writes `.toad` detections, optionally exporting detected
blocks as a v2 `.card`.  The Python `thriftyx capture` / `detect` path
is the recommended entry point; fastdet is kept for parity with the
original C pipeline.

## Installation
### Requirements

 - fastcapture (build and install it first, see `fastcapture/README.md`),
   which brings in libairspy, FFTW3f and libvolk
 - CMake

### Building and installing

    cmake -S . -B build
    cmake --build build -j
    sudo cmake --install build


### Usage
Refer to `fastdet --help`.

Examples:

 - Read samples directly from an Airspy (`-i airspy`), discard blocks whose carrier-peak SNR is below 12, cross-correlate with `template.tpl`, trigger a detection when the correlation-peak SNR exceeds 14, and write detections to `rx.toad`:

    fastdet -i airspy -z template.tpl -t 12s -u 14s -o rx.toad

 - Also write the detected blocks to a `.card` file (e.g. for `thriftyx analyze_detect`):

    fastdet -i airspy -z template.tpl -o rx.toad -x rx.card

 - Read raw interleaved int16 I/Q from a file and print detections without writing a `.toad` file:

    fastdet -i data.bin -z template.tpl

 - Read samples from a `.card` file, output detections to a `.toad` file:

    fastdet --card -i rx.card -o rx.toad
