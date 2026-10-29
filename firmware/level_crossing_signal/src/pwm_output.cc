#include "src/pwm_output.h"

#include <algorithm>
#include <cstdint>

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"

namespace level_crossing_signal {

namespace {

// Above the audible range so inductive loads do not whine, and low enough
// to keep thousands of steps of resolution at the default 125 MHz clock.
constexpr uint32_t kPwmFrequencyHz = 20'000;

}  // namespace

PwmOutput::PwmOutput(uint32_t gpio, bool active_high)
    : gpio_(gpio),
      active_high_(active_high),
      wrap_(clock_get_hz(clk_sys) / kPwmFrequencyHz - 1) {
  gpio_set_function(gpio_, GPIO_FUNC_PWM);
  const uint32_t slice = pwm_gpio_to_slice_num(gpio_);
  pwm_config config = pwm_get_default_config();
  pwm_config_set_wrap(&config, static_cast<uint16_t>(wrap_));
  pwm_init(slice, &config, /*start=*/false);
  Set(0.0f);
  pwm_set_enabled(slice, true);
}

void PwmOutput::Set(float level) {
  level = std::clamp(level, 0.0f, 1.0f);
  if (!active_high_) level = 1.0f - level;
  const uint32_t full_scale = wrap_ + 1;
  const auto compare =
      static_cast<uint16_t>(level * static_cast<float>(full_scale) + 0.5f);
  pwm_set_gpio_level(gpio_, compare);
}

}  // namespace level_crossing_signal
