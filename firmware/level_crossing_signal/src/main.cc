// Railroad level crossing signal for the Waveshare RP2040-Zero.
//
// Alternately flashes two lamps on GPIO 8 and GPIO 14, with an incandescent
// glow and fade, in the pattern of a North American grade crossing flasher.

#include <cstdint>
#include <cstdio>

#include "pico/stdlib.h"

#include "src/crossing_signal.h"
#include "src/incandescent_lamp.h"
#include "src/pwm_output.h"

namespace level_crossing_signal {

namespace {

// Pin assignments.
constexpr uint32_t kLeftLampGpio = 8;
constexpr uint32_t kRightLampGpio = 14;
// Set to false if the lamps are wired so that a low pin turns them on.
constexpr bool kLampsActiveHigh = true;

// Flash rate per lamp. Must be within the 45-65 range used in the US.
constexpr uint32_t kFlashesPerMinute = 55;
static_assert(kFlashesPerMinute >= 45 && kFlashesPerMinute <= 65,
              "Flash rate is outside the MUTCD range");

// Filament time constants. Set both to 0 for hard on/off switching.
constexpr float kLampRiseMs = 40.0f;
constexpr float kLampFallMs = 90.0f;

// Control loop period. 200 Hz keeps the fades smooth.
constexpr uint32_t kTickMs = 5;

[[noreturn]] void Run() {
  PwmOutput left_output(kLeftLampGpio, kLampsActiveHigh);
  PwmOutput right_output(kRightLampGpio, kLampsActiveHigh);
  IncandescentLamp left_lamp(kTickMs, kLampRiseMs, kLampFallMs);
  IncandescentLamp right_lamp(kTickMs, kLampRiseMs, kLampFallMs);
  const CrossingSignal signal(kFlashesPerMinute);

  printf("level_crossing_signal: GPIO %u and %u at %u flashes/min\n",
         static_cast<unsigned>(kLeftLampGpio),
         static_cast<unsigned>(kRightLampGpio),
         static_cast<unsigned>(kFlashesPerMinute));

  // Sleep until absolute deadlines so the flash rate does not drift.
  absolute_time_t next_tick = get_absolute_time();
  for (uint64_t tick = 0;; ++tick) {
    const Lamp lit = signal.LitLampAt(tick * kTickMs);
    left_output.Set(left_lamp.Update(lit == Lamp::kLeft));
    right_output.Set(right_lamp.Update(lit == Lamp::kRight));

    next_tick = delayed_by_ms(next_tick, kTickMs);
    sleep_until(next_tick);
  }
}

}  // namespace

}  // namespace level_crossing_signal

int main() {
  stdio_init_all();
  level_crossing_signal::Run();
}
