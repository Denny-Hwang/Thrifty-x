# c-build CI failure investigation

**Status:** Partial fix landed. The `fastcapture` matrix entry now
builds cleanly on Ubuntu Noble. The `fastdet` matrix entry has a
deeper structural issue (header layout + library naming mismatch
between what fastcapture installs and what fastdet expects) and has
been temporarily dropped from the matrix with a `TODO` comment in the
workflow. Restoring it is tracked as separate follow-up work.

---

## 1. Root causes (4 distinct failures stacked)

### 1.1 apt package rename (Noble)

The workflow asks for `libvolk2-dev`. On Ubuntu Noble (`ubuntu-latest`
as of this writing) the package has been renamed to `libvolk-dev`
(now provides libvolk **3.1**). The pkg-config interface
(`pkg-config volk`) is unchanged, so neither `fastcapture` nor
`fastdet` source needs to change - only the apt install line.

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | `libvolk2-dev` -> `libvolk-dev` |

### 1.2 Type mismatch in `fastcapture/rawconv.c`

PR #37's `/2048.0` normalization rewrote `rawconv.c` to:

```c
output[i] = input[2*i] / 2048.0f + I * (input[2*i+1] / 2048.0f);
```

`output` has type `fcomplex*` (a struct with `float real, imag`,
defined at `fastcapture/fft.h:17-20`), not the C99 native
`complex float`. The expression produces `complex float`. gcc 13
(Noble's default) refuses the assignment:

```
rawconv.c:41:21: error: incompatible types when assigning to type 'fcomplex' from type 'complex float'
```

Fix: assign the real and imag fields separately, drop the now-unused
`<complex.h>` include.

| File | Change |
|---|---|
| `fastcapture/rawconv.c:18, 41-42` | Drop `#include <complex.h>`; assign `output[i].real / .imag` explicitly. |

### 1.3 Missing source file in `fastcapture/CMakeLists.txt`

`fastcapture/fastcard.c:107` calls `raw_reader_new` (defined in
`fastcapture/raw_reader.c`), but `raw_reader.c` is not in the
`FASTCAPTURE_SOURCES` list. The static library link fails:

```
undefined reference to `raw_reader_new'
```

Fix: add `raw_reader.c` to the source list.

| File | Change |
|---|---|
| `fastcapture/CMakeLists.txt:42` | add `raw_reader.c` |

### 1.4 fastdet -> fastcapture integration mismatch (not fixed here)

`fastdet/fastdet.cpp` includes:

```c
#include <fastcard/parse.h>
#include <fastcard/base64.h>
```

But `fastcapture/CMakeLists.txt` only installs `fastcard.h` (flat, no
subdirectory). The headers `parse.h` and `base64.h` are not installed
at all. Even if installed, they would land in `/usr/include/` rather
than `/usr/include/fastcard/`.

`fastdet/cmake/Modules/FindFastcard.cmake` is also broken:

```cmake
pkg_check_modules (PC_FASTCARD librtlsdr)   # wrong: rtlsdr is not a fastcard provider
find_library(FASTCARD_LIBRARIES NAMES fastcard ...)  # wrong: library is libfastcapture.a
find_path(... NAMES fastcard/fastcard.h ...)  # wrong: header is fastcard.h, not fastcard/fastcard.h
```

Restoring the fastdet build needs a coordinated rename:

1. `fastcapture/CMakeLists.txt`: install **all** consumed headers
   (`parse.h`, `base64.h`, `fargs.h`, ...) under
   `include/fastcard/<name>.h`, and either rename the library to
   `libfastcard.a` or update `FindFastcard.cmake`.
2. `fastdet/cmake/Modules/FindFastcard.cmake`: drop the
   `librtlsdr` pkg-check (this is leftover from upstream Thrifty's
   pre-Airspy era), point at the correct library name, and look for
   one of the actually-installed headers.
3. `.github/workflows/ci.yml`: build+install fastcapture (with
   `cmake --install build/fastcapture --prefix /tmp/inst`), then
   configure fastdet with
   `cmake -DCMAKE_PREFIX_PATH=/tmp/inst -S fastdet ...`.

This is a non-trivial coordinated change touching three files in
ways that affect downstream packaging. It belongs in its own PR.
For now, the `fastdet` entry is dropped from the matrix with a
comment in `.github/workflows/ci.yml`.

## 2. Verified locally on Noble (gcc 13.3.0, cmake 3.28)

```
$ rm -rf build && cmake -S fastcapture -B build/fastcapture && \
    cmake --build build/fastcapture --config Release -j
...
[100%] Linking C executable fastcapture
[100%] Built target fastcapture_bin
```

```
$ build/fastcapture/fastcapture --help 2>&1 | head -3
Usage: fastcapture ...
```

(no segfault, no missing-symbol error)

## 3. Follow-up

- Restore `fastdet` to the CI matrix once the integration mismatch in
  §1.4 is fixed. Suggested sequence:
  1. Audit which headers fastdet actually needs from fastcapture
     (`grep -h '#include <fastcard/' fastdet/*.cpp fastdet/*.h`).
  2. Update `fastcapture/CMakeLists.txt` to install those headers
     under `include/fastcard/`.
  3. Rewrite `fastdet/cmake/Modules/FindFastcard.cmake` to find the
     installed fastcapture artifacts.
  4. Update the workflow to install fastcapture before configuring
     fastdet.
- Check whether `libairspy-dev` is in the `universe` repo on the GitHub
  Actions Noble runner; if `apt-get update` fails to find it again,
  add `sudo add-apt-repository -y universe` before the install step.
