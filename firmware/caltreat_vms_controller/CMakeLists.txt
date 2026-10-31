cmake_minimum_required(VERSION 3.22)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# Must reach the HUB75 library's own sources, not just main. 5 does not compile in lib 3.1.0 (missing
# CIE LUT). For panels that need it, add PANEL_DRIVER_SHIFTREG here too.
idf_build_set_property(COMPILE_DEFINITIONS "PIXEL_COLOR_DEPTH_BITS=6" APPEND)

project(caltreat_vms_controller)
