#include "src/incandescent_lamp.h"

#include <cmath>

namespace level_crossing_signal {

namespace {

// Fraction of the remaining distance to the target covered in one tick.
float StepFraction(float tick_ms, float time_constant_ms) {
  if (time_constant_ms <= 0.0f) return 1.0f;
  return 1.0f - std::exp(-tick_ms / time_constant_ms);
}

}  // namespace

IncandescentLamp::IncandescentLamp(float tick_ms, float rise_ms,
                                   float fall_ms)
    : rise_step_(StepFraction(tick_ms, rise_ms)),
      fall_step_(StepFraction(tick_ms, fall_ms)) {}

float IncandescentLamp::Update(bool energized) {
  const float target = energized ? 1.0f : 0.0f;
  const float step = energized ? rise_step_ : fall_step_;
  heat_ += (target - heat_) * step;

  // Light output climbs steeply with filament temperature. Squaring is a
  // cheap approximation that gives the lagging glow-up and dim afterglow.
  return heat_ * heat_;
}

}  // namespace level_crossing_signal
