# level_crossing_signal

Flashes two lamps alternately, like a North American railroad grade crossing
signal. Each lamp glows up and fades out the way an incandescent bulb does.

**Board:** Waveshare RP2040-Zero

## Pinout

| GPIO | Function   |
| ---- | ---------- |
| 8    | Left lamp  |
| 14   | Right lamp |

Outputs are active high: a lamp is on when its pin is high. Drive an LED
through a series resistor to ground, or a low-side transistor for anything
bigger. Pins use 20 kHz PWM to set brightness.

## Behavior

- Each lamp flashes 55 times per minute (US standard range is 45 to 65), so
  the pair swaps about every 545 ms.
- The left lamp lights first after power-up.
- Brightness follows a filament model with a 40 ms warm-up and 90 ms
  cool-down.
- A startup line is printed over USB serial.

## Settings

All in the unnamed namespace at the top of `src/main.cc`:

| Constant            | Default | Meaning                                    |
| ------------------- | ------- | ------------------------------------------ |
| `kLeftLampGpio`     | 8       | Left lamp pin                              |
| `kRightLampGpio`    | 14      | Right lamp pin                             |
| `kLampsActiveHigh`  | `true`  | Set `false` if a low pin turns a lamp on   |
| `kFlashesPerMinute` | 55      | Flashes per lamp per minute (45 to 65)     |
| `kLampRiseMs`       | 40      | Warm-up time constant; 0 for instant on    |
| `kLampFallMs`       | 90      | Cool-down time constant; 0 for instant off |

## Code

| File                       | Role                                           |
| -------------------------- | ---------------------------------------------- |
| `src/main.cc`              | Settings and the 200 Hz control loop           |
| `src/crossing_signal.*`    | Which lamp is lit at a given time (pure logic) |
| `src/incandescent_lamp.*`  | Filament glow model (pure logic)               |
| `src/pwm_output.*`         | PWM brightness on one GPIO (hardware)          |

## Build and flash

See `../AGENTS.md` for toolchain setup. Then:

```sh
cmake -S . -B build -G Ninja
ninja -C build
picotool load -v -x -f build/level_crossing_signal.uf2
```
