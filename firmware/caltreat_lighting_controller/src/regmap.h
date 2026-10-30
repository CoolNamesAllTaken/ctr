// Modbus register map. Mirrored in tools/src/lcctl/regmap.py; bump
// REGMAP_VERSION on incompatible changes.
//
// Addresses are 0-based. 32-bit values take two registers, high word first.
// Byte strings (UID, MAC, IP) are packed two bytes per register, first byte
// in the high half.
//
// Coils            0-29   output latch of GPIO n (write: header pins in
//                         OUTPUT function only)
// Discrete inputs  0-29   pad level of GPIO n (after input inversion; 0 for
//                         pins that are not on the headers)
// Input registers  see IR_* below
// Holding regs     see HR_* below
// FC 21            write file record (firmware update, see HR_OTA_BASE)
// FC 43 / 14       device identification
#pragma once

#define REGMAP_VERSION          1
#define REG_GPIO_COUNT          30

// ---- input registers ------------------------------------------------------
#define IR_INFO_BASE            0
enum {
    IR_MAP_VERSION      = 0,
    IR_FW_VERSION       = 1,    // u32 0x00MMmmpp
    IR_UID              = 3,    // 8 bytes (4 regs)
    IR_BOOT_PARTITION   = 7,    // 0 = app_a, 1 = app_b, 0xFFFF unknown
    IR_BOOT_TYPE        = 8,    // boot ROM BOOT_TYPE_* (4 = flash update)
    IR_TBYB_INFO        = 9,    // boot ROM BOOT_TBYB_AND_UPDATE_FLAG_* at boot
    IR_FLAGS            = 10,   // IR_FLAG_*
    IR_FAILSAFE_REASON  = 11,   // FAILSAFE_REASON_*, valid while failsafe
    IR_UPTIME_MS        = 12,   // u32
    IR_IO_MASK          = 14,   // u32, GPIOs on the headers
    IR_MAC              = 16,   // 6 bytes (3 regs)
    IR_RESET_REASON     = 19,   // RESET_*
    IR_IP               = 20,   // 4 bytes, as read from the CH9120 at boot
    IR_NETMASK          = 22,
    IR_GATEWAY          = 24,
    IR_BAD_FRAMES       = 26,   // unparseable input dropped since boot
    IR_INFO_COUNT       = 32,
};
#define IR_FLAG_TRIAL           0x0001   // running a trial image (not bought yet)
#define IR_FLAG_FAILSAFE        0x0002   // pins are at their failsafe defaults
#define IR_FLAG_OTA_BUSY        0x0004   // firmware transfer in progress

// ADC: raw (12-bit) and millivolts for GP26, GP27, GP28 and the
// temperature sensor (0xFFFF for a pin not in ADC function), then the
// temperature in 0.01 degC (signed).
#define IR_ADC_BASE             100
#define IR_ADC_TEMP_CENTI_C     108
#define IR_ADC_COUNT            9

// Edge counters: per GPIO n at IR_EDGE_BASE + 4 * n: count (u32, wraps),
// time the last counted change was first seen (u32 ms since boot).
#define IR_EDGE_BASE            200
#define IR_EDGE_STRIDE          4

// Firmware update status: state (UPDATER_*), error (UPDATER_ERR_*), bytes
// received (u32), target partition.
#define IR_OTA_BASE             400
#define IR_OTA_STATE            400
#define IR_OTA_ERROR            401
#define IR_OTA_RECEIVED         402
#define IR_OTA_TARGET           404
#define IR_OTA_COUNT            5

// ---- holding registers ------------------------------------------------------
#define HR_HEARTBEAT_MS         0     // failsafe after this long without a request; 0 = off
#define HR_CMD_KEY              1     // write CMD_KEY and the command together (FC 16)
#define HR_CMD                  2
#define HR_SYS_COUNT            3
#define CMD_KEY                 0x5AA5
enum {
    CMD_REBOOT  = 1,   // normal reboot
    CMD_SAVE    = 2,   // persist heartbeat + pin defaults
    CMD_BOOTSEL = 3,   // reboot into USB BOOTSEL mode
};

// Pin block per GPIO n at HR_PIN_BASE + 16 * n (header pins only).
// Writing func/pull/drive/flags reconfigures the pin. On a PWM pin, writing
// duty (and/or freq) ramps to it over the current fade_ms. Writing pulse_ms
// pulses an OUTPUT pin to pulse_level for that long. edges/debounce_ms set
// up edge counting.
#define HR_PIN_BASE             1000
#define HR_PIN_STRIDE           16
enum {
    PIN_REG_FUNC        = 0,    // PIN_FUNC_*
    PIN_REG_PULL        = 1,    // PIN_PULL_*
    PIN_REG_DRIVE       = 2,    // PIN_DRIVE_*
    PIN_REG_FLAGS       = 3,    // PIN_FLAG_*
    PIN_REG_DUTY        = 4,    // 0..65535
    PIN_REG_FREQ        = 5,    // u32 Hz
    PIN_REG_FADE_MS     = 7,    // u32
    PIN_REG_PULSE_LEVEL = 9,
    PIN_REG_PULSE_MS    = 10,   // u32
    PIN_REG_EDGES       = 12,   // EDGE_*
    PIN_REG_DEBOUNCE_MS = 13,
    PIN_REG_COUNT       = 14,   // 14-15 reserved
};

// Failsafe / power-on default per GPIO n at HR_DEFAULT_BASE + 8 * n:
// func, pull, drive, flags, duty, freq (u32), reserved. Persisted by CMD_SAVE.
#define HR_DEFAULT_BASE         2000
#define HR_DEFAULT_STRIDE       8
#define DEF_REG_COUNT           7

// Firmware update control: write the image description together with
// OTA_CMD_BEGIN in one FC 16, stream the image with FC 21 (file number
// 1 + offset / 4096, record number (offset % 4096) / 2, at most 120
// registers per record, high byte first), then OTA_CMD_VERIFY and
// OTA_CMD_ACTIVATE.
#define HR_OTA_BASE             3000
enum {
    OTA_REG_CMD     = 0,
    OTA_REG_LENGTH  = 1,    // u32 bytes
    OTA_REG_MAJOR   = 3,    // picobin version of the image
    OTA_REG_MINOR   = 4,
    OTA_REG_SHA256  = 5,    // 32 bytes (16 regs)
    HR_OTA_COUNT    = 21,
};
enum { OTA_CMD_BEGIN = 1, OTA_CMD_VERIFY = 2, OTA_CMD_ACTIVATE = 3, OTA_CMD_ABORT = 4 };
#define OTA_SECTOR_SIZE         4096
