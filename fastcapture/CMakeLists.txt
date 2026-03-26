cmake_minimum_required(VERSION 3.5)
project(fastcapture C)

set(CMAKE_C_STANDARD 11)

find_package(PkgConfig REQUIRED)

# Airspy (replaces RTL-SDR)
pkg_check_modules(AIRSPY REQUIRED libairspy)

# FFTW3f (single-precision)
pkg_check_modules(FFTW3F REQUIRED fftw3f)

# pthreads
find_package(Threads REQUIRED)

include_directories(
    ${AIRSPY_INCLUDE_DIRS}
    ${FFTW3F_INCLUDE_DIRS}
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/lib
)

# Core fastcapture library
set(FASTCAPTURE_SOURCES
    airspy_reader.c    # NEW: replaces rtlsdr_reader.c
    rawconv.c
    cardet.c
    fft.c
    circbuf.c
    reader.c
    card_reader.c
    fargs.c
    parse.c
    lib/base64.c
)

add_library(fastcapture_lib STATIC ${FASTCAPTURE_SOURCES})
target_link_libraries(fastcapture_lib
    ${AIRSPY_LIBRARIES}
    ${FFTW3F_LIBRARIES}
    Threads::Threads
    m
)

# Main fastcapture binary
add_executable(fastcapture fastcard_cli.c)
target_link_libraries(fastcapture fastcapture_lib)

# Installation
install(TARGETS fastcapture DESTINATION bin)
install(FILES fastcapture.h DESTINATION include)
