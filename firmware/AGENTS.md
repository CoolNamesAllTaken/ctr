# Firmware conventions

Every app under `firmware/` should look like it was written by the same
person. Follow these notes when you add or change firmware for any board.
`level_crossing_signal/` is the reference example; copy its layout.

## Stack

- **Language:** C++17. Use C only when an SDK API forces it.
- **SDK:** Raspberry Pi Pico SDK (2.x), built with CMake and Ninja. No Arduino,
  no MicroPython, no PlatformIO.
- **Style:** [Google C++ Style Guide](https://google.github.io/styleguide/cppguide.html).
  `firmware/.clang-format` enforces formatting; run
  `clang-format -i src/*.cc src/*.h` if clang-format is installed.
- **Compiler warnings:** build with `-Wall -Wextra` and leave zero warnings.

## Layout of an app

```
firmware/
  cmake/pico_sdk_import.cmake   # Shared by all apps. Do not copy per app.
  <app_name>/
    CMakeLists.txt
    README.md                   # What it does, pinout, build and flash steps.
    src/
      main.cc                   # Config constants, object wiring, main loop.
      <thing>.h / <thing>.cc    # One class per file pair.
```

- Directory, file and target names are `lower_snake_case`, and the CMake
  project name, executable name and C++ namespace all equal `<app_name>`.
- Set `PICO_BOARD` in `CMakeLists.txt` before including the SDK import.
  Use an SDK board header when one exists (for example
  `waveshare_rp2040_zero`).
- Add the app root to the include path and write project includes as
  `#include "src/foo.h"`.
- Build output goes in `<app_name>/build/`, which is gitignored.

## Google style, applied here

- Files end in `.cc` and `.h`.
- Header guards use the path from the app root:
  `<APP_NAME>_SRC_<FILE>_H_`.
- Include order, each group separated by a blank line: the matching header
  (in `.cc` files), C system headers, C++ standard headers, Pico SDK headers
  (`"pico/..."`, `"hardware/..."`), then project headers (`"src/..."`).
- Put all code in `namespace <app_name>`. Put file-local helpers and
  constants in an unnamed namespace, not `static`.
- Naming: types and functions `CamelCase`, variables `snake_case`, class
  members `snake_case_`, constants and enumerators `kCamelCase`.
- Use `<cstdint>` fixed-width types (`uint32_t`) in our APIs, not the SDK's
  `uint` typedef.
- Mark single-argument constructors `explicit`. Delete copy operations on
  classes that own hardware.
- Comment every header with what the file is for, and every class with what
  it does and a short usage example. Comment non-obvious numbers with where
  they came from (datasheet, standard, measurement).
- Write `/*param=*/` comments for bare literal arguments.

## Architecture

- **Keep logic separate from hardware.** Timing, state machines and signal
  models go in classes that include no SDK headers, so they can be reasoned
  about and tested on a host. Hardware access goes in thin wrapper classes
  (for example `PwmOutput`) that do one thing.
- **`main.cc` holds the configuration.** Pin numbers, rates and tuning values
  are `constexpr` constants at the top of `main.cc`, in an unnamed namespace,
  with `static_assert` on any value that has a valid range.
- **No global objects with constructors.** Build hardware objects as locals
  in a `[[noreturn]] void Run()` function inside the app namespace. `main()`
  only calls `stdio_init_all()` and `Run()`.
- **Fixed-rate loop.** Use `delayed_by_ms()` and `sleep_until()` on an
  absolute deadline so timing does not drift. Avoid `sleep_ms()` in loops.
- **No heap after startup, no exceptions, no RTTI.** The SDK disables
  exceptions and RTTI by default; leave it that way.
- **Logging:** `printf` over USB CDC (`pico_enable_stdio_usb(... 1)`,
  `pico_enable_stdio_uart(... 0)`). USB stdio also lets `picotool -f` reboot
  the board into the bootloader without pressing BOOTSEL.

## Build and flash

The Pico SDK is not vendored in this repo. Point at a local checkout and an
Arm GNU toolchain:

```sh
export PICO_SDK_PATH=~/git-checkouts/pico-docker/pico-sdk
export PICO_TOOLCHAIN_PATH=/Applications/ArmGNUToolchain/14.2.rel1/arm-none-eabi

cd firmware/<app_name>
cmake -S . -B build -G Ninja
ninja -C build
picotool load -v -x -f build/<app_name>.uf2   # -f reboots a running board
```

If the board is not running USB stdio firmware, hold BOOTSEL while plugging
it in, then flash without `-f` (or copy the `.uf2` onto the `RPI-RP2` drive).

## Before calling an app done

1. It builds from a clean `build/` with zero warnings.
2. If the board is attached, it is flashed and `picotool load` reports
   verification OK.
3. Its `README.md` lists the pinout, the behavior and any tunable constants.
