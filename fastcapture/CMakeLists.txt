cmake_minimum_required(VERSION 3.10)
project(fastcapture C)

set(VERSION_INFO_MAJOR_VERSION 0)
set(VERSION_INFO_MINOR_VERSION 2)

set(CMAKE_C_STANDARD 11)

# Release (optimised) unless asked otherwise: an unoptimised build keeps
# up with 6-10 Msps poorly on a Pi and drops samples.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    message(STATUS "Build type not specified: defaulting to Release.")
endif()

find_package(PkgConfig REQUIRED)

# Airspy (replaces RTL-SDR)
pkg_check_modules(AIRSPY REQUIRED libairspy)

# FFTW3f (single-precision)
pkg_check_modules(FFTW3F REQUIRED fftw3f)

# Volk (vector-optimized library of kernels)
pkg_check_modules(VOLK REQUIRED volk)

# pthreads
find_package(Threads REQUIRED)

# Generate configuration.h from template
set(USE_FFTW 1)
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/configuration.h.in
    ${CMAKE_CURRENT_BINARY_DIR}/configuration.h
)

include_directories(
    ${AIRSPY_INCLUDE_DIRS}
    ${FFTW3F_INCLUDE_DIRS}
    ${VOLK_INCLUDE_DIRS}
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/lib
    ${CMAKE_CURRENT_BINARY_DIR}
)

# Core fastcapture library
set(FASTCAPTURE_SOURCES
    fastcard.c
    airspy_reader.c
    raw_reader.c
    rawconv.c
    cardet.c
    fft.c
    circbuf.c
    stamp_queue.c
    sigthread.c
    reader.c
    card_reader.c
    fargs.c
    parse.c
    lib/base64.c
)

add_library(fastcapture_lib STATIC ${FASTCAPTURE_SOURCES})
# Output name on disk: libfastcapture.a so the pkg-config "-lfastcapture"
# directive resolves correctly.  (The target *name* stays "fastcapture_lib"
# to disambiguate from the executable target below.)
# POSITION_INDEPENDENT_CODE: fastdet links this static archive into its
# shared library (libfastdet.so), which requires the objects to be -fPIC.
set_target_properties(fastcapture_lib PROPERTIES
    OUTPUT_NAME fastcapture
    POSITION_INDEPENDENT_CODE ON)
target_link_libraries(fastcapture_lib
    ${AIRSPY_LIBRARIES}
    ${FFTW3F_LIBRARIES}
    ${VOLK_LIBRARIES}
    Threads::Threads
    m
)

# Main fastcapture binary
add_executable(fastcapture_bin fastcard_cli.c)
set_target_properties(fastcapture_bin PROPERTIES OUTPUT_NAME fastcapture)
target_link_libraries(fastcapture_bin fastcapture_lib)

# Unit tests (run with `ctest --test-dir <build-dir>`).  include(CTest)
# defines BUILD_TESTING, ON by default; configure with
# -DBUILD_TESTING=OFF to skip them.
include(CTest)
if(BUILD_TESTING)
    add_executable(test_circbuf tests/test_circbuf.c circbuf.c)
    target_link_libraries(test_circbuf Threads::Threads)
    add_test(NAME circbuf COMMAND test_circbuf)
    add_executable(test_stamp_queue tests/test_stamp_queue.c stamp_queue.c)
    target_link_libraries(test_stamp_queue Threads::Threads)
    add_test(NAME stamp_queue COMMAND test_stamp_queue)
    add_executable(test_fargs tests/test_fargs.c fargs.c parse.c)
    add_test(NAME fargs COMMAND test_fargs)
    # A block-size search that never ends fails instead of stalling.
    set_tests_properties(fargs PROPERTIES TIMEOUT 30)
    add_executable(test_card_reader tests/test_card_reader.c card_reader.c
                   reader.c lib/base64.c)
    add_test(NAME card_reader COMMAND test_card_reader)
    add_executable(test_cardet tests/test_cardet.c cardet.c)
    target_link_libraries(test_cardet ${VOLK_LIBRARIES})
    add_test(NAME cardet COMMAND test_cardet)

    # Installs into a scratch prefix under the build tree (no root) and
    # builds a program on the installed headers with fastcapture.pc.
    add_test(NAME install
             COMMAND ${CMAKE_COMMAND}
                     -DBUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
                     -DSTAGE_DIR=${CMAKE_CURRENT_BINARY_DIR}/test-install
                     -DPKG_CONFIG=${PKG_CONFIG_EXECUTABLE}
                     -DCC=${CMAKE_C_COMPILER}
                     "-DC_FLAGS=${CMAKE_C_FLAGS}"
                     -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/install_check.cmake)
endif()

# Installation
install(TARGETS fastcapture_bin DESTINATION bin)
install(TARGETS fastcapture_lib DESTINATION lib)
install(FILES
    fastcard.h
    cardet.h
    rawconv.h
    reader.h
    fft.h
    fargs.h
    fargs_type.h
    parse.h
    sigthread.h
    lib/base64.h
    DESTINATION include)

# pkg-config file.  Threads::Threads' flag (none where libc has them).
set(FASTCAPTURE_PC_THREAD_LIBS "${CMAKE_THREAD_LIBS_INIT}")
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/fastcard.pc.in
    ${CMAKE_CURRENT_BINARY_DIR}/fastcapture.pc
    @ONLY
)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/fastcapture.pc
        DESTINATION lib/pkgconfig)
