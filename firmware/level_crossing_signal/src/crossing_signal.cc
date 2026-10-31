#include "src/crossing_signal.h"

#include <cstdint>

namespace level_crossing_signal {

namespace {

constexpr uint32_t kMsPerMinute = 60'000;

}  // namespace

CrossingSignal::CrossingSignal(uint32_t flashes_per_minute)
    : half_period_ms_(kMsPerMinute / flashes_per_minute / 2) {}

Lamp CrossingSignal::LitLampAt(uint64_t elapsed_ms) const {
  const uint64_t half_period_index = elapsed_ms / half_period_ms_;
  return (half_period_index % 2 == 0) ? Lamp::kLeft : Lamp::kRight;
}

}  // namespace level_crossing_signal
