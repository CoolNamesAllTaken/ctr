// Boot ROM A/B partitions and try-before-you-buy (TBYB).
//
// The ROM boots the newer of app_a / app_b. After an update the app reboots
// with a "flash update" boot into the new image, which then runs on trial:
// it only becomes the image the ROM boots normally once it calls
// rom_explicit_buy(). Any reboot before that (crash, watchdog, the trial
// deadline below, power loss) falls back to the previous image.
//
// The ROM enforces the trial with a 16.7 s watchdog, but the app replaces
// that timeout with its own as soon as it enables the watchdog, so the
// deadline is enforced here in software instead.
//
// An image sent over the network (OTA_MARKER set by the updater before the
// reboot) buys only once the host has reached it again, which proves the
// new firmware can still be updated; the previous image stays as fallback.
// An image loaded over USB buys at once and invalidates the other partition:
// a USB install replaces the firmware (and repeated development uploads of
// the same version cannot leave the ROM choosing between two equal ones).

#include <stdio.h>

#include "app.h"
#include "hardware/flash.h"
#include "hardware/structs/watchdog.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"

#define TRIAL_TIMEOUT_MS 60000

static boot_info_t info;
static bool info_valid;
static bool trial;              // running unbought
static bool ota_trial;          // trial started by a network update
static bool usb_install;        // bought a USB-loaded image this boot
static int buy_rc = 1;          // result of the last buy attempt (1 = none)
static uint8_t reset_reason;
static absolute_time_t trial_deadline;
#if !OTA_TEST_NEVER_BUY
static uint8_t buy_workarea[4096] __attribute__((aligned(4)));
#endif

static uint8_t classify_reset(void) {
    uint32_t req = watchdog_hw->scratch[SCRATCH_REQUEST];
    watchdog_hw->scratch[SCRATCH_REQUEST] = 0;
    if (!watchdog_caused_reboot()) return RESET_POWER_ON;
    if (req == REQUEST_REBOOT) return RESET_REQUESTED;
    return RESET_WATCHDOG;
}

static void buy(void) {
#if OTA_TEST_NEVER_BUY
    buy_rc = -1;
#else
    buy_rc = rom_explicit_buy(buy_workarea, sizeof buy_workarea);
    if (buy_rc == BOOTROM_OK) trial = false;
#endif
    printf("boot: explicit_buy rc=%d\n", buy_rc);
}

void boot_ctl_init(void) {
    reset_reason = classify_reset();
    uint32_t marker = watchdog_hw->scratch[SCRATCH_OTA];
    watchdog_hw->scratch[SCRATCH_OTA] = 0;

    info_valid = rom_get_boot_info(&info);
    trial = info_valid && info.boot_type == BOOT_TYPE_FLASH_UPDATE &&
            (info.tbyb_and_update_info & BOOT_TBYB_AND_UPDATE_FLAG_BUY_PENDING);
    if (!trial) return;
    trial_deadline = make_timeout_time_ms(TRIAL_TIMEOUT_MS);
    ota_trial = marker == OTA_MARKER;
    if (!ota_trial) {
        buy();
        usb_install = buy_rc == BOOTROM_OK;
    }
}

void boot_ctl_finish_install(void) {
    if (!usb_install) return;
    unsigned other = boot_ctl_partition() == PARTITION_APP_B ? PARTITION_APP_A : PARTITION_APP_B;
    bool ok = flash_erase(app_partition_offset(other), FLASH_SECTOR_SIZE);
    printf("boot: USB install, other partition %s\n", ok ? "invalidated" : "NOT invalidated");
}

void boot_ctl_host_seen(void) {
    if (trial && ota_trial && buy_rc == 1) buy();
}

void boot_ctl_task(void) {
    if (trial && time_reached(trial_deadline)) {
        printf("boot: trial not bought in time, rolling back\n");
        boot_ctl_reboot();
    }
}

bool boot_ctl_on_trial(void) { return trial; }

int boot_ctl_partition(void) { return info_valid ? info.partition : -1; }

uint8_t boot_ctl_boot_type(void) { return info_valid ? info.boot_type : 0xFF; }

uint8_t boot_ctl_tbyb_info(void) { return info_valid ? info.tbyb_and_update_info : 0; }

uint8_t boot_ctl_reset_reason(void) { return reset_reason; }

void boot_ctl_print(void) {
    printf("boot: up %lu ms fw %lu.%lu.%lu partition %d boot_type %d tbyb 0x%02x reset %u trial %d (ota %d) buy_rc %d\n",
           (unsigned long)to_ms_since_boot(get_absolute_time()),
           (unsigned long)(FW_VERSION >> 16) & 0xFF, (unsigned long)(FW_VERSION >> 8) & 0xFF,
           (unsigned long)FW_VERSION & 0xFF, boot_ctl_partition(), (int)boot_ctl_boot_type(),
           boot_ctl_tbyb_info(), reset_reason, trial, ota_trial, buy_rc);
}

void boot_ctl_reboot(void) {
    stdio_flush();
    watchdog_hw->scratch[SCRATCH_REQUEST] = REQUEST_REBOOT;
    watchdog_reboot(0, 0, 10);
    while (true) tight_loop_contents();
}

void boot_ctl_reboot_bootsel(void) {
    stdio_flush();
    rom_reset_usb_boot(0, 0);
}
