// Settings in the "data" partition, plus raw flash access.
//
// Data partition sectors 0 and 1 hold the settings record, written
// alternately. Each copy carries a sequence number and CRC32; the newest
// valid copy wins, so losing power during a write keeps the previous one.

#include <stddef.h>
#include <string.h>

#include "app.h"
#include "hardware/flash.h"
#include "hardware/structs/qmi.h"
#include "hardware/xip_cache.h"
#include "pico/flash.h"
#include "pico/sha256.h"

#define META_MAGIC      0x4154454Du   // "META"
#define META_LAYOUT     4
#define META_SECTOR(i)  (FLASH_DATA_OFFSET + (i) * FLASH_SECTOR_SIZE)

#define META_BUF_SIZE   ((sizeof(meta_t) + FLASH_PAGE_SIZE - 1) & ~(FLASH_PAGE_SIZE - 1))
_Static_assert(META_BUF_SIZE <= FLASH_SECTOR_SIZE, "meta record too large");

static int current_sector = -1;   // sector holding the newest valid record
static uint32_t protected_offset;  // running app partition
static uint8_t page_buf[FLASH_PAGE_SIZE] __attribute__((aligned(4)));

static uint32_t crc32(const void *data, uint32_t len) {
    const uint8_t *p = data;
    uint32_t crc = 0xFFFFFFFFu;
    while (len--) {
        crc ^= *p++;
        for (int i = 0; i < 8; i++) crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1));
    }
    return ~crc;
}

void flashstore_init(uint32_t running_app_offset) {
    protected_offset = running_app_offset;
    // QMI translation window 1 (0x10400000) -> physical flash 0..4MB
    qmi_hw->atrans[1] = (0x400u << QMI_ATRANS1_SIZE_LSB) | 0u;
    xip_cache_invalidate_all();
}

static void defaults(meta_t *m) {
    memset(m, 0, sizeof *m);
    m->magic = META_MAGIC;
    m->layout = META_LAYOUT;
    m->heartbeat_ms = 3000;
    for (int i = 0; i < BOARD_IO_PIN_COUNT; i++) {
        m->pins[i].func = PIN_FUNC_OFF;
        m->pins[i].pull = PIN_PULL_NONE;
        m->pins[i].drive = PIN_DRIVE_4MA;
        m->pins[i].flags = PIN_FLAG_SCHMITT;
        // Low enough for the MOSFET module's slow optocouplers (see README)
        m->pins[i].pwm_freq_hz = 200;
    }
}

static bool record_valid(const meta_t *m) {
    return m->magic == META_MAGIC && m->layout == META_LAYOUT &&
           m->crc == crc32(m, offsetof(meta_t, crc));
}

void flashstore_load(meta_t *m) {
    const meta_t *r0 = (const meta_t *)flash_raw(META_SECTOR(0));
    const meta_t *r1 = (const meta_t *)flash_raw(META_SECTOR(1));
    bool v0 = record_valid(r0), v1 = record_valid(r1);
    if (v0 && (!v1 || (int32_t)(r0->seq - r1->seq) > 0)) {
        memcpy(m, r0, sizeof *m);
        current_sector = 0;
    } else if (v1) {
        memcpy(m, r1, sizeof *m);
        current_sector = 1;
    } else {
        defaults(m);
        current_sector = -1;
    }
}

bool flashstore_save(meta_t *m) {
    static uint8_t buf[META_BUF_SIZE] __attribute__((aligned(4)));
    int target = current_sector == 0 ? 1 : 0;
    m->magic = META_MAGIC;
    m->layout = META_LAYOUT;
    m->seq++;
    m->crc = crc32(m, offsetof(meta_t, crc));
    memset(buf, 0xFF, sizeof buf);
    memcpy(buf, m, sizeof *m);
    if (!flash_erase(META_SECTOR(target), FLASH_SECTOR_SIZE)) return false;
    if (!flash_program(META_SECTOR(target), buf, sizeof buf)) return false;
    if (memcmp(flash_raw(META_SECTOR(target)), m, sizeof *m) != 0) return false;
    current_sector = target;
    return true;
}

// ---- raw flash ----------------------------------------------------------

typedef struct {
    uint32_t       offset;
    const uint8_t *data;
    uint32_t       len;
} flash_op_t;

static void do_erase(void *p) {
    const flash_op_t *op = p;
    flash_range_erase(op->offset, op->len);
}

static void do_program(void *p) {
    const flash_op_t *op = p;
    flash_range_program(op->offset, op->data, op->len);
}

static bool in_writable_range(uint32_t offset, uint32_t len) {
    // Never touch the partition table or the image we are running from.
    if (offset < FLASH_PT_SIZE || len > FLASH_TOTAL_SIZE - offset) return false;
    return offset + len <= protected_offset || offset >= protected_offset + FLASH_APP_SIZE;
}

bool flash_erase(uint32_t offset, uint32_t len) {
    if (!in_writable_range(offset, len) || (offset | len) & (FLASH_SECTOR_SIZE - 1)) return false;
    flash_op_t op = { offset, NULL, len };
    return flash_safe_execute(do_erase, &op, UINT32_MAX) == PICO_OK;
}

bool flash_program(uint32_t offset, const void *data, uint32_t len) {
    if (!in_writable_range(offset, len) || (offset & (FLASH_PAGE_SIZE - 1))) return false;
    const uint8_t *src = data;
    uint32_t whole = len & ~(FLASH_PAGE_SIZE - 1);
    if (whole) {
        flash_op_t op = { offset, src, whole };
        if (flash_safe_execute(do_program, &op, UINT32_MAX) != PICO_OK) return false;
    }
    if (len > whole) {
        memset(page_buf, 0xFF, sizeof page_buf);
        memcpy(page_buf, src + whole, len - whole);
        flash_op_t op = { offset + whole, page_buf, FLASH_PAGE_SIZE };
        if (flash_safe_execute(do_program, &op, UINT32_MAX) != PICO_OK) return false;
    }
    return true;
}

bool flash_sha256(uint32_t offset, uint32_t len, uint8_t out[32]) {
    pico_sha256_state_t st;
    if (pico_sha256_try_start(&st, SHA256_BIG_ENDIAN, false) != PICO_OK) return false;
    const uint8_t *p = flash_raw(offset);
    while (len) {
        uint32_t n = len > 4096 ? 4096 : len;
        pico_sha256_update_blocking(&st, p, n);
        p += n;
        len -= n;
    }
    sha256_result_t r;
    pico_sha256_finish(&st, &r);
    memcpy(out, r.bytes, 32);
    return true;
}
