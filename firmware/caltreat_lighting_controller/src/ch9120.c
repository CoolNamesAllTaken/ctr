// WCH CH9120 UART <-> Ethernet controller.
//
// The chip is configured over the same UART at 9600 baud while CFG is held
// low (command set: "CH9120 Serial Control Instruction Set"). Settings live
// in its EEPROM, so they are only rewritten when they differ: the fields the
// chip can report are compared, the rest (DHCP, serial format, ...) are
// covered by CH9120_CONFIG_VERSION, stored by the caller.

#include <string.h>

#include "app.h"
#include "hardware/gpio.h"
#include "hardware/uart.h"
#include "hardware/watchdog.h"
#include "pico/time.h"

#define CMD_SAVE            0x0D
#define CMD_EXECUTE         0x0E
#define CMD_SET_MODE        0x10
#define CMD_SET_LOCAL_PORT  0x14
#define CMD_SET_RANDOM_PORT 0x17
#define CMD_SET_BAUD        0x21
#define CMD_SET_SERIAL_FMT  0x22
#define CMD_SET_TIMEOUT     0x23
#define CMD_SET_LINK_CLOSE  0x24
#define CMD_SET_PACKET_LEN  0x25
#define CMD_SET_CLEAR_BUF   0x26
#define CMD_SET_DHCP        0x33
#define CMD_GET_MODE        0x60
#define CMD_GET_LOCAL_IP    0x61
#define CMD_GET_MASK        0x62
#define CMD_GET_GATEWAY     0x63
#define CMD_GET_LOCAL_PORT  0x64
#define CMD_GET_BAUD        0x71
#define CMD_GET_MAC         0x81

#define MODE_TCP_SERVER     0x00
#define ACK                 0xAA
#define REPLY_TIMEOUT_US    200000

// Configuration takes several seconds in the worst case; keep the caller's
// watchdog fed.
static void wait_ms(uint32_t ms) {
    watchdog_update();
    sleep_ms(ms);
    watchdog_update();
}

void ch9120_init(void) {
    gpio_init(CH9120_PIN_CFG);
    gpio_set_dir(CH9120_PIN_CFG, GPIO_OUT);
    gpio_put(CH9120_PIN_CFG, 1);

    gpio_init(CH9120_PIN_RST);
    gpio_set_dir(CH9120_PIN_RST, GPIO_OUT);
    gpio_put(CH9120_PIN_RST, 1);

    gpio_init(CH9120_PIN_TCPCS);
    gpio_set_dir(CH9120_PIN_TCPCS, GPIO_IN);
    gpio_pull_up(CH9120_PIN_TCPCS);

    uart_init(CH9120_UART, CH9120_CONFIG_BAUD);
    uart_set_fifo_enabled(CH9120_UART, true);
    gpio_set_function(CH9120_PIN_TX, GPIO_FUNC_UART);
    gpio_set_function(CH9120_PIN_RX, GPIO_FUNC_UART);

    // Drop any TCP connection left over from before our own reset
    ch9120_reset();
}

void ch9120_reset(void) {
    gpio_put(CH9120_PIN_RST, 0);
    wait_ms(20);
    gpio_put(CH9120_PIN_RST, 1);
    wait_ms(500);
}

bool ch9120_connected(void) {
    return !gpio_get(CH9120_PIN_TCPCS);
}

// ---- configuration commands ------------------------------------------------

static void drain_rx(void) {
    while (uart_is_readable(CH9120_UART)) (void)uart_getc(CH9120_UART);
}

static bool read_bytes(uint8_t *out, unsigned n) {
    unsigned got = 0;
    absolute_time_t deadline = make_timeout_time_us(REPLY_TIMEOUT_US);
    while (got < n && !time_reached(deadline)) {
        if (uart_is_readable(CH9120_UART)) out[got++] = (uint8_t)uart_getc(CH9120_UART);
    }
    return got == n;
}

static void send_cmd(uint8_t cmd, const uint8_t *arg, unsigned arg_len) {
    uint8_t hdr[3] = { 0x57, 0xAB, cmd };
    drain_rx();
    uart_write_blocking(CH9120_UART, hdr, sizeof hdr);
    if (arg_len) uart_write_blocking(CH9120_UART, arg, arg_len);
}

// Little-endian argument of `len` bytes
static bool set(uint8_t cmd, uint32_t value, unsigned len) {
    uint8_t arg[4] = { (uint8_t)value, (uint8_t)(value >> 8), (uint8_t)(value >> 16), (uint8_t)(value >> 24) };
    send_cmd(cmd, arg, len);
    uint8_t r = 0;
    bool ok = read_bytes(&r, 1) && r == ACK;
    wait_ms(10);
    return ok;
}

static bool get(uint8_t cmd, uint8_t *out, unsigned n) {
    send_cmd(cmd, NULL, 0);
    return read_bytes(out, n);
}

static uint32_t le(const uint8_t *b, unsigned n) {
    uint32_t v = 0;
    while (n--) v = (v << 8) | b[n];
    return v;
}

static bool matches(void) {
    uint8_t mode, port[2], baud[4];
    return get(CMD_GET_MODE, &mode, 1) && mode == MODE_TCP_SERVER &&
           get(CMD_GET_LOCAL_PORT, port, 2) && le(port, 2) == MODBUS_TCP_PORT &&
           get(CMD_GET_BAUD, baud, 4) && le(baud, 4) == CH9120_DATA_BAUD;
}

static bool write_config(void) {
    bool ok = true;
    ok &= set(CMD_SET_MODE, MODE_TCP_SERVER, 1);
    ok &= set(CMD_SET_DHCP, 1, 1);
    ok &= set(CMD_SET_RANDOM_PORT, 0, 1);
    ok &= set(CMD_SET_LOCAL_PORT, MODBUS_TCP_PORT, 2);
    ok &= set(CMD_SET_BAUD, CH9120_DATA_BAUD, 4);
    ok &= set(CMD_SET_SERIAL_FMT, 0x080401, 3);   // 1 stop bit, no parity, 8 data bits
    // Packetise on 4 idle character times (timeout 0) or 512 bytes
    ok &= set(CMD_SET_TIMEOUT, 0, 4);
    ok &= set(CMD_SET_PACKET_LEN, 512, 4);
    // Drop the connection when the cable is unplugged, and discard stale
    // serial data when a new connection is made
    ok &= set(CMD_SET_LINK_CLOSE, 1, 1);
    ok &= set(CMD_SET_CLEAR_BUF, 1, 1);
    send_cmd(CMD_SAVE, NULL, 0);
    uint8_t r = 0;
    ok &= read_bytes(&r, 1) && r == ACK;
    wait_ms(200);
    // Apply the settings and restart the network side (no reply)
    send_cmd(CMD_EXECUTE, NULL, 0);
    wait_ms(500);
    return ok;
}

int ch9120_configure(bool force, ch9120_info_t *info) {
    // Enter configuration mode
    uart_tx_wait_blocking(CH9120_UART);
    uart_set_baudrate(CH9120_UART, CH9120_CONFIG_BAUD);
    gpio_put(CH9120_PIN_CFG, 0);
    wait_ms(500);
    drain_rx();

    memset(info, 0, sizeof *info);
    get(CMD_GET_MAC, info->mac, 6);
    get(CMD_GET_LOCAL_IP, info->ip, 4);
    get(CMD_GET_MASK, info->mask, 4);
    get(CMD_GET_GATEWAY, info->gateway, 4);

    int result = CH9120_UNCHANGED;
    if (force || !matches()) result = write_config() ? CH9120_WRITTEN : CH9120_FAILED;

    // Back to data mode
    gpio_put(CH9120_PIN_CFG, 1);
    wait_ms(500);
    uart_set_baudrate(CH9120_UART, CH9120_DATA_BAUD);
    drain_rx();
    return result;
}
