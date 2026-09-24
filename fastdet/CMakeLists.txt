##
# Fastdet CMake file
#
# This is a mess, I know.
##

cmake_minimum_required (VERSION 3.10)
project (fastdet)
enable_language(CXX)

# show all errors
add_definitions(-Wall)
add_definitions(-Wextra)
add_definitions(-Wno-unused-parameter)
add_definitions(-Wno-unused)
add_definitions(-Wsign-compare)

# use GNU C++17 (required for VOLK headers with hex-float literals)
add_definitions(-std=gnu++17)

# the version number
set(VERSION_INFO_MAJOR_VERSION 0)
set(VERSION_INFO_MINOR_VERSION 10)

# select the release build type by default to get optimization flags
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release")
    message(STATUS "Build type not specified: defaulting to release.")
endif()
set(CMAKE_BUILD_TYPE ${CMAKE_BUILD_TYPE} CACHE STRING "")

# find dependencies
list(APPEND CMAKE_MODULE_PATH ${CMAKE_SOURCE_DIR}/cmake/Modules)
find_package(PkgConfig)
find_package(Fastcard)
find_package(Volk)

# fastcapture is a STATIC archive, so its own dependencies (libairspy,
# fftw3f) must be linked explicitly by every consumer.
pkg_check_modules(AIRSPY REQUIRED libairspy)
pkg_check_modules(FFTW3F REQUIRED fftw3f)

if(NOT FASTCARD_FOUND)
    message(FATAL_ERROR "Fastcard required to compile fastdet")
endif()

if(NOT VOLK_FOUND)
    message(FATAL_ERROR "volk required to compile fastdet")
endif()

include_directories(
    ${FASTCARD_INCLUDE_DIRS}
    ${VOLK_INCLUDE_DIRS}
    ${AIRSPY_INCLUDE_DIRS}
    ${FFTW3F_INCLUDE_DIRS}
)

# configure a header file to pass some of the CMake settings to the source code
configure_file (
  "${PROJECT_SOURCE_DIR}/configuration.h.in"
  "${PROJECT_BINARY_DIR}/configuration.h"
)

# add the binary tree to the search path for include files so that we will find
# configuration.h
include_directories("${PROJECT_BINARY_DIR}")

# create PkgConfig file
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/fastdet.pc.in
    ${CMAKE_CURRENT_BINARY_DIR}/fastdet.pc
@ONLY)

# create uninstall target
configure_file(
    ${CMAKE_SOURCE_DIR}/cmake/cmake_uninstall.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/cmake_uninstall.cmake
@ONLY)

add_custom_target(uninstall
    ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_BINARY_DIR}/cmake_uninstall.cmake
)

# add targets
add_library(fastdet
            SHARED
            corr_detector.cpp
            fastcard_wrappers.cpp)

# FIXME: corr_detector should be independent from fastcard
# All of the fastcapture archive goes into libfastdet.so, not only the
# members fastdet's own code calls: the installed headers
# (fastcard_wrappers.h includes fargs.h, fastcard.h, fft.h) declare
# fargs_new, fargs_finalize, ..., and a program linked the way
# fastdet.pc says (-lfastdet) needs them from here -- as fastdet_bin
# does.  Public: volk, which AlignedArray calls inline.
target_link_libraries (fastdet
                       PRIVATE
                       -Wl,--whole-archive
                       ${FASTCARD_LIBRARIES}
                       -Wl,--no-whole-archive
                       ${AIRSPY_LIBRARIES}
                       ${FFTW3F_LIBRARIES}
                       m
                       PUBLIC
                       ${VOLK_LIBRARIES})

add_executable(fastdet_bin fastdet.cpp)
target_link_libraries (fastdet_bin fastdet)

# INSTALL_RPATH: the installed binary finds libfastdet.so next to it
# (<prefix>/lib), for any --prefix.  Without it, a fresh install to
# /usr/local exits 127 until someone runs ldconfig.
set_target_properties(fastdet_bin
  PROPERTIES OUTPUT_NAME fastdet
             INSTALL_RPATH "$ORIGIN/../lib")

# add install targets
install (TARGETS fastdet_bin DESTINATION bin)

install (TARGETS fastdet DESTINATION lib)

install (FILES corr_detector.h
               fastcard_wrappers.h
         DESTINATION include/fastdet)

install(FILES ${CMAKE_CURRENT_BINARY_DIR}/fastdet.pc
         DESTINATION lib/pkgconfig)

# Tests (run with `ctest --test-dir <build-dir>`).  include(CTest)
# defines BUILD_TESTING, ON by default; configure with
# -DBUILD_TESTING=OFF to skip them.
include(CTest)
if(BUILD_TESTING)
    add_executable(test_corr_detector tests/test_corr_detector.cpp)
    target_include_directories(test_corr_detector
                               PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(test_corr_detector fastdet)
    add_test(NAME corr_detector COMMAND test_corr_detector)

    add_executable(test_card_stdout tests/test_card_stdout.cpp)
    add_test(NAME card_stdout
             COMMAND test_card_stdout $<TARGET_FILE:fastdet_bin>)

    # Installs into a scratch prefix under the build tree (no root).
    pkg_get_variable(FASTCAPTURE_PC_DIR fastcapture pcfiledir)
    add_test(NAME install
             COMMAND ${CMAKE_COMMAND}
                     -DBUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
                     -DSTAGE_DIR=${CMAKE_CURRENT_BINARY_DIR}/test-install
                     -DPKG_CONFIG=${PKG_CONFIG_EXECUTABLE}
                     -DFASTCAPTURE_PC_DIR=${FASTCAPTURE_PC_DIR}
                     -DCXX=${CMAKE_CXX_COMPILER}
                     "-DCXX_FLAGS=${CMAKE_CXX_FLAGS}"
                     -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/install_check.cmake)
endif()
