// The CH9120's TCP byte stream over the UART.
//
// Reception runs on a DMA channel in endless mode into a ring buffer, so
// bytes keep arriving while the CPU has interrupts disabled for flash
// erase/program. Modbus is request/response, which keeps the amount of
// unread data far below the ring size.

#include "app.h"
#include "hardware/dma.h"
#include "hardware/uart.h"
#include "hardware/watchdog.h"
#include "pico/time.h"

#define RX_RING_BITS 12
#define RX_RING_SIZE (1u << RX_RING_BITS)
#define RX_RING_MASK (RX_RING_SIZE - 1)

static uint8_t rx_ring[RX_RING_SIZE] __attribute__((aligned(RX_RING_SIZE)));
static uint32_t rx_tail;   // next ring index to consume
static int rx_dma = -1;

static uint32_t rx_head(void) {
    return ((uint32_t)dma_channel_hw_addr(rx_dma)->write_addr - (uint32_t)rx_ring) & RX_RING_MASK;
}

void link_init(void) {
    if (rx_dma < 0) rx_dma = dma_claim_unused_channel(true);
    dma_channel_abort(rx_dma);

    dma_channel_config c = dma_channel_get_default_config(rx_dma);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_8);
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, true);
    channel_config_set_ring(&c, true, RX_RING_BITS);
    channel_config_set_dreq(&c, uart_get_dreq(CH9120_UART, false));
    dma_channel_configure(rx_dma, &c, rx_ring, &uart_get_hw(CH9120_UART)->dr,
                          dma_encode_endless_transfer_count(), true);
    rx_tail = rx_head();
}

void link_reset(void) {
    rx_tail = rx_head();
}

uint32_t link_available(void) {
    return (rx_head() - rx_tail) & RX_RING_MASK;
}

uint32_t link_peek(uint8_t *buf, uint32_t n) {
    uint32_t avail = link_available();
    if (n > avail) n = avail;
    for (uint32_t i = 0; i < n; i++) buf[i] = rx_ring[(rx_tail + i) & RX_RING_MASK];
    return n;
}

int32_t link_read(uint8_t *buf, uint32_t n, int32_t timeout_ms) {
    uint32_t got = 0;
    absolute_time_t deadline = timeout_ms > 0 ? make_timeout_time_ms((uint32_t)timeout_ms) : at_the_end_of_time;
    while (got < n) {
        uint32_t avail = link_available();
        if (avail) {
            while (avail-- && got < n) {
                buf[got++] = rx_ring[rx_tail];
                rx_tail = (rx_tail + 1) & RX_RING_MASK;
            }
            if (timeout_ms > 0) deadline = make_timeout_time_ms((uint32_t)timeout_ms);
            continue;
        }
        if (timeout_ms == 0 || time_reached(deadline)) break;
        watchdog_update();
        tight_loop_contents();
    }
    return (int32_t)got;
}

void link_write(const uint8_t *buf, uint32_t n) {
    uart_write_blocking(CH9120_UART, buf, n);
}

void link_flush(void) {
    uart_tx_wait_blocking(CH9120_UART);
}
