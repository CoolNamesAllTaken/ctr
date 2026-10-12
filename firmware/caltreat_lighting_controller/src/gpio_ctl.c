// Pin configuration, digital I/O, timed pulses and failsafe defaults for the
// header GPIOs.
//
// Note RP2350 erratum E9: with the internal pull-down enabled, an input pin
// can latch high after being driven high. Prefer external pull-downs (or
// pull-ups) on inputs that need a defined idle level.

#include <string.h>

#include "app.h"
#include "hardware/adc.h"
#include "hardware/gpio.h"
#include "hardware/structs/sio.h"
#include "pico/time.h"

static pin_cfg_t pins[BOARD_IO_PIN_COUNT];
static alarm_id_t pulse_alarm[BOARD_IO_PIN_COUNT];

pin_cfg_t *gpio_ctl_state(unsigned gpio) {
    int i = board_io_index(gpio);
    return i < 0 ? NULL : &pins[i];
}

static bool is_adc_pin(unsigned gpio) {
    return gpio >= BOARD_ADC_FIRST_PIN && gpio <= BOARD_ADC_LAST_PIN;
}

uint8_t gpio_ctl_validate(unsigned gpio, const pin_cfg_t *cfg) {
    if (board_io_index(gpio) < 0) return ST_BAD_ARG;
    if (cfg->func > PIN_FUNC_ADC || cfg->pull > PIN_PULL_BUS_KEEP || cfg->drive > PIN_DRIVE_12MA) {
        return ST_BAD_ARG;
    }
    if (cfg->func == PIN_FUNC_ADC && !is_adc_pin(gpio)) return ST_BAD_ARG;
    if (cfg->func == PIN_FUNC_PWM) {
        if (cfg->pwm_freq_hz == 0) return ST_BAD_ARG;
        return pwm_ctl_check(gpio, cfg->pwm_freq_hz);
    }
    return ST_OK;
}

static void cancel_pulse(int idx) {
    if (pulse_alarm[idx] > 0) {
        cancel_alarm(pulse_alarm[idx]);
        pulse_alarm[idx] = 0;
    }
}

static void apply_pad(unsigned gpio, const pin_cfg_t *cfg) {
    gpio_set_pulls(gpio,
                   cfg->pull == PIN_PULL_UP || cfg->pull == PIN_PULL_BUS_KEEP,
                   cfg->pull == PIN_PULL_DOWN || cfg->pull == PIN_PULL_BUS_KEEP);
    static const enum gpio_drive_strength drive[] = {
        GPIO_DRIVE_STRENGTH_2MA, GPIO_DRIVE_STRENGTH_4MA,
        GPIO_DRIVE_STRENGTH_8MA, GPIO_DRIVE_STRENGTH_12MA,
    };
    gpio_set_drive_strength(gpio, drive[cfg->drive]);
    gpio_set_slew_rate(gpio, (cfg->flags & PIN_FLAG_SLEW_FAST) ? GPIO_SLEW_RATE_FAST : GPIO_SLEW_RATE_SLOW);
    gpio_set_input_hysteresis_enabled(gpio, (cfg->flags & PIN_FLAG_SCHMITT) != 0);
    gpio_set_inover(gpio, (cfg->flags & PIN_FLAG_INVERT_IN) ? GPIO_OVERRIDE_INVERT : GPIO_OVERRIDE_NORMAL);
    gpio_set_outover(gpio, (cfg->flags & PIN_FLAG_INVERT_OUT) ? GPIO_OVERRIDE_INVERT : GPIO_OVERRIDE_NORMAL);
}

uint8_t gpio_ctl_configure(unsigned gpio, const pin_cfg_t *cfg) {
    uint8_t st = gpio_ctl_validate(gpio, cfg);
    if (st != ST_OK) return st;
    int idx = board_io_index(gpio);
    pin_cfg_t *cur = &pins[idx];

    // Tear down the previous function
    cancel_pulse(idx);
    if (cur->func == PIN_FUNC_PWM) pwm_ctl_stop(gpio);

    *cur = *cfg;
    switch (cfg->func) {
    case PIN_FUNC_OFF:
        gpio_set_function(gpio, GPIO_FUNC_NULL);
        gpio_set_input_enabled(gpio, false);
        apply_pad(gpio, cfg);
        break;
    case PIN_FUNC_INPUT:
        gpio_set_function(gpio, GPIO_FUNC_SIO);
        gpio_set_dir(gpio, GPIO_IN);
        gpio_set_input_enabled(gpio, true);
        apply_pad(gpio, cfg);
        break;
    case PIN_FUNC_OUTPUT:
        // Latch the level before enabling the driver to avoid a glitch
        gpio_put(gpio, (cfg->flags & PIN_FLAG_LEVEL_HIGH) != 0);
        gpio_set_function(gpio, GPIO_FUNC_SIO);
        gpio_set_input_enabled(gpio, true);
        apply_pad(gpio, cfg);
        gpio_set_dir(gpio, GPIO_OUT);
        break;
    case PIN_FUNC_PWM:
        gpio_set_input_enabled(gpio, true);
        apply_pad(gpio, cfg);
        st = pwm_ctl_start(gpio, cfg->pwm_freq_hz, cfg->pwm_duty);
        break;
    case PIN_FUNC_ADC:
        // Analogue: digital input off, no pulls, no output
        adc_gpio_init(gpio);
        cur->pull = PIN_PULL_NONE;
        break;
    }
    if (st != ST_OK) {
        // Leave the pin safe if the function could not be started
        gpio_set_function(gpio, GPIO_FUNC_NULL);
        cur->func = PIN_FUNC_OFF;
    }
    return st;
}

static bool outputs_only(uint32_t mask) {
    if (mask & ~BOARD_IO_PIN_MASK) return false;
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if ((mask & (1u << BOARD_IO_PINS[i])) && pins[i].func != PIN_FUNC_OUTPUT) return false;
    }
    return true;
}

uint8_t gpio_ctl_write(uint32_t mask, uint32_t values) {
    if (!outputs_only(mask)) return ST_BAD_ARG;
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (mask & (1u << BOARD_IO_PINS[i])) cancel_pulse(i);
    }
    gpio_put_masked(mask, values);
    // Keep the recorded level in step so the pin registers reflect reality
    uint32_t out = sio_hw->gpio_out;
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (!(mask & (1u << BOARD_IO_PINS[i]))) continue;
        if (out & (1u << BOARD_IO_PINS[i])) pins[i].flags |= PIN_FLAG_LEVEL_HIGH;
        else pins[i].flags &= (uint8_t)~PIN_FLAG_LEVEL_HIGH;
    }
    return ST_OK;
}

uint32_t gpio_ctl_levels(void) {
    return gpio_get_all() & BOARD_IO_PIN_MASK;
}

uint32_t gpio_ctl_outputs(void) {
    return sio_hw->gpio_out & BOARD_IO_PIN_MASK;
}

static int64_t pulse_end(alarm_id_t id, void *user) {
    (void)id;
    unsigned gpio = (unsigned)(uintptr_t)user & 0xFF;
    bool level = ((uintptr_t)user >> 8) & 1;
    gpio_put(gpio, level);
    int idx = board_io_index(gpio);
    if (idx >= 0) {
        pulse_alarm[idx] = 0;
        if (level) pins[idx].flags |= PIN_FLAG_LEVEL_HIGH;
        else pins[idx].flags &= (uint8_t)~PIN_FLAG_LEVEL_HIGH;
    }
    return 0;
}

uint8_t gpio_ctl_pulse(unsigned gpio, bool level, uint32_t ms) {
    int idx = board_io_index(gpio);
    if (idx < 0 || pins[idx].func != PIN_FUNC_OUTPUT) return ST_BAD_ARG;
    if (ms == 0) return ST_BAD_ARG;
    cancel_pulse(idx);
    gpio_put(gpio, level);
    uintptr_t user = gpio | ((uintptr_t)!level << 8);
    alarm_id_t id = add_alarm_in_ms(ms, pulse_end, (void *)user, true);
    if (id < 0) return ST_BAD_STATE;
    pulse_alarm[idx] = id;   // 0 means it already fired
    return ST_OK;
}

void gpio_ctl_apply_defaults(const pin_cfg_t defaults[], bool at_boot) {
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (!at_boot && (defaults[i].flags & PIN_FLAG_FS_HOLD)) continue;
        if (gpio_ctl_configure(BOARD_IO_PINS[i], &defaults[i]) != ST_OK) {
            pin_cfg_t off = defaults[i];
            off.func = PIN_FUNC_OFF;
            gpio_ctl_configure(BOARD_IO_PINS[i], &off);
        }
    }
}

void gpio_ctl_init(const pin_cfg_t defaults[]) {
    memset(pins, 0, sizeof pins);
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        pins[i].func = PIN_FUNC_OFF;
        gpio_set_function(BOARD_IO_PINS[i], GPIO_FUNC_NULL);
    }
    gpio_ctl_apply_defaults(defaults, true);
}
