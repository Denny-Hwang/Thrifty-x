# Install fastdet into STAGE_DIR, a prefix other than the one the build
# was configured with, and check what is installed there:
#  - the binary starts without LD_LIBRARY_PATH or an ldconfig run, and
#    loads the libfastdet.so installed next to it;
#  - `pkg-config --cflags --libs fastdet` resolves, points at STAGE_DIR,
#    and is enough to build and run a program on the installed headers
#    (CorrDetector, and CarrierDetector on a fargs_new'd fargs_t).
#
# Run by ctest: cmake -DBUILD_DIR=... -DSTAGE_DIR=... [-DPKG_CONFIG=...
#   -DFASTCAPTURE_PC_DIR=... -DCXX=... -DCXX_FLAGS=...]
#   -P install_check.cmake

file(REMOVE_RECURSE "${STAGE_DIR}")
# Every install rule is in the default "Unspecified" component, so this
# installs everything, but writes install_manifest_Unspecified.txt: the
# install_manifest.txt of a real install (read by `make uninstall`)
# stays as it is.
execute_process(
    COMMAND "${CMAKE_COMMAND}" "-DCMAKE_INSTALL_PREFIX=${STAGE_DIR}"
            -DCMAKE_INSTALL_COMPONENT=Unspecified
            -P "${BUILD_DIR}/cmake_install.cmake"
    RESULT_VARIABLE rc OUTPUT_QUIET)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "installing into ${STAGE_DIR} failed (${rc})")
endif()

unset(ENV{LD_LIBRARY_PATH})
execute_process(
    COMMAND "${STAGE_DIR}/bin/fastdet" --help
    RESULT_VARIABLE rc OUTPUT_QUIET ERROR_VARIABLE err)
if(NOT rc EQUAL 0)
    message(FATAL_ERROR "the installed fastdet does not start (${rc}): "
                        "${err}")
endif()

# glibc's loader lists what it resolved, as ldd does: libfastdet.so must
# come from STAGE_DIR, not from another install the loader cache knows.
set(ENV{LD_TRACE_LOADED_OBJECTS} 1)
execute_process(
    COMMAND "${STAGE_DIR}/bin/fastdet"
    OUTPUT_VARIABLE loaded ERROR_QUIET)
unset(ENV{LD_TRACE_LOADED_OBJECTS})
if(loaded MATCHES "libfastdet\\.so => ([^ \t\n]+)")
    get_filename_component(found "${CMAKE_MATCH_1}" REALPATH)
    get_filename_component(want "${STAGE_DIR}/lib/libfastdet.so" REALPATH)
    if(NOT found STREQUAL want)
        message(FATAL_ERROR "the installed fastdet loads ${found}, "
                            "not ${want}")
    endif()
endif()

if(PKG_CONFIG AND FASTCAPTURE_PC_DIR)
    set(ENV{PKG_CONFIG_PATH}
        "${STAGE_DIR}/lib/pkgconfig:${FASTCAPTURE_PC_DIR}:$ENV{PKG_CONFIG_PATH}")
    execute_process(
        COMMAND "${PKG_CONFIG}" --cflags --libs fastdet
        RESULT_VARIABLE rc OUTPUT_VARIABLE flags ERROR_VARIABLE err
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT rc EQUAL 0)
        message(FATAL_ERROR "pkg-config fastdet failed: ${err}")
    endif()
    if(NOT flags MATCHES "-L([^ ]+) +-lfastdet")
        message(FATAL_ERROR "no -L for -lfastdet in '${flags}'")
    endif()
    get_filename_component(libdir "${CMAKE_MATCH_1}" REALPATH)
    get_filename_component(want "${STAGE_DIR}/lib" REALPATH)
    if(NOT libdir STREQUAL want)
        message(FATAL_ERROR "fastdet.pc points at ${libdir}, not ${want}")
    endif()

    # A program on the installed headers links with those flags alone:
    # AlignedArray calls volk inline, and a dynamic link does not pull
    # in the Requires.private libraries.  CarrierDetector takes a
    # fargs_t, so fargs_new/fargs_finalize (fargs.h, which
    # fastcard_wrappers.h includes) must come from libfastdet.so too.
    # Built with the project's CXX_FLAGS: under the sanitizers the
    # program must load their runtime first.
    if(CXX)
        file(WRITE "${STAGE_DIR}/consumer.cpp" [=[
#include <cstdlib>
#include <fastdet/corr_detector.h>

int main() {
    CorrDetector det(std::vector<float>(31, 1.0f), 256, 31, 0, 15);
    char block[] = "1024", history[] = "128";
    fargs_t* args = fargs_new();
    if (args == NULL
            || fargs_parse_opt(args, 'b', block) != 0
            || fargs_parse_opt(args, 'h', history) != 0
            || fargs_finalize(args) != 0) {
        return 1;
    }
    args->input_file = "/dev/null";
    {
        CarrierDetector carrier(args);
        carrier.start();
        if (carrier.process_next()) {
            return 2;               // /dev/null holds no block
        }
    }
    free(args);
    return 0;
}
]=])
        separate_arguments(flags UNIX_COMMAND "${flags}")
        separate_arguments(cxx_flags UNIX_COMMAND "${CXX_FLAGS}")
        execute_process(
            COMMAND "${CXX}" ${cxx_flags} -std=gnu++17
                    -o "${STAGE_DIR}/consumer"
                    "${STAGE_DIR}/consumer.cpp" ${flags}
            RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE out)
        if(NOT rc EQUAL 0)
            message(FATAL_ERROR "a program on the fastdet headers does "
                                "not build with `pkg-config --cflags "
                                "--libs fastdet`:\n${out}")
        endif()
        execute_process(
            COMMAND "${CMAKE_COMMAND}" -E env
                    "LD_LIBRARY_PATH=${STAGE_DIR}/lib"
                    "${STAGE_DIR}/consumer"
            RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE out)
        if(NOT rc EQUAL 0)
            message(FATAL_ERROR "the program built on fastdet.pc failed "
                                "(${rc}):\n${out}")
        endif()
    endif()
else()
    message(STATUS "pkg-config or fastcapture.pc not found: "
                   "fastdet.pc not checked")
endif()
