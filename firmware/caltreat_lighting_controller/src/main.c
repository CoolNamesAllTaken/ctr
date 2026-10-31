// App: Modbus TCP <-> GPIO bridge.
//
// Booted by the ROM from partition app_a or app_b (see boot_ctl.c). The
// CH9120 runs as a TCP server on port 502 with a DHCP address; the host
// (one client at a time) reads and writes the register map in regmap.h.
// Pins sit at their stored defaults ("failsafe") until the host writes
// something, and return to them when the host goes quiet (heartbeat) or
// disconnects.

#include <stdio.h>
#include <string.h>

#include "app.h"
#include "hardware/watchdog.h"
#include "pico/stdio_usb.h"
#include "pico/stdlib.h"

const uint32_t fw_info[3] = { 0x45325052u /* "RP2E" */, 0x4F464E49u /* "INFO" */, FW_VERSION };

#define WATCHDOG_MS         5000
// A connection with no requests for the heartbeat time plus this grace (or
// STALE_IDLE_MS with the heartbeat off) is assumed half-open, e.g. the host
// vanished without closing it; the CH9120 is reset to accept a new one.
#define STALE_GRACE_MS      5000
#define STALE_IDLE_MS       60000
// With no client, the CH9120 is reset now and then so it asks for a DHCP lease
// again: it only does that coming out of reset, so a board that booted before
// the cable or the DHCP server was up would never get an address. The interval
// doubles after each reset, up to the maximum, so a DHCP server slower than the
// first interval still gets time to answer; it starts over on connect/disconnect.
#define NO_CLIENT_RESET_MIN_MS  30000
#define NO_CLIENT_RESET_MAX_MS  60000

#define LED_WAITING   BOARD_LED_RGB(12, 12, 12)   // white: no client connected
#define LED_CONNECTED BOARD_LED_RGB(0, 32, 0)     // green: host connected and in control
#define LED_UPDATING  BOARD_LED_RGB(32, 24, 0)    // yellow: receiving firmware
#define LED_FAILSAFE  BOARD_LED_RGB(32, 0, 0)     // red: failsafe defaults applied

static meta_t meta;
static pin_cfg_t defaults[BOARD_IO_PIN_COUNT];   // edited over Modbus, persisted by CMD_SAVE
static bool failsafe_active = true;   // pins are at their defaults until the host takes over
static uint8_t failsafe_reason = FAILSAFE_REASON_BOOT;
static bool connected;
static absolute_time_t last_request;
static ch9120_info_t net_info;
static int net_result;

static void enter_failsafe(uint8_t reason) {
    if (failsafe_active) return;
    failsafe_active = true;
    failsafe_reason = reason;
    gpio_ctl_apply_defaults(defaults, false);
    printf("failsafe: entered (%s)\n", reason == FAILSAFE_REASON_HEARTBEAT ? "heartbeat" : "disconnect");
}

static void update_led(void) {
    static uint32_t shown = 0xFFFFFFFFu;
    uint32_t color = updater_state() == UPDATER_RECEIVING ? LED_UPDATING
                   : !connected                           ? LED_WAITING
                   : failsafe_active                      ? LED_FAILSAFE
                                                          : LED_CONNECTED;
    if (color != shown) board_led_set(color);
    shown = color;
}

// ---- interface for the Modbus server (app.h) ----------------------------

void app_request_seen(bool write) {
    last_request = get_absolute_time();
    boot_ctl_host_seen();
    if (write && failsafe_active) {
        failsafe_active = false;
        printf("failsafe: left\n");
    }
}

bool app_failsafe_active(void) { return failsafe_active; }
uint8_t app_failsafe_reason(void) { return failsafe_reason; }
uint16_t app_heartbeat_ms(void) { return meta.heartbeat_ms; }
void app_set_heartbeat_ms(uint16_t ms) { meta.heartbeat_ms = ms; }
pin_cfg_t *app_default(unsigned idx) { return &defaults[idx]; }
const ch9120_info_t *app_net_info(void) { return &net_info; }

uint8_t app_save_settings(void) {
    memcpy(meta.pins, defaults, sizeof defaults);
    return flashstore_save(&meta) ? ST_OK : ST_FLASH_ERROR;
}

// ---- main ---------------------------------------------------------------

static void print_status(void) {
    static const char *const net_results[] = { "unchanged", "reprogrammed", "NOT ACKED" };
    const uint8_t *m = net_info.mac, *ip = net_info.ip;
    boot_ctl_print();
    printf("net: ch9120 %s mac %02x:%02x:%02x:%02x:%02x:%02x ip %u.%u.%u.%u connected %d failsafe %d\n",
           net_results[net_result], m[0], m[1], m[2], m[3], m[4], m[5], ip[0], ip[1], ip[2], ip[3], connected,
           failsafe_active);
}

int main(void) {
    boot_ctl_init();
    stdio_init_all();
    int partition = boot_ctl_partition() == PARTITION_APP_B ? PARTITION_APP_B : PARTITION_APP_A;
    flashstore_init(app_partition_offset(partition));
    boot_ctl_finish_install();
    flashstore_load(&meta);
    memcpy(defaults, meta.pins, sizeof defaults);

    // Outputs to their safe state before anything slow happens
    gpio_ctl_init(defaults);
    adc_ctl_init();
    events_init();
    updater_init(partition);
    status_led_init();
    update_led();
    watchdog_enable(WATCHDOG_MS, true);

    ch9120_init();
    net_result = ch9120_configure(meta.ch9120_config != CH9120_CONFIG_VERSION, &net_info);
    if (net_result == CH9120_WRITTEN) {
        meta.ch9120_config = CH9120_CONFIG_VERSION;
        flashstore_save(&meta);
    }
    link_init();
    modbus_srv_init();

    last_request = get_absolute_time();
    uint32_t no_client_reset_ms = NO_CLIENT_RESET_MIN_MS;
    bool usb_log = false;

    while (true) {
        watchdog_update();

        bool now_connected = ch9120_connected();
        if (now_connected != connected) {
            // On connect, keep any input: the CH9120 clears its buffer itself,
            // and the first request may already be arriving.
            connected = now_connected;
            last_request = get_absolute_time();
            no_client_reset_ms = NO_CLIENT_RESET_MIN_MS;
            printf("net: client %s\n", connected ? "connected" : "disconnected");
            if (!connected) {
                modbus_srv_reset();
                enter_failsafe(FAILSAFE_REASON_DISCONNECT);
            }
        }

        if (connected) {
            modbus_srv_task();
            int64_t silent_ms = absolute_time_diff_us(last_request, get_absolute_time()) / 1000;
            if (meta.heartbeat_ms && silent_ms > meta.heartbeat_ms) enter_failsafe(FAILSAFE_REASON_HEARTBEAT);
            if (silent_ms > (meta.heartbeat_ms ? meta.heartbeat_ms + STALE_GRACE_MS : STALE_IDLE_MS)) {
                printf("net: connection idle for %lld ms, resetting CH9120\n", silent_ms);
                ch9120_reset();
                last_request = get_absolute_time();
            }
        } else if (absolute_time_diff_us(last_request, get_absolute_time()) / 1000 > no_client_reset_ms) {
            printf("net: no client for %lu ms, resetting CH9120\n", (unsigned long)no_client_reset_ms);
            ch9120_reset();
            last_request = get_absolute_time();
            if (no_client_reset_ms < NO_CLIENT_RESET_MAX_MS) no_client_reset_ms *= 2;
        }

        pwm_ctl_task();
        boot_ctl_task();
        update_led();

        // Repeat the status whenever a USB log reader attaches
        bool usb_now = stdio_usb_connected();
        if (usb_now && !usb_log) print_status();
        usb_log = usb_now;
    }
}
