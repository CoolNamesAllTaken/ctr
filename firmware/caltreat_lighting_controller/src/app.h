// Interfaces between the app modules.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "board.h"

// Firmware version 0x00MMmmpp, from custom_fw_version in platformio.ini
// (scripts/build_image.py, which also seals it into the image).
#ifndef FW_VERSION
#define FW_VERSION 0
#endif

// Magic words + FW_VERSION in the image, so `lcctl ota` can read the version
// from firmware.bin (main.c). Read the version from here so the linker keeps it.
extern const uint32_t fw_info[3];

// ---- flash layout (must match partitions.json) ------------------------------

#define FLASH_TOTAL_SIZE      (4u * 1024 * 1024)
#define FLASH_PT_SIZE         (128u * 1024)    // partition table region
#define FLASH_APP_A_OFFSET    0x020000u
#define FLASH_APP_B_OFFSET    0x200000u
#define FLASH_APP_SIZE        (1920u * 1024)
#define FLASH_DATA_OFFSET     0x3E0000u

// Boot ROM partition indices (position in partitions.json)
#define PARTITION_APP_A       0
#define PARTITION_APP_B       1

static inline uint32_t app_partition_offset(unsigned partition) {
    return partition == PARTITION_APP_B ? FLASH_APP_B_OFFSET : FLASH_APP_A_OFFSET;
}

// Raw view of the whole flash chip (QMI address translation window 1). The
// boot ROM maps the running partition at 0x10000000, so the data partition
// and the other app partition are read through this window.
#define FLASH_RAW_BASE        0x10400000u

// Watchdog scratch registers 0-3 survive a watchdog reboot (4-7 belong to
// the boot ROM).
#define SCRATCH_REQUEST       0   // why we rebooted ourselves
#define SCRATCH_OTA           1   // set before a flash-update reboot from OTA
#define REQUEST_REBOOT        0x5EB0071Eu
#define OTA_MARKER            0x07A0B007u

enum { RESET_POWER_ON = 0, RESET_WATCHDOG = 1, RESET_REQUESTED = 2 };

// ---- status codes returned by the modules -------------------------------------

enum {
    ST_OK          = 0,
    ST_BAD_ARG     = 1,   // value out of range / not allowed for this pin
    ST_BAD_STATE   = 2,   // pin not in the required function
    ST_CONFLICT    = 3,   // e.g. PWM slice already used at another frequency
    ST_FLASH_ERROR = 4,
};

// ---- pin configuration ---------------------------------------------------------

enum { PIN_FUNC_OFF, PIN_FUNC_INPUT, PIN_FUNC_OUTPUT, PIN_FUNC_PWM, PIN_FUNC_ADC };
enum { PIN_PULL_NONE, PIN_PULL_UP, PIN_PULL_DOWN, PIN_PULL_BUS_KEEP };
enum { PIN_DRIVE_2MA, PIN_DRIVE_4MA, PIN_DRIVE_8MA, PIN_DRIVE_12MA };
#define PIN_FLAG_SLEW_FAST   0x01
#define PIN_FLAG_SCHMITT     0x02
#define PIN_FLAG_INVERT_IN   0x04
#define PIN_FLAG_INVERT_OUT  0x08
#define PIN_FLAG_LEVEL_HIGH  0x10   // output level (OUTPUT function)
#define PIN_FLAG_FS_HOLD     0x20   // failsafe: leave this pin as it is
#define PIN_FLAGS_ALL        0x3F

// Live pin configuration, also the stored power-on / failsafe default
typedef struct {
    uint8_t  func;             // PIN_FUNC_*
    uint8_t  pull;             // PIN_PULL_*
    uint8_t  drive;            // PIN_DRIVE_*
    uint8_t  flags;            // PIN_FLAG_*
    uint16_t pwm_duty;         // 0..65535 = 0..100 %
    uint32_t pwm_freq_hz;
} pin_cfg_t;

#define EDGE_RISE 0x01
#define EDGE_FALL 0x02

// ---- main.c: connection, failsafe and persistent settings ----------------------

enum { FAILSAFE_REASON_HEARTBEAT = 0, FAILSAFE_REASON_DISCONNECT = 1, FAILSAFE_REASON_BOOT = 2 };

// A valid Modbus request arrived; writes take the pins out of failsafe.
void app_request_seen(bool write);
bool app_failsafe_active(void);
uint8_t app_failsafe_reason(void);
uint16_t app_heartbeat_ms(void);
void app_set_heartbeat_ms(uint16_t ms);
// Power-on / failsafe default of header pin `idx` (index into BOARD_IO_PINS).
pin_cfg_t *app_default(unsigned idx);
// Persist heartbeat and pin defaults. Returns ST_*.
uint8_t app_save_settings(void);

// ---- modbus_srv.c: Modbus TCP server and register map (regmap.h) ---------------

void modbus_srv_init(void);
// Serve buffered requests; call often.
void modbus_srv_task(void);
// Drop buffered input (disconnect).
void modbus_srv_reset(void);

// ---- boot_ctl.c: boot ROM A/B partitions, trial (TBYB) and reboots -------------

// Call first thing in main(), before the watchdog is enabled.
void boot_ctl_init(void);
// After flashstore_init(): a USB-installed image invalidates the other partition.
void boot_ctl_finish_install(void);
// A valid request from the host arrived (buys a network-delivered trial image).
void boot_ctl_host_seen(void);
// Reboots when a trial image was not bought in time.
void boot_ctl_task(void);
bool boot_ctl_on_trial(void);
int boot_ctl_partition(void);           // PARTITION_APP_*, -1 if unknown
uint8_t boot_ctl_boot_type(void);       // BOOT_TYPE_*
uint8_t boot_ctl_tbyb_info(void);       // BOOT_TBYB_AND_UPDATE_FLAG_*
uint8_t boot_ctl_reset_reason(void);    // RESET_*
void boot_ctl_print(void);              // boot summary on the USB log
void __attribute__((noreturn)) boot_ctl_reboot(void);
void __attribute__((noreturn)) boot_ctl_reboot_bootsel(void);

// ---- gpio_ctl.c: pin configuration, digital I/O, pulses --------------------------

void gpio_ctl_init(const pin_cfg_t defaults[]);
// Check a configuration without applying it. Returns ST_*.
uint8_t gpio_ctl_validate(unsigned gpio, const pin_cfg_t *cfg);
uint8_t gpio_ctl_configure(unsigned gpio, const pin_cfg_t *cfg);
// Current configuration of a header pin, NULL if not a header pin.
pin_cfg_t *gpio_ctl_state(unsigned gpio);
// Set the OUTPUT pins in `mask` (GPIO-number bitmap) to `values`.
uint8_t gpio_ctl_write(uint32_t mask, uint32_t values);
uint32_t gpio_ctl_levels(void);         // pad levels of the header pins
uint32_t gpio_ctl_outputs(void);        // output latches of the header pins
// Drive `level` for `ms`, then the opposite level.
uint8_t gpio_ctl_pulse(unsigned gpio, bool level, uint32_t ms);
// Put pins into their default state. At boot every pin is set; afterwards
// pins flagged PIN_FLAG_FS_HOLD are left alone.
void gpio_ctl_apply_defaults(const pin_cfg_t defaults[], bool at_boot);

// ---- pwm_ctl.c --------------------------------------------------------------------

// ST_CONFLICT if `gpio` cannot run at freq_hz next to the other PWM pins.
uint8_t pwm_ctl_check(unsigned gpio, uint32_t freq_hz);
uint8_t pwm_ctl_start(unsigned gpio, uint32_t freq_hz, uint16_t duty);
void pwm_ctl_stop(unsigned gpio);
// Pin must be in PIN_FUNC_PWM. freq_hz 0 keeps the frequency; the duty ramps
// linearly over fade_ms (0 = at once).
uint8_t pwm_ctl_set(unsigned gpio, uint32_t freq_hz, uint16_t duty, uint32_t fade_ms);
void pwm_ctl_task(void);

// ---- adc_ctl.c ----------------------------------------------------------------------

#define ADC_INPUT_TEMPERATURE 0xFE
void adc_ctl_init(void);
// Average of a few 12-bit readings of GP26-28 (in ADC function) or the
// temperature sensor. Returns false if the input is not available.
bool adc_ctl_read(uint8_t input, uint16_t *raw);

// ---- events.c: debounced edge counting --------------------------------------------

void events_init(void);
uint8_t events_config(unsigned gpio, uint8_t edges, uint16_t debounce_ms);
// Configuration and counters of a header pin: edges counted (wraps) and when
// the last one started (ms since boot).
void events_get(unsigned gpio, uint8_t *edges, uint16_t *debounce_ms, uint32_t *count, uint32_t *last_ms);

// ---- flashstore.c: settings in the data partition, raw flash access -----------------

typedef struct {
    uint32_t  magic;
    uint32_t  layout;
    uint32_t  seq;
    uint16_t  heartbeat_ms;      // failsafe after this long without a request; 0 = off
    uint16_t  reserved;
    uint32_t  ch9120_config;     // CH9120_CONFIG_VERSION last written to the CH9120
    pin_cfg_t pins[BOARD_IO_PIN_COUNT];   // power-on / failsafe defaults
    uint32_t  crc;
} meta_t;

// Map the raw flash window and protect the partition we run from.
void flashstore_init(uint32_t running_app_offset);
// Newest valid settings record, or factory defaults.
void flashstore_load(meta_t *m);
bool flashstore_save(meta_t *m);

static inline const uint8_t *flash_raw(uint32_t offset) {
    return (const uint8_t *)(FLASH_RAW_BASE + offset);
}
// Offsets from the start of the chip. Erase: multiples of 4096. Program:
// offset a multiple of 256, data in RAM (a trailing partial page is padded).
bool flash_erase(uint32_t offset, uint32_t len);
bool flash_program(uint32_t offset, const void *data, uint32_t len);
bool flash_sha256(uint32_t offset, uint32_t len, uint8_t out[32]);

// ---- updater.c: firmware update into the other app partition ------------------------

enum { UPDATER_IDLE, UPDATER_RECEIVING, UPDATER_VERIFIED, UPDATER_FAILED };
enum {
    UPDATER_ERR_NONE, UPDATER_ERR_STATE, UPDATER_ERR_LENGTH, UPDATER_ERR_SAME_VERSION,
    UPDATER_ERR_ON_TRIAL, UPDATER_ERR_FLASH, UPDATER_ERR_HASH, UPDATER_ERR_SEQUENCE,
};

typedef struct {
    uint32_t length;
    uint16_t major;            // picobin version of the image
    uint16_t minor;
    uint8_t  sha256[32];
} updater_image_t;

void updater_init(unsigned running_partition);
// Each returns UPDATER_ERR_*.
int updater_begin(const updater_image_t *img, bool on_trial);
int updater_write(uint32_t offset, const uint8_t *data, uint32_t len);
int updater_verify(void);
void updater_abort(void);
int updater_activate(void);    // reboots into the verified image
int updater_state(void);
int updater_error(void);
uint32_t updater_received(void);
unsigned updater_target_partition(void);
const updater_image_t *updater_image(void);

// ---- link.c: the CH9120's TCP byte stream over the UART -----------------------------

void link_init(void);
void link_reset(void);                  // drop buffered bytes
uint32_t link_available(void);
uint32_t link_peek(uint8_t *buf, uint32_t n);
// Read n bytes, waiting up to timeout_ms for each next byte (0 = only what
// is buffered). Returns the number read.
int32_t link_read(uint8_t *buf, uint32_t n, int32_t timeout_ms);
void link_write(const uint8_t *buf, uint32_t n);
void link_flush(void);

// ---- ch9120.c: the Ethernet controller -----------------------------------------------

// Bump when the settings written by ch9120_configure() change.
#define CH9120_CONFIG_VERSION 3

typedef struct {
    uint8_t mac[6];
    uint8_t ip[4];             // as reported at boot (usually the last DHCP lease)
    uint8_t mask[4];
    uint8_t gateway[4];
} ch9120_info_t;

enum { CH9120_UNCHANGED, CH9120_WRITTEN, CH9120_FAILED };

// Set up the pins and UART and hard-reset the chip.
void ch9120_init(void);
// Read the chip's info and make sure it runs as a DHCP TCP server on
// MODBUS_TCP_PORT; rewrite the settings if `force` or if they differ.
// Leaves the UART at the data baud rate. Returns CH9120_*.
int ch9120_configure(bool force, ch9120_info_t *info);
// Hard reset (drops the TCP connection; settings are kept).
void ch9120_reset(void);
// True while a client is connected.
bool ch9120_connected(void);

// main.c: what the CH9120 reported at boot
const ch9120_info_t *app_net_info(void);
