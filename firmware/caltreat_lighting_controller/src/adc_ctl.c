// ADC readings on GP26-28 and the internal temperature sensor.

#include "app.h"
#include "hardware/adc.h"

#define ADC_TEMP_CHANNEL (NUM_ADC_CHANNELS - 1)

void adc_ctl_init(void) {
    adc_init();
    adc_set_temp_sensor_enabled(true);
}

static bool input_channel(uint8_t input, unsigned *chan) {
    if (input == ADC_INPUT_TEMPERATURE) {
        *chan = ADC_TEMP_CHANNEL;
        return true;
    }
    pin_cfg_t *s = gpio_ctl_state(input);
    if (input < BOARD_ADC_FIRST_PIN || input > BOARD_ADC_LAST_PIN || !s || s->func != PIN_FUNC_ADC) {
        return false;
    }
    *chan = input - BOARD_ADC_FIRST_PIN;
    return true;
}

bool adc_ctl_read(uint8_t input, uint16_t *raw) {
    unsigned chan;
    if (!input_channel(input, &chan)) return false;
    adc_select_input(chan);
    (void)adc_read();   // settle after switching the mux
    uint32_t sum = 0;
    for (unsigned i = 0; i < 4; i++) sum += adc_read();
    *raw = (uint16_t)((sum + 2) / 4);
    return true;
}
