// Waveshare RP2350-ETH: pins, fixed hardware parameters and the status LED.
#pragma once

#include <stdint.h>

#include "pico/status_led.h"
#include "pico/time.h"

// CH9120 Ethernet controller (UART-attached, own TCP/IP stack)
#define CH9120_UART          uart1
#define CH9120_PIN_TX        20   // MCU TX -> CH9120 RXD
#define CH9120_PIN_RX        21   // MCU RX <- CH9120 TXD
#define CH9120_PIN_TCPCS     17   // low = TCP connection up
#define CH9120_PIN_CFG       18   // low = configuration mode
#define CH9120_PIN_RST       19   // low = reset
#define CH9120_CONFIG_BAUD   9600u
#define CH9120_DATA_BAUD     921600u

// Modbus TCP server port (the CH9120 gets its address over DHCP)
#define MODBUS_TCP_PORT      502u

// User GPIOs broken out on the headers. Index into this table is used for
// per-pin config storage; Modbus addresses pins by GPIO number.
#define BOARD_IO_PIN_COUNT   14
static const uint8_t BOARD_IO_PINS[BOARD_IO_PIN_COUNT] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 22, 26, 27, 28,
};
#define BOARD_IO_PIN_MASK ( \
    0x000003FFu | (1u << 22) | (1u << 26) | (1u << 27) | (1u << 28))

// GPIOs that can be used as ADC inputs (ADC input = gpio - 26)
#define BOARD_ADC_FIRST_PIN  26
#define BOARD_ADC_LAST_PIN   28

static inline int board_io_index(unsigned gpio) {
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        if (BOARD_IO_PINS[i] == gpio) return i;
    }
    return -1;
}

// ---- status LED: WS2812 on GP25 via the SDK's colored status LED ---------

// This board's LED takes its colour bytes in R,G,B order, while the SDK
// sends G,R,B, so red and green are swapped here.
#define BOARD_LED_RGB(r, g, b) PICO_COLORED_STATUS_LED_COLOR_FROM_RGB(g, r, b)

// WS2812 latches a colour only after the data line has been idle for a
// while (>= 280 us on newer parts), so leave a gap before each frame. The
// SDK only sends a colour when the LED goes from off to on, so switch it off
// first to make every call take effect.
static inline void board_led_set(uint32_t color) {
    busy_wait_us(400);
    colored_status_led_set_state(false);
    busy_wait_us(400);
    colored_status_led_set_on_with_color(color);
}
