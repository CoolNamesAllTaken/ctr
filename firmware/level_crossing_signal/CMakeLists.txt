cmake_minimum_required(VERSION 3.13)

# Board and SDK must be chosen before the SDK is imported.
set(PICO_BOARD waveshare_rp2040_zero CACHE STRING "Target board")
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/pico_sdk_import.cmake)

project(level_crossing_signal C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

pico_sdk_init()

add_executable(level_crossing_signal
  src/crossing_signal.cc
  src/incandescent_lamp.cc
  src/main.cc
  src/pwm_output.cc
)

# Includes are written relative to the app root, e.g. "src/pwm_output.h".
target_include_directories(level_crossing_signal PRIVATE
  ${CMAKE_CURRENT_LIST_DIR}
)

target_compile_options(level_crossing_signal PRIVATE -Wall -Wextra)

target_link_libraries(level_crossing_signal PRIVATE
  hardware_pwm
  pico_stdlib
)

# Logs go to USB CDC so no UART adapter is needed.
pico_enable_stdio_usb(level_crossing_signal 1)
pico_enable_stdio_uart(level_crossing_signal 0)

pico_add_extra_outputs(level_crossing_signal)
