cmake_minimum_required(VERSION 3.5)
project(fastcapture C)

set(VERSION_INFO_MAJOR_VERSION 0)
set(VERSION_INFO_MINOR_VERSION 2)

set(CMAKE_C_STANDARD 11)

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
    ${VOLK_LIBRARIES}
    Threads::Threads
    m
)

# Main fastcapture binary
add_executable(fastcapture fastcard_cli.c)
target_link_libraries(fastcapture fastcapture_lib)

# Installation
install(TARGETS fastcapture DESTINATION bin)
install(TARGETS fastcapture_lib DESTINATION lib)
install(FILES fastcard.h DESTINATION include)

# pkg-config file
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/fastcard.pc.in
    ${CMAKE_CURRENT_BINARY_DIR}/fastcard.pc
    @ONLY
)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/fastcard.pc
        DESTINATION lib/pkgconfig)
