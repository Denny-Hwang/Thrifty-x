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
    ctest --test-dir build --output-on-failure   # correlator, card output and install checks
    sudo cmake --install build
    sudo ldconfig

CI also runs the same tests on an ASan/UBSan build: some out-of-bounds
reads in the correlator only fail a test there.  To do the same, configure
a second build directory with `-DCMAKE_BUILD_TYPE=Debug` and
`-fsanitize=address,undefined` in `CMAKE_CXX_FLAGS`,
`CMAKE_EXE_LINKER_FLAGS` and `CMAKE_SHARED_LINKER_FLAGS`.

The installed `fastdet` finds `libfastdet.so` in `<prefix>/lib` by
itself (its RPATH is `$ORIGIN/../lib`), for any `--prefix`.  `ldconfig`
refreshes the loader cache for other programs that link `libfastdet`;
they can build with `pkg-config --cflags --libs fastdet`.
`libfastdet.so` carries the whole fastcapture library, so what the
installed headers declare (`CorrDetector`, `CarrierDetector` and the
`fargs_new` / `fargs_parse_opt` / `fargs_finalize` that set up its
`fargs_t`) links from `-lfastdet` alone; the `install` test builds and
runs such a program.


### Usage
Refer to `fastdet --help`.

Examples:

 - Read samples directly from an Airspy (`-i airspy`), discard blocks whose carrier-peak SNR is below 12, cross-correlate with `template.tpl`, trigger a detection when the correlation-peak SNR exceeds 14, and write detections to `rx.toad`:

    fastdet -i airspy -z template.tpl -t 12s -u 14s -o rx.toad

 - Also write the detected blocks to a `.card` file (e.g. for `thriftyx analyze_detect`):

    fastdet -i airspy -z template.tpl -o rx.toad -x rx.card

 - Status lines go to stdout, or to stderr when `-o -` or `-x -` puts
   the `.toad` or the card there, so a piped card stays readable (only
   one of `-o` and `-x` may be `-`):

    fastdet -i airspy -z template.tpl -o rx.toad -x - | thriftyx detect - -z template.npy

 - Read raw interleaved int16 I/Q from a file and print detections without writing a `.toad` file:

    fastdet -i data.bin -z template.tpl

 - Read samples from a `.card` file, output detections to a `.toad` file:

    fastdet --card -i rx.card -o rx.toad

   The card's header decides the geometry: `-b`/`-h` must match what it
   records (fastdet says what to rerun with), and a card that records
   no history (Python cards from before it was recorded) needs `-h`.
   Every block is read (`-k` does not apply), and a card written with
   `-x` records the replayed card's sample rate.
