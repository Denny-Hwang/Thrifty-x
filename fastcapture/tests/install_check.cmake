# Install fastcapture into STAGE_DIR, a prefix other than the one the
# build was configured with, and check that `pkg-config --cflags --libs
# fastcapture` alone builds a program on the installed headers and
# static library.  libfastcapture.a carries no record of what it links,
# so fastcapture.pc must name all of it (libairspy, volk, fftw3f, libm):
# with those under Requires.private the link failed on airspy_* and
# fftwf_*.
#
# Run by ctest: cmake -DBUILD_DIR=... -DSTAGE_DIR=... -DPKG_CONFIG=...
#   -DCC=... -P install_check.cmake

file(REMOVE_RECURSE "${STAGE_DIR}")
# Every install rule is in the default "Unspecified" component, so this
# installs everything, but writes install_manifest_Unspecified.txt: the
# install_manifest.txt of a real install stays as it is.
execute_process(
    COMMAND "${CMAKE_COMMAND}" "-DCMAKE_INSTALL_PREFIX=${STAGE_DIR}"
            -DCMAKE_INSTALL_COMPONENT=Unspecified
            -P "${BUILD_DIR}/cmake_install.cmake"
    RESULT_VARIABLE rc OUTPUT_QUIET)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "installing into ${STAGE_DIR} failed (${rc})")
endif()

set(ENV{PKG_CONFIG_PATH}
    "${STAGE_DIR}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")
execute_process(
    COMMAND "${PKG_CONFIG}" --cflags --libs fastcapture
    RESULT_VARIABLE rc OUTPUT_VARIABLE flags ERROR_VARIABLE err
    OUTPUT_STRIP_TRAILING_WHITESPACE)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "pkg-config fastcapture failed: ${err}")
endif()
if(NOT flags MATCHES "-L([^ ]+) +-lfastcapture")
    message(FATAL_ERROR "no -L for -lfastcapture in '${flags}'")
endif()
get_filename_component(libdir "${CMAKE_MATCH_1}" REALPATH)
get_filename_component(want "${STAGE_DIR}/lib" REALPATH)
if(NOT libdir STREQUAL want)
    message(FATAL_ERROR "fastcapture.pc points at ${libdir}, not ${want}")
endif()

# Parses arguments, sets up a capture from a file (FFTW plan, volk
# buffers; fastcard.o also pulls in the Airspy reader) and frees it.
file(WRITE "${STAGE_DIR}/consumer.c" [=[
#include <fastcard.h>
#include <fargs.h>

int main(void) {
    char block[] = "1024", history[] = "128";
    fargs_t* args = fargs_new();
    if (args == NULL
            || fargs_parse_opt(args, 'b', block) != 0
            || fargs_parse_opt(args, 'h', history) != 0
            || fargs_finalize(args) != 0) {
        return 1;
    }
    args->input_file = "/dev/null";
    fastcard_t* fc = fastcard_new(args);
    if (fc == NULL) {
        return 2;
    }
    fastcard_free(fc);
    free(args);
    return 0;
}
]=])
separate_arguments(flags UNIX_COMMAND "${flags}")
execute_process(
    COMMAND "${CC}" -o "${STAGE_DIR}/consumer" "${STAGE_DIR}/consumer.c"
            ${flags}
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE out)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "a program on the fastcapture headers does not "
                        "build with `pkg-config --cflags --libs "
                        "fastcapture`:\n${out}")
endif()
execute_process(
    COMMAND "${STAGE_DIR}/consumer"
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE out)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "the program built on fastcapture.pc failed "
                        "(${rc}):\n${out}")
endif()
