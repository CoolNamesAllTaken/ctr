// Firmware update into the app partition we are not running from.
//
// The host announces the image (BEGIN: length, version, SHA-256), streams
// it in order, has it checked (VERIFY) and then starts it (ACTIVATE): a
// boot ROM "flash update" reboot into the new partition, where the image
// runs on trial until it buys itself (see boot_ctl.c).
//
// The image's first sector is held in RAM and only written by VERIFY, so an
// interrupted transfer never leaves a bootable half image.

#include <string.h>

#include "app.h"
#include "boot/picoboot_constants.h"
#include "hardware/flash.h"
#include "hardware/structs/watchdog.h"
#include "pico/bootrom.h"

static struct {
    int             state;
    int             error;
    unsigned        running;
    updater_image_t img;
    uint32_t        received;   // bytes accepted so far
} up;

static uint8_t first_sector[FLASH_SECTOR_SIZE] __attribute__((aligned(4)));
static uint8_t sector_buf[FLASH_SECTOR_SIZE] __attribute__((aligned(4)));

static uint32_t target_offset(void) {
    return app_partition_offset(updater_target_partition());
}

static int fail(int err) {
    up.state = UPDATER_FAILED;
    up.error = err;
    return err;
}

void updater_init(unsigned running_partition) {
    memset(&up, 0, sizeof up);
    up.running = running_partition;
}

unsigned updater_target_partition(void) {
    return up.running == PARTITION_APP_A ? PARTITION_APP_B : PARTITION_APP_A;
}

int updater_begin(const updater_image_t *img, bool on_trial) {
    up.state = UPDATER_IDLE;
    up.error = UPDATER_ERR_NONE;
    up.received = 0;
    if (on_trial) return fail(UPDATER_ERR_ON_TRIAL);
    if (img->length == 0 || img->length > FLASH_APP_SIZE) return fail(UPDATER_ERR_LENGTH);
    // Image versions as sealed by scripts/build_image.py: major, (minor << 8) | patch
    if (img->major == ((FW_VERSION >> 16) & 0xFF) && img->minor == (FW_VERSION & 0xFFFF)) {
        return fail(UPDATER_ERR_SAME_VERSION);
    }
    up.img = *img;
    // Whatever image the target held must not boot any more
    if (!flash_erase(target_offset(), FLASH_SECTOR_SIZE)) return fail(UPDATER_ERR_FLASH);
    up.state = UPDATER_RECEIVING;
    return UPDATER_ERR_NONE;
}

// Write the sector that ends at `up.received` (sector 0 stays in RAM).
static bool flush_sector(void) {
    uint32_t start = (up.received - 1) & ~(FLASH_SECTOR_SIZE - 1);
    if (start == 0) return true;
    uint32_t n = up.received - start;
    memset(sector_buf + n, 0xFF, FLASH_SECTOR_SIZE - n);
    return flash_erase(target_offset() + start, FLASH_SECTOR_SIZE) &&
           flash_program(target_offset() + start, sector_buf, FLASH_SECTOR_SIZE);
}

int updater_write(uint32_t offset, const uint8_t *data, uint32_t len) {
    if (up.state != UPDATER_RECEIVING) return UPDATER_ERR_STATE;
    if (offset + len <= up.received) return UPDATER_ERR_NONE;   // already have it
    if (offset != up.received) return UPDATER_ERR_SEQUENCE;     // gap or overlap: host restarts
    if (len > up.img.length - offset) len = up.img.length - offset;   // padding
    while (len) {
        uint32_t in_sector = up.received & (FLASH_SECTOR_SIZE - 1);
        uint32_t n = FLASH_SECTOR_SIZE - in_sector;
        if (n > len) n = len;
        uint8_t *buf = up.received < FLASH_SECTOR_SIZE ? first_sector : sector_buf;
        memcpy(buf + in_sector, data, n);
        up.received += n;
        data += n;
        len -= n;
        bool sector_full = (up.received & (FLASH_SECTOR_SIZE - 1)) == 0;
        if ((sector_full || up.received == up.img.length) && !flush_sector()) return fail(UPDATER_ERR_FLASH);
    }
    return UPDATER_ERR_NONE;
}

int updater_verify(void) {
    if (up.state == UPDATER_VERIFIED) return UPDATER_ERR_NONE;
    if (up.state != UPDATER_RECEIVING || up.received != up.img.length) return UPDATER_ERR_STATE;
    uint32_t n = up.img.length < FLASH_SECTOR_SIZE ? up.img.length : FLASH_SECTOR_SIZE;
    memset(first_sector + n, 0xFF, FLASH_SECTOR_SIZE - n);
    if (!flash_program(target_offset(), first_sector, FLASH_SECTOR_SIZE)) return fail(UPDATER_ERR_FLASH);
    uint8_t sha[32];
    if (!flash_sha256(target_offset(), up.img.length, sha) || memcmp(sha, up.img.sha256, 32) != 0) {
        flash_erase(target_offset(), FLASH_SECTOR_SIZE);
        return fail(UPDATER_ERR_HASH);
    }
    up.state = UPDATER_VERIFIED;
    return UPDATER_ERR_NONE;
}

void updater_abort(void) {
    if (up.state == UPDATER_RECEIVING) flash_erase(target_offset(), FLASH_SECTOR_SIZE);
    up.state = UPDATER_IDLE;
    up.error = UPDATER_ERR_NONE;
    up.received = 0;
}

int updater_activate(void) {
    if (up.state != UPDATER_VERIFIED) return UPDATER_ERR_STATE;
    // Tells the new image that the host sent it (buy only once the host is
    // back); survives the watchdog reboot.
    watchdog_hw->scratch[SCRATCH_OTA] = OTA_MARKER;
    int rc = rom_reboot(REBOOT2_FLAG_REBOOT_TYPE_FLASH_UPDATE, 100, XIP_BASE + target_offset(), 0);
    if (rc != BOOTROM_OK) {
        watchdog_hw->scratch[SCRATCH_OTA] = 0;
        return fail(UPDATER_ERR_FLASH);
    }
    while (true) tight_loop_contents();
}

int updater_state(void) { return up.state; }
int updater_error(void) { return up.error; }
uint32_t updater_received(void) { return up.received; }
const updater_image_t *updater_image(void) { return &up.img; }
