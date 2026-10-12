// Modbus TCP server: nanoMODBUS over the CH9120 byte stream, and the
// register map (regmap.h) on top of the app modules.
//
// nanoMODBUS is only polled once a complete MBAP frame is buffered, so a
// slow or broken client never stalls the main loop. Input that cannot be
// parsed as Modbus TCP is dropped; the client's retry starts clean.

#include <stdio.h>
#include <string.h>

#include "app.h"
#include "nanomodbus.h"
#include "pico/stdio.h"
#include "pico/time.h"
#include "pico/unique_id.h"
#include "regmap.h"

#define BYTE_TIMEOUT_MS     50     // between bytes of a frame already announced
#define PARTIAL_TIMEOUT_MS  1000   // drop an incomplete frame after this long
#define MBAP_LEN            6

static nmbs_t nmbs;
static uint32_t bad_frames;
static bool partial;
static absolute_time_t partial_since;
static enum { ACTION_NONE, ACTION_REBOOT, ACTION_BOOTSEL, ACTION_OTA_ACTIVATE } pending_action;

// Per-pin values that only exist as registers
static struct {
    uint32_t fade_ms;
    uint32_t pulse_ms;
    uint8_t  pulse_level;
} shadow[BOARD_IO_PIN_COUNT];

// ---- transport ------------------------------------------------------------

static int32_t mb_read(uint8_t *buf, uint16_t count, int32_t byte_timeout_ms, void *arg) {
    (void)arg;
    return link_read(buf, count, byte_timeout_ms);
}

static int32_t mb_write(const uint8_t *buf, uint16_t count, int32_t byte_timeout_ms, void *arg) {
    (void)byte_timeout_ms;
    (void)arg;
    link_write(buf, count);
    return count;
}

// ---- helpers --------------------------------------------------------------

static nmbs_error status_to_exception(uint8_t st) {
    switch (st) {
    case ST_OK:          return NMBS_ERROR_NONE;
    case ST_FLASH_ERROR: return NMBS_EXCEPTION_SERVER_DEVICE_FAILURE;
    default:             return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
    }
}

static void put_u32(uint16_t *r, uint32_t v) {
    r[0] = (uint16_t)(v >> 16);
    r[1] = (uint16_t)v;
}

static uint32_t get_u32(const uint16_t *r) {
    return ((uint32_t)r[0] << 16) | r[1];
}

static void put_bytes(uint16_t *r, const uint8_t *b, unsigned n) {
    for (unsigned i = 0; i < n; i += 2) r[i / 2] = (uint16_t)((b[i] << 8) | (i + 1 < n ? b[i + 1] : 0));
}

static bool is_header_pin(unsigned gpio) {
    return gpio < 32 && (BOARD_IO_PIN_MASK & (1u << gpio));
}

static void seen(bool write) {
    app_request_seen(write);
}

// ---- input registers ------------------------------------------------------

static void fill_info(uint16_t *r) {
    memset(r, 0, IR_INFO_COUNT * sizeof *r);
    pico_unique_board_id_t id;
    pico_get_unique_board_id(&id);
    const ch9120_info_t *net = app_net_info();
    uint16_t flags = 0;
    if (boot_ctl_on_trial()) flags |= IR_FLAG_TRIAL;
    if (app_failsafe_active()) flags |= IR_FLAG_FAILSAFE;
    if (updater_state() == UPDATER_RECEIVING) flags |= IR_FLAG_OTA_BUSY;

    r[IR_MAP_VERSION] = REGMAP_VERSION;
    put_u32(&r[IR_FW_VERSION], fw_info[2]);
    put_bytes(&r[IR_UID], id.id, 8);
    r[IR_BOOT_PARTITION] = boot_ctl_partition() < 0 ? 0xFFFF : (uint16_t)boot_ctl_partition();
    r[IR_BOOT_TYPE] = boot_ctl_boot_type();
    r[IR_TBYB_INFO] = boot_ctl_tbyb_info();
    r[IR_FLAGS] = flags;
    r[IR_FAILSAFE_REASON] = app_failsafe_reason();
    put_u32(&r[IR_UPTIME_MS], to_ms_since_boot(get_absolute_time()));
    put_u32(&r[IR_IO_MASK], BOARD_IO_PIN_MASK);
    put_bytes(&r[IR_MAC], net->mac, 6);
    r[IR_RESET_REASON] = boot_ctl_reset_reason();
    put_bytes(&r[IR_IP], net->ip, 4);
    put_bytes(&r[IR_NETMASK], net->mask, 4);
    put_bytes(&r[IR_GATEWAY], net->gateway, 4);
    r[IR_BAD_FRAMES] = (uint16_t)(bad_frames > 0xFFFF ? 0xFFFF : bad_frames);
}

static void fill_adc(uint16_t *r) {
    static const uint8_t inputs[4] = { 26, 27, 28, ADC_INPUT_TEMPERATURE };
    for (unsigned i = 0; i < 4; i++) {
        uint16_t raw;
        if (adc_ctl_read(inputs[i], &raw)) {
            r[2 * i] = raw;
            r[2 * i + 1] = (uint16_t)((raw * 3300u + 2047) / 4095);   // 3.3 V reference
        } else {
            r[2 * i] = r[2 * i + 1] = 0xFFFF;
        }
    }
    // RP2350 datasheet: T = 27 - (V - 0.706) / 0.001721
    int32_t mv = r[7];
    int32_t centi = 2700 - ((mv - 706) * 100000) / 1721;
    r[IR_ADC_TEMP_CENTI_C - IR_ADC_BASE] = (uint16_t)(int16_t)centi;
}

static nmbs_error read_input_registers(uint16_t address, uint16_t quantity, uint16_t *out, uint8_t unit_id,
                                       void *arg) {
    (void)unit_id;
    (void)arg;
    seen(false);
    uint16_t info[IR_INFO_COUNT], adc[IR_ADC_COUNT], edge[IR_EDGE_STRIDE];
    bool have_info = false, have_adc = false;
    int edge_gpio = -1;
    for (uint32_t a = address; a < (uint32_t)address + quantity; a++) {
        uint16_t *dst = &out[a - address];
        if (a < IR_INFO_BASE + IR_INFO_COUNT) {
            if (!have_info) fill_info(info), have_info = true;
            *dst = info[a - IR_INFO_BASE];
        } else if (a >= IR_ADC_BASE && a < IR_ADC_BASE + IR_ADC_COUNT) {
            if (!have_adc) fill_adc(adc), have_adc = true;
            *dst = adc[a - IR_ADC_BASE];
        } else if (a >= IR_EDGE_BASE && a < IR_EDGE_BASE + IR_EDGE_STRIDE * REG_GPIO_COUNT) {
            unsigned gpio = (a - IR_EDGE_BASE) / IR_EDGE_STRIDE;
            if (!is_header_pin(gpio)) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
            if ((int)gpio != edge_gpio) {
                uint8_t edges;
                uint16_t debounce_ms;
                uint32_t count, last_ms;
                events_get(gpio, &edges, &debounce_ms, &count, &last_ms);
                put_u32(&edge[0], count);
                put_u32(&edge[2], last_ms);
                edge_gpio = (int)gpio;
            }
            *dst = edge[(a - IR_EDGE_BASE) % IR_EDGE_STRIDE];
        } else if (a >= IR_OTA_BASE && a < IR_OTA_BASE + IR_OTA_COUNT) {
            uint16_t ota[IR_OTA_COUNT] = { (uint16_t)updater_state(), (uint16_t)updater_error() };
            put_u32(&ota[IR_OTA_RECEIVED - IR_OTA_BASE], updater_received());
            ota[IR_OTA_TARGET - IR_OTA_BASE] = (uint16_t)updater_target_partition();
            *dst = ota[a - IR_OTA_BASE];
        } else {
            return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
        }
    }
    return NMBS_ERROR_NONE;
}

// ---- holding registers: read ------------------------------------------------

static void pin_regs(unsigned gpio, uint16_t *r) {
    memset(r, 0, HR_PIN_STRIDE * sizeof *r);
    int idx = board_io_index(gpio);
    const pin_cfg_t *c = gpio_ctl_state(gpio);
    uint8_t edges;
    uint16_t debounce_ms;
    uint32_t count, last_ms;
    events_get(gpio, &edges, &debounce_ms, &count, &last_ms);
    r[PIN_REG_FUNC] = c->func;
    r[PIN_REG_PULL] = c->pull;
    r[PIN_REG_DRIVE] = c->drive;
    r[PIN_REG_FLAGS] = c->flags;
    r[PIN_REG_DUTY] = c->pwm_duty;
    put_u32(&r[PIN_REG_FREQ], c->pwm_freq_hz);
    put_u32(&r[PIN_REG_FADE_MS], shadow[idx].fade_ms);
    r[PIN_REG_PULSE_LEVEL] = shadow[idx].pulse_level;
    put_u32(&r[PIN_REG_PULSE_MS], shadow[idx].pulse_ms);
    r[PIN_REG_EDGES] = edges;
    r[PIN_REG_DEBOUNCE_MS] = debounce_ms;
}

static void default_regs(unsigned gpio, uint16_t *r) {
    memset(r, 0, HR_DEFAULT_STRIDE * sizeof *r);
    const pin_cfg_t *c = app_default((unsigned)board_io_index(gpio));
    r[PIN_REG_FUNC] = c->func;
    r[PIN_REG_PULL] = c->pull;
    r[PIN_REG_DRIVE] = c->drive;
    r[PIN_REG_FLAGS] = c->flags;
    r[PIN_REG_DUTY] = c->pwm_duty;
    put_u32(&r[PIN_REG_FREQ], c->pwm_freq_hz);
}

static void ota_regs(uint16_t *r) {
    memset(r, 0, HR_OTA_COUNT * sizeof *r);
    const updater_image_t *img = updater_image();
    put_u32(&r[OTA_REG_LENGTH], img->length);
    r[OTA_REG_MAJOR] = img->major;
    r[OTA_REG_MINOR] = img->minor;
    put_bytes(&r[OTA_REG_SHA256], img->sha256, 32);
}

static void sys_regs(uint16_t *r) {
    memset(r, 0, HR_SYS_COUNT * sizeof *r);
    r[HR_HEARTBEAT_MS] = app_heartbeat_ms();
}

// A holding register block: where it starts, how many registers, how to
// read it and how to apply a write to it.
typedef struct {
    uint16_t base;
    uint16_t len;
    uint16_t used;     // registers that exist (the rest of `len` reads 0)
    int      gpio;     // -1 if not a pin block
    int      kind;     // 0 sys, 1 pin, 2 default, 3 ota
} block_t;

static bool find_block(uint16_t addr, block_t *b) {
    if (addr < HR_SYS_COUNT) {
        *b = (block_t){ 0, HR_SYS_COUNT, HR_SYS_COUNT, -1, 0 };
        return true;
    }
    if (addr >= HR_PIN_BASE && addr < HR_PIN_BASE + HR_PIN_STRIDE * REG_GPIO_COUNT) {
        unsigned gpio = (addr - HR_PIN_BASE) / HR_PIN_STRIDE;
        if (!is_header_pin(gpio)) return false;
        *b = (block_t){ (uint16_t)(HR_PIN_BASE + gpio * HR_PIN_STRIDE), HR_PIN_STRIDE, PIN_REG_COUNT, (int)gpio, 1 };
        return true;
    }
    if (addr >= HR_DEFAULT_BASE && addr < HR_DEFAULT_BASE + HR_DEFAULT_STRIDE * REG_GPIO_COUNT) {
        unsigned gpio = (addr - HR_DEFAULT_BASE) / HR_DEFAULT_STRIDE;
        if (!is_header_pin(gpio)) return false;
        *b = (block_t){ (uint16_t)(HR_DEFAULT_BASE + gpio * HR_DEFAULT_STRIDE), HR_DEFAULT_STRIDE, DEF_REG_COUNT,
                        (int)gpio, 2 };
        return true;
    }
    if (addr >= HR_OTA_BASE && addr < HR_OTA_BASE + HR_OTA_COUNT) {
        *b = (block_t){ HR_OTA_BASE, HR_OTA_COUNT, HR_OTA_COUNT, -1, 3 };
        return true;
    }
    return false;
}

static void block_read(const block_t *b, uint16_t *r) {
    switch (b->kind) {
    case 0: sys_regs(r); break;
    case 1: pin_regs((unsigned)b->gpio, r); break;
    case 2: default_regs((unsigned)b->gpio, r); break;
    default: ota_regs(r); break;
    }
}

static nmbs_error read_holding_registers(uint16_t address, uint16_t quantity, uint16_t *out, uint8_t unit_id,
                                         void *arg) {
    (void)unit_id;
    (void)arg;
    seen(false);
    uint32_t a = address, end = (uint32_t)address + quantity;
    while (a < end) {
        block_t b;
        if (!find_block((uint16_t)a, &b)) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
        uint16_t r[32];
        block_read(&b, r);
        for (; a < end && a < (uint32_t)b.base + b.len; a++) out[a - address] = r[a - b.base];
    }
    return NMBS_ERROR_NONE;
}

// ---- holding registers: write -----------------------------------------------

#define W(bit) (written & (1u << (bit)))

static nmbs_error apply_sys(const uint16_t *r, uint32_t written) {
    if (W(HR_HEARTBEAT_MS)) app_set_heartbeat_ms(r[HR_HEARTBEAT_MS]);
    if (W(HR_CMD)) {
        if (!W(HR_CMD_KEY) || r[HR_CMD_KEY] != CMD_KEY) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        switch (r[HR_CMD]) {
        case CMD_REBOOT:  pending_action = ACTION_REBOOT; break;
        case CMD_BOOTSEL: pending_action = ACTION_BOOTSEL; break;
        case CMD_SAVE:    return status_to_exception(app_save_settings());
        default:          return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        }
    }
    return NMBS_ERROR_NONE;
}

static bool regs_to_cfg(const uint16_t *r, pin_cfg_t *c) {
    if (r[PIN_REG_FUNC] > PIN_FUNC_ADC || r[PIN_REG_PULL] > PIN_PULL_BUS_KEEP ||
        r[PIN_REG_DRIVE] > PIN_DRIVE_12MA || (r[PIN_REG_FLAGS] & ~PIN_FLAGS_ALL)) {
        return false;
    }
    memset(c, 0, sizeof *c);
    c->func = (uint8_t)r[PIN_REG_FUNC];
    c->pull = (uint8_t)r[PIN_REG_PULL];
    c->drive = (uint8_t)r[PIN_REG_DRIVE];
    c->flags = (uint8_t)r[PIN_REG_FLAGS];
    c->pwm_duty = r[PIN_REG_DUTY];
    c->pwm_freq_hz = get_u32(&r[PIN_REG_FREQ]);
    return true;
}

static nmbs_error apply_pin(unsigned gpio, const uint16_t *r, uint32_t written) {
    int idx = board_io_index(gpio);
    pin_cfg_t *cur = gpio_ctl_state(gpio);
    uint8_t st = ST_OK;

    if (W(PIN_REG_FADE_MS) || W(PIN_REG_FADE_MS + 1)) shadow[idx].fade_ms = get_u32(&r[PIN_REG_FADE_MS]);
    if (W(PIN_REG_PULSE_LEVEL)) shadow[idx].pulse_level = r[PIN_REG_PULSE_LEVEL] ? 1 : 0;

    bool cfg_written = W(PIN_REG_FUNC) || W(PIN_REG_PULL) || W(PIN_REG_DRIVE) || W(PIN_REG_FLAGS);
    bool freq_written = W(PIN_REG_FREQ) || W(PIN_REG_FREQ + 1);
    if (cfg_written) {
        pin_cfg_t cfg;
        if (!regs_to_cfg(r, &cfg)) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        st = gpio_ctl_configure(gpio, &cfg);
    } else if (W(PIN_REG_DUTY) || freq_written) {
        if (cur->func == PIN_FUNC_PWM) {
            st = pwm_ctl_set(gpio, freq_written ? get_u32(&r[PIN_REG_FREQ]) : 0, r[PIN_REG_DUTY],
                             shadow[idx].fade_ms);
        } else {
            // Remembered for when the pin is switched to PWM
            if (freq_written && get_u32(&r[PIN_REG_FREQ]) == 0) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
            cur->pwm_duty = r[PIN_REG_DUTY];
            cur->pwm_freq_hz = get_u32(&r[PIN_REG_FREQ]);
        }
    }
    if (st != ST_OK) return status_to_exception(st);

    if (W(PIN_REG_PULSE_MS) || W(PIN_REG_PULSE_MS + 1)) {
        shadow[idx].pulse_ms = get_u32(&r[PIN_REG_PULSE_MS]);
        if (shadow[idx].pulse_ms) {
            st = gpio_ctl_pulse(gpio, shadow[idx].pulse_level, shadow[idx].pulse_ms);
            if (st != ST_OK) return status_to_exception(st);
        }
    }

    if (W(PIN_REG_EDGES) || W(PIN_REG_DEBOUNCE_MS)) {
        if (r[PIN_REG_EDGES] > 0xFF) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        st = events_config(gpio, (uint8_t)r[PIN_REG_EDGES], r[PIN_REG_DEBOUNCE_MS]);
    }
    return status_to_exception(st);
}

static nmbs_error apply_default(unsigned gpio, const uint16_t *r) {
    pin_cfg_t cfg;
    if (!regs_to_cfg(r, &cfg)) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
    // PWM conflicts depend on what else runs at failsafe time, so only the
    // basic checks apply here.
    uint8_t st = gpio_ctl_validate(gpio, &cfg);
    if (st == ST_CONFLICT) st = ST_OK;
    if (st != ST_OK) return status_to_exception(st);
    *app_default((unsigned)board_io_index(gpio)) = cfg;
    return NMBS_ERROR_NONE;
}

static nmbs_error updater_exception(int err) {
    if (err == UPDATER_ERR_NONE) return NMBS_ERROR_NONE;
    return err == UPDATER_ERR_FLASH ? NMBS_EXCEPTION_SERVER_DEVICE_FAILURE : NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
}

static nmbs_error apply_ota(const uint16_t *r, uint32_t written) {
    if (!W(OTA_REG_CMD)) return NMBS_ERROR_NONE;
    switch (r[OTA_REG_CMD]) {
    case OTA_CMD_BEGIN: {
        updater_image_t img = { get_u32(&r[OTA_REG_LENGTH]), r[OTA_REG_MAJOR], r[OTA_REG_MINOR], { 0 } };
        for (unsigned i = 0; i < 16; i++) {
            img.sha256[2 * i] = (uint8_t)(r[OTA_REG_SHA256 + i] >> 8);
            img.sha256[2 * i + 1] = (uint8_t)r[OTA_REG_SHA256 + i];
        }
        nmbs_error err = updater_exception(updater_begin(&img, boot_ctl_on_trial()));
        printf("ota: begin %lu bytes v%u.%u -> partition %u: %d\n", (unsigned long)img.length, img.major,
               img.minor, updater_target_partition(), updater_error());
        return err;
    }
    case OTA_CMD_VERIFY: {
        nmbs_error err = updater_exception(updater_verify());
        printf("ota: verify %s\n", err == NMBS_ERROR_NONE ? "ok" : "FAILED");
        return err;
    }
    case OTA_CMD_ACTIVATE:
        if (updater_state() != UPDATER_VERIFIED) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        pending_action = ACTION_OTA_ACTIVATE;
        return NMBS_ERROR_NONE;
    case OTA_CMD_ABORT:
        updater_abort();
        return NMBS_ERROR_NONE;
    default:
        return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
    }
}

static nmbs_error write_file_record(uint16_t file_number, uint16_t record_number, const uint16_t *regs,
                                    uint16_t count, uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    seen(true);
    if (record_number * 2u + count * 2u > OTA_SECTOR_SIZE) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    uint8_t data[2 * 122];
    for (unsigned i = 0; i < count; i++) {
        data[2 * i] = (uint8_t)(regs[i] >> 8);
        data[2 * i + 1] = (uint8_t)regs[i];
    }
    uint32_t offset = (uint32_t)(file_number - 1) * OTA_SECTOR_SIZE + record_number * 2u;
    return updater_exception(updater_write(offset, data, count * 2u));
}

static nmbs_error write_registers(uint16_t address, uint16_t quantity, const uint16_t *regs) {
    seen(true);
    uint32_t a = address, end = (uint32_t)address + quantity;
    while (a < end) {
        block_t b;
        if (!find_block((uint16_t)a, &b)) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
        uint16_t r[32];
        uint32_t written = 0;
        block_read(&b, r);
        if (b.kind == 0) r[HR_CMD_KEY] = r[HR_CMD] = 0;
        if (b.kind == 3) r[OTA_REG_CMD] = 0;
        for (; a < end && a < (uint32_t)b.base + b.len; a++) {
            unsigned off = a - b.base;
            if (off >= b.used) continue;   // reserved: ignored
            r[off] = regs[a - address];
            written |= 1u << off;
        }
        nmbs_error err = NMBS_ERROR_NONE;
        switch (b.kind) {
        case 0: err = apply_sys(r, written); break;
        case 1: err = apply_pin((unsigned)b.gpio, r, written); break;
        case 2: err = apply_default((unsigned)b.gpio, r); break;
        default: err = apply_ota(r, written); break;
        }
        if (err != NMBS_ERROR_NONE) return err;
    }
    return NMBS_ERROR_NONE;
}

static nmbs_error write_multiple_registers(uint16_t address, uint16_t quantity, const uint16_t *registers,
                                           uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    return write_registers(address, quantity, registers);
}

static nmbs_error write_single_register(uint16_t address, uint16_t value, uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    return write_registers(address, 1, &value);
}

// ---- coils and discrete inputs ------------------------------------------------

static nmbs_error read_coils(uint16_t address, uint16_t quantity, nmbs_bitfield out, uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    seen(false);
    if ((uint32_t)address + quantity > REG_GPIO_COUNT) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    uint32_t outputs = gpio_ctl_outputs();
    for (uint16_t i = 0; i < quantity; i++) nmbs_bitfield_write(out, i, (outputs >> (address + i)) & 1);
    return NMBS_ERROR_NONE;
}

static nmbs_error read_discrete_inputs(uint16_t address, uint16_t quantity, nmbs_bitfield out, uint8_t unit_id,
                                       void *arg) {
    (void)unit_id;
    (void)arg;
    seen(false);
    if ((uint32_t)address + quantity > REG_GPIO_COUNT) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    uint32_t levels = gpio_ctl_levels();
    for (uint16_t i = 0; i < quantity; i++) nmbs_bitfield_write(out, i, (levels >> (address + i)) & 1);
    return NMBS_ERROR_NONE;
}

static nmbs_error write_coils(uint16_t address, uint16_t quantity, uint32_t values) {
    seen(true);
    if ((uint32_t)address + quantity > REG_GPIO_COUNT) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    uint32_t mask = 0;
    for (uint16_t i = 0; i < quantity; i++) {
        unsigned gpio = address + i;
        if (!is_header_pin(gpio)) return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
        if (gpio_ctl_state(gpio)->func != PIN_FUNC_OUTPUT) return NMBS_EXCEPTION_ILLEGAL_DATA_VALUE;
        mask |= 1u << gpio;
    }
    return status_to_exception(gpio_ctl_write(mask, values << address));
}

static nmbs_error write_single_coil(uint16_t address, bool value, uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    return write_coils(address, 1, value ? 1 : 0);
}

static nmbs_error write_multiple_coils(uint16_t address, uint16_t quantity, const nmbs_bitfield coils,
                                       uint8_t unit_id, void *arg) {
    (void)unit_id;
    (void)arg;
    uint32_t values = 0;
    for (uint16_t i = 0; i < quantity && i < 32; i++) {
        if (nmbs_bitfield_read(coils, i)) values |= 1u << i;
    }
    return write_coils(address, quantity, values);
}

// ---- device identification ------------------------------------------------------

#define DEVID_UID 0x80
#define DEVID_MAC 0x81

static nmbs_error read_device_identification_map(nmbs_bitfield_256 map) {
    for (unsigned id = 0; id <= 6; id++) nmbs_bitfield_set(map, id);
    nmbs_bitfield_set(map, DEVID_UID);
    nmbs_bitfield_set(map, DEVID_MAC);
    return NMBS_ERROR_NONE;
}

static nmbs_error read_device_identification(uint8_t object_id, char buf[NMBS_DEVICE_IDENTIFICATION_STRING_LENGTH]) {
    seen(false);
    const size_t n = NMBS_DEVICE_IDENTIFICATION_STRING_LENGTH;
    uint32_t v = fw_info[2];
    switch (object_id) {
    case 0x00: snprintf(buf, n, "CalTreat"); break;                       // VendorName
    case 0x01: snprintf(buf, n, "lighting-controller"); break;               // ProductCode
    case 0x02: snprintf(buf, n, "%lu.%lu.%lu", (unsigned long)(v >> 16) & 0xFF,
                        (unsigned long)(v >> 8) & 0xFF, (unsigned long)v & 0xFF); break;   // MajorMinorRevision
    case 0x03: snprintf(buf, n, "https://github.com/CoolNamesAllTaken/ctr"); break;   // VendorUrl
    case 0x04: snprintf(buf, n, "CalTreat Lighting Controller"); break;      // ProductName
    case 0x05: snprintf(buf, n, "Waveshare RP2350-ETH"); break;           // ModelName
    case 0x06: snprintf(buf, n, "regmap %d", REGMAP_VERSION); break;      // UserApplicationName
    case DEVID_UID: {
        pico_get_unique_board_id_string(buf, n);
        break;
    }
    case DEVID_MAC: {
        const uint8_t *m = app_net_info()->mac;
        snprintf(buf, n, "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]);
        break;
    }
    default:
        return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    }
    return NMBS_ERROR_NONE;
}

// ---- server ---------------------------------------------------------------------

void modbus_srv_init(void) {
    nmbs_platform_conf platform;
    nmbs_platform_conf_create(&platform);
    platform.transport = NMBS_TRANSPORT_TCP;
    platform.read = mb_read;
    platform.write = mb_write;

    nmbs_callbacks cb;
    nmbs_callbacks_create(&cb);
    cb.read_coils = read_coils;
    cb.read_discrete_inputs = read_discrete_inputs;
    cb.read_holding_registers = read_holding_registers;
    cb.read_input_registers = read_input_registers;
    cb.write_single_coil = write_single_coil;
    cb.write_single_register = write_single_register;
    cb.write_multiple_coils = write_multiple_coils;
    cb.write_multiple_registers = write_multiple_registers;
    cb.read_device_identification = read_device_identification;
    cb.read_device_identification_map = read_device_identification_map;
    cb.write_file_record = write_file_record;

    nmbs_server_create(&nmbs, 0, &platform, &cb);
    nmbs_set_read_timeout(&nmbs, 0);
    nmbs_set_byte_timeout(&nmbs, BYTE_TIMEOUT_MS);
}

// Input that cannot be Modbus TCP: drop it, the client's retry starts clean
static void drop_input(void) {
    modbus_srv_reset();
    bad_frames++;
}

// Reboots requested by the last request run after its response is sent
static void run_pending_action(void) {
    if (pending_action == ACTION_NONE) return;
    link_flush();
    sleep_ms(20);   // let the CH9120 send the response
    switch (pending_action) {
    case ACTION_BOOTSEL:
        boot_ctl_reboot_bootsel();
    case ACTION_OTA_ACTIVATE:
        printf("ota: activating\n");
        stdio_flush();
        updater_activate();   // returns only on failure
        pending_action = ACTION_NONE;
        return;
    default:
        boot_ctl_reboot();
    }
}

void modbus_srv_task(void) {
    uint32_t avail = link_available();
    if (avail == 0) {
        partial = false;
        return;
    }
    if (avail >= MBAP_LEN) {
        uint8_t hdr[MBAP_LEN];
        link_peek(hdr, MBAP_LEN);
        uint16_t protocol = (uint16_t)((hdr[2] << 8) | hdr[3]);
        uint16_t len = (uint16_t)((hdr[4] << 8) | hdr[5]);
        if (protocol != 0 || len < 2 || len > 254) {
            drop_input();
            return;
        }
        if (avail >= MBAP_LEN + (uint32_t)len) {
            partial = false;
            if (nmbs_server_poll(&nmbs) != NMBS_ERROR_NONE) drop_input();
            else run_pending_action();
            return;
        }
    }
    // Incomplete frame: wait for the rest, but not forever
    if (!partial) {
        partial = true;
        partial_since = get_absolute_time();
    } else if (absolute_time_diff_us(partial_since, get_absolute_time()) > PARTIAL_TIMEOUT_MS * 1000) {
        drop_input();
    }
}

void modbus_srv_reset(void) {
    link_reset();
    partial = false;
}
