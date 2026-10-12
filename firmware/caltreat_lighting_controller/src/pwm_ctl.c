// PWM outputs with per-pin frequency/duty and linear fades.
//
// Each PWM slice has two channels (A/B) that share one counter, so pins on
// the same slice must use the same frequency, and two pins on the same slice
// AND channel (e.g. GP6 and GP22, both slice 3 channel A) would always show
// the same waveform. Both cases are rejected with ST_CONFLICT.

#include "app.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "pico/time.h"

#define PWM_MIN_FREQ_HZ 10u
#define PWM_MAX_FREQ_HZ 10000000u

static struct {
    uint32_t freq_hz;   // 0 = slice unused
    uint16_t top;
} slices[NUM_PWM_SLICES];

static struct {
    bool     active;
    uint16_t from;
    uint16_t to;
    uint16_t now;       // duty currently output while active
    uint64_t start_us;
    uint64_t dur_us;
} fades[BOARD_IO_PIN_COUNT];

static bool pin_uses_pwm(unsigned gpio) {
    pin_cfg_t *s = gpio_ctl_state(gpio);
    return s && s->func == PIN_FUNC_PWM;
}

uint8_t pwm_ctl_check(unsigned gpio, uint32_t freq_hz) {
    if (freq_hz < PWM_MIN_FREQ_HZ || freq_hz > PWM_MAX_FREQ_HZ) return ST_BAD_ARG;
    unsigned slice = pwm_gpio_to_slice_num(gpio);
    unsigned chan = pwm_gpio_to_channel(gpio);
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        unsigned other = BOARD_IO_PINS[i];
        if (other == gpio || !pin_uses_pwm(other)) continue;
        if (pwm_gpio_to_slice_num(other) != slice) continue;
        if (pwm_gpio_to_channel(other) == chan) return ST_CONFLICT;
        if (slices[slice].freq_hz != freq_hz) return ST_CONFLICT;
    }
    return ST_OK;
}

static bool slice_has_other_user(unsigned gpio) {
    unsigned slice = pwm_gpio_to_slice_num(gpio);
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        unsigned other = BOARD_IO_PINS[i];
        if (other != gpio && pin_uses_pwm(other) && pwm_gpio_to_slice_num(other) == slice) return true;
    }
    return false;
}

static void set_level(unsigned gpio, uint16_t duty) {
    unsigned slice = pwm_gpio_to_slice_num(gpio);
    uint32_t period = (uint32_t)slices[slice].top + 1;
    uint32_t level = (uint32_t)(((uint64_t)duty * period + 32767) / 65535);
    // level == top + 1 (duty 65535) keeps the output high for the whole period
    pwm_set_chan_level(slice, pwm_gpio_to_channel(gpio), (uint16_t)level);
}

// Leaves the slice stopped: the caller sets the channel levels and then
// enables it, so the first period already has the right duty (the compare
// registers only latch at a counter wrap while the slice runs).
static void program_slice(unsigned slice, uint32_t freq_hz) {
    uint32_t clk = clock_get_hz(clk_sys);
    // Divider in 1/16ths, as small as possible for the best resolution
    uint64_t div16 = ((uint64_t)clk * 16 + (uint64_t)freq_hz * 65536 - 1) / ((uint64_t)freq_hz * 65536);
    if (div16 < 16) div16 = 16;
    if (div16 > 0xFFF) div16 = 0xFFF;
    uint64_t top = ((uint64_t)clk * 16) / (div16 * freq_hz);
    top = top ? top - 1 : 0;
    if (top > 0xFFFE) top = 0xFFFE;   // keep top+1 representable for 100 % duty
    if (top < 1) top = 1;

    pwm_config c = pwm_get_default_config();
    pwm_config_set_clkdiv_int_frac4(&c, (uint8_t)(div16 >> 4), (uint8_t)(div16 & 0xF));
    pwm_config_set_wrap(&c, (uint16_t)top);
    pwm_init(slice, &c, false);
    slices[slice].freq_hz = freq_hz;
    slices[slice].top = (uint16_t)top;
}

uint8_t pwm_ctl_start(unsigned gpio, uint32_t freq_hz, uint16_t duty) {
    uint8_t st = pwm_ctl_check(gpio, freq_hz);
    if (st != ST_OK) return st;
    unsigned slice = pwm_gpio_to_slice_num(gpio);
    int idx = board_io_index(gpio);
    fades[idx].active = false;
    if (slices[slice].freq_hz != freq_hz || !slice_has_other_user(gpio)) {
        program_slice(slice, freq_hz);
    }
    set_level(gpio, duty);
    pwm_set_enabled(slice, true);
    gpio_set_function(gpio, GPIO_FUNC_PWM);
    return ST_OK;
}

void pwm_ctl_stop(unsigned gpio) {
    unsigned slice = pwm_gpio_to_slice_num(gpio);
    int idx = board_io_index(gpio);
    if (idx >= 0) fades[idx].active = false;
    pwm_set_chan_level(slice, pwm_gpio_to_channel(gpio), 0);
    gpio_set_function(gpio, GPIO_FUNC_NULL);
    if (!slice_has_other_user(gpio)) {
        pwm_set_enabled(slice, false);
        slices[slice].freq_hz = 0;
    }
}

uint8_t pwm_ctl_set(unsigned gpio, uint32_t freq_hz, uint16_t duty, uint32_t fade_ms) {
    pin_cfg_t *s = gpio_ctl_state(gpio);
    if (!s || s->func != PIN_FUNC_PWM) return ST_BAD_STATE;
    int idx = board_io_index(gpio);
    unsigned slice = pwm_gpio_to_slice_num(gpio);

    if (freq_hz && freq_hz != slices[slice].freq_hz) {
        uint8_t st = pwm_ctl_check(gpio, freq_hz);
        if (st != ST_OK) return st;
        program_slice(slice, freq_hz);
        s->pwm_freq_hz = freq_hz;
        // The other channel keeps its duty cycle at the new period
        for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
            unsigned other = BOARD_IO_PINS[i];
            if (other != gpio && pin_uses_pwm(other) && pwm_gpio_to_slice_num(other) == slice) {
                set_level(other, gpio_ctl_state(other)->pwm_duty);
            }
        }
        set_level(gpio, s->pwm_duty);
        pwm_set_enabled(slice, true);
    }

    if (fade_ms == 0) {
        fades[idx].active = false;
        set_level(gpio, duty);
    } else {
        // A fade that is still running continues from where it is now
        uint16_t from = fades[idx].active ? fades[idx].now : s->pwm_duty;
        fades[idx].active = true;
        fades[idx].from = from;
        fades[idx].now = from;
        fades[idx].to = duty;
        fades[idx].start_us = time_us_64();
        fades[idx].dur_us = (uint64_t)fade_ms * 1000;
        set_level(gpio, from);
    }
    s->pwm_duty = duty;
    return ST_OK;
}

void pwm_ctl_task(void) {
    uint64_t now = time_us_64();
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (!fades[i].active) continue;
        uint64_t t = now - fades[i].start_us;
        uint16_t duty;
        if (t >= fades[i].dur_us) {
            duty = fades[i].to;
            fades[i].active = false;
        } else {
            int32_t span = (int32_t)fades[i].to - (int32_t)fades[i].from;
            duty = (uint16_t)(fades[i].from + (int32_t)((int64_t)span * (int64_t)t / (int64_t)fades[i].dur_us));
        }
        fades[i].now = duty;
        set_level(BOARD_IO_PINS[i], duty);
    }
}
