// Models the warm-up and cool-down of an incandescent filament so that an
// LED driven by it glows and fades like a traditional crossing signal lamp.
// Hardware independent.

#ifndef LEVEL_CROSSING_SIGNAL_SRC_INCANDESCENT_LAMP_H_
#define LEVEL_CROSSING_SIGNAL_SRC_INCANDESCENT_LAMP_H_

namespace level_crossing_signal {

// First-order thermal model of a lamp filament, stepped at a fixed rate.
//
// Example:
//   IncandescentLamp lamp(/*tick_ms=*/5, /*rise_ms=*/40, /*fall_ms=*/90);
//   float light = lamp.Update(/*energized=*/true);
class IncandescentLamp {
 public:
  // `tick_ms` is the interval between calls to Update(). `rise_ms` and
  // `fall_ms` are the filament's heating and cooling time constants. A time
  // constant of 0 makes that transition instant.
  IncandescentLamp(float tick_ms, float rise_ms, float fall_ms);

  // Advances the model by one tick and returns light output in [0, 1].
  float Update(bool energized);

 private:
  float rise_step_;
  float fall_step_;
  float heat_ = 0.0f;  // Filament temperature, normalized to [0, 1].
};

}  // namespace level_crossing_signal

#endif  // LEVEL_CROSSING_SIGNAL_SRC_INCANDESCENT_LAMP_H_
