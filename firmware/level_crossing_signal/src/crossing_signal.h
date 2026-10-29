// Timing for a railroad grade crossing flasher: two lamps that light
// alternately at a fixed rate. Hardware independent.

#ifndef LEVEL_CROSSING_SIGNAL_SRC_CROSSING_SIGNAL_H_
#define LEVEL_CROSSING_SIGNAL_SRC_CROSSING_SIGNAL_H_

#include <cstdint>

namespace level_crossing_signal {

enum class Lamp { kLeft, kRight };

// Decides which lamp of the pair is energized at a given time.
//
// Example:
//   CrossingSignal signal(/*flashes_per_minute=*/55);
//   Lamp lit = signal.LitLampAt(elapsed_ms);
class CrossingSignal {
 public:
  // `flashes_per_minute` is how often each lamp flashes. US practice
  // (MUTCD section 8C.02) calls for 45 to 65 flashes per minute per lamp.
  explicit CrossingSignal(uint32_t flashes_per_minute);

  // Returns the lamp that is lit `elapsed_ms` after the signal activated.
  // The left lamp lights first.
  Lamp LitLampAt(uint64_t elapsed_ms) const;

 private:
  uint32_t half_period_ms_;
};

}  // namespace level_crossing_signal

#endif  // LEVEL_CROSSING_SIGNAL_SRC_CROSSING_SIGNAL_H_
