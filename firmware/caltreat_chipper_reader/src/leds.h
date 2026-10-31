/*
 * WS2812B LED string lighting the four corner light pipes (espressif/led_strip
 * over RMT, one data line).
 *
 * Clipper's light code (clippercard.com "Using Clipper"): green = paid,
 * yellow = paid, balance low, red = not accepted. As on the real validators
 * the lights are off while idle and flash once, for about a second, per tap.
 */
#pragma once

#include <stdint.h>
#include "esp_err.h"

#define LEDS_GPIO   32
#define LEDS_COUNT  100

typedef enum {
    LEDS_GREEN,
    LEDS_YELLOW,
    LEDS_RED,
} leds_color_t;

/* Set up the strip (all off) and its task. */
esp_err_t leds_init(void);

/* Set every LED to one colour and latch it (direct, for bring-up). */
esp_err_t leds_fill(uint8_t r, uint8_t g, uint8_t b);

/* One ~1 s flash of `color`, then off. Returns immediately; a new flash
 * replaces the one showing. */
void leds_flash(leds_color_t color);
