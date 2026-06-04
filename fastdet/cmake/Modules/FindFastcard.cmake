find_package(PkgConfig)
pkg_check_modules (PC_FASTCARD fastcapture)

find_path(
    FASTCARD_INCLUDE_DIRS
    NAMES fastcard.h
    HINTS ${PC_FASTCARD_INCLUDE_DIRS} ${PC_FASTCARD_INCLUDEDIR}
    PATHS /usr/include
          /usr/local/include
)

find_library(
    FASTCARD_LIBRARIES
    NAMES fastcapture
    HINTS ${PC_FASTCARD_LIBRARY_DIRS}
    PATHS /usr/lib
          /usr/local/lib
)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(FASTCARD DEFAULT_MSG
                                  FASTCARD_LIBRARIES FASTCARD_INCLUDE_DIRS)

mark_as_advanced(FASTCARD_LIBRARIES FASTCARD_INCLUDE_DIRS)
