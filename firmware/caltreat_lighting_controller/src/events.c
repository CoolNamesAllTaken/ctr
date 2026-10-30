// Input edge counting. All header pins are sampled at 1 kHz from a timer
// interrupt; an edge is counted once the new level has been stable for the
// pin's debounce time. The host polls the counters (and the time the last
// counted change was first seen), so no edge is lost between polls.

#include "app.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/time.h"

#define SAMPLE_INTERVAL_US 1000

static struct {
    uint8_t  edges;        // EDGE_* (0 = not counted)
    uint8_t  stable;       // debounced level
    uint16_t debounce_ms;
    uint64_t change_us;    // when the current candidate level first appeared, 0 = none
    uint32_t count;
    uint32_t last_ms;
} pin_ev[BOARD_IO_PIN_COUNT];

static repeating_timer_t sample_timer;

static bool sample(repeating_timer_t *t) {
    (void)t;
    uint32_t levels = gpio_get_all();
    uint64_t now = time_us_64();
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (!pin_ev[i].edges) continue;
        uint8_t level = (levels >> BOARD_IO_PINS[i]) & 1;
        if (level == pin_ev[i].stable) {
            pin_ev[i].change_us = 0;
            continue;
        }
        if (!pin_ev[i].change_us) pin_ev[i].change_us = now;
        if (now - pin_ev[i].change_us < (uint64_t)pin_ev[i].debounce_ms * 1000) continue;

        pin_ev[i].stable = level;
        if (pin_ev[i].edges & (level ? EDGE_RISE : EDGE_FALL)) {
            pin_ev[i].count++;
            pin_ev[i].last_ms = (uint32_t)(pin_ev[i].change_us / 1000);
        }
        pin_ev[i].change_us = 0;
    }
    return true;
}

void events_init(void) {
    add_repeating_timer_us(-SAMPLE_INTERVAL_US, sample, NULL, &sample_timer);
}

uint8_t events_config(unsigned gpio, uint8_t edges, uint16_t debounce_ms) {
    int i = board_io_index(gpio);
    if (i < 0 || (edges & ~(EDGE_RISE | EDGE_FALL))) return ST_BAD_ARG;
    uint32_t save = save_and_disable_interrupts();
    pin_ev[i].edges = edges;
    pin_ev[i].debounce_ms = debounce_ms;
    pin_ev[i].stable = gpio_get(gpio);
    pin_ev[i].change_us = 0;
    restore_interrupts(save);
    return ST_OK;
}

void events_get(unsigned gpio, uint8_t *edges, uint16_t *debounce_ms, uint32_t *count, uint32_t *last_ms) {
    int i = board_io_index(gpio);
    if (i < 0) return;
    uint32_t save = save_and_disable_interrupts();
    *edges = pin_ev[i].edges;
    *debounce_ms = pin_ev[i].debounce_ms;
    *count = pin_ev[i].count;
    *last_ms = pin_ev[i].last_ms;
    restore_interrupts(save);
}
