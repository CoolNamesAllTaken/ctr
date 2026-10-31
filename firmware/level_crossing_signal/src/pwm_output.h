// Variable-brightness output on a single GPIO using the RP2040 PWM hardware.

#ifndef LEVEL_CROSSING_SIGNAL_SRC_PWM_OUTPUT_H_
#define LEVEL_CROSSING_SIGNAL_SRC_PWM_OUTPUT_H_

#include <cstdint>

namespace level_crossing_signal {

// Owns one GPIO and drives it with PWM. Two outputs may share a PWM slice
// only if they use the same frequency, which all instances do.
//
// Example:
//   PwmOutput led(/*gpio=*/8, /*active_high=*/true);
//   led.Set(0.5f);
class PwmOutput {
 public:
  // Configures `gpio` for PWM and turns the output off. If `active_high` is
  // false, a level of 1.0 drives the pin low.
  PwmOutput(uint32_t gpio, bool active_high);

  PwmOutput(const PwmOutput&) = delete;
  PwmOutput& operator=(const PwmOutput&) = delete;

  // Sets the output level: 0.0 is off, 1.0 is fully on. Values outside that
  // range are clamped.
  void Set(float level);

 private:
  uint32_t gpio_;
  bool active_high_;
  uint32_t wrap_;  // PWM counter top value; a compare of wrap_ + 1 is 100%.
};

}  // namespace level_crossing_signal

#endif  // LEVEL_CROSSING_SIGNAL_SRC_PWM_OUTPUT_H_
