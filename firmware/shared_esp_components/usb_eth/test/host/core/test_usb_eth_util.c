/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Host-side unit tests for usb_eth_util.c. Build and run with `make` in
 * test/host (plain gcc, no ESP-IDF needed).
 */
#include <stdlib.h>
#include <string.h>
#include "usb_eth_util.h"
#include "test_harness.h"

static void test_round_up_and_mps(void)
{
    CHECK_EQ(usb_eth_round_up(0, 64), 0);
    CHECK_EQ(usb_eth_round_up(1, 64), 64);
    CHECK_EQ(usb_eth_round_up(64, 64), 64);
    CHECK_EQ(usb_eth_round_up(65, 64), 128);
    CHECK_EQ(usb_eth_round_up(100, 60), 120);
    CHECK_EQ(usb_eth_round_up(5, 0), 5);
    CHECK_EQ(usb_eth_ep_mps(0x0040), 64);
    CHECK_EQ(usb_eth_ep_mps(0x0200), 512);
    CHECK_EQ(usb_eth_ep_mps(0x1200), 512);     /* HS mult bits stripped */
}

static void test_recovery_limiter(void)
{
    usb_eth_recovery_t r;
    usb_eth_recovery_init(&r, 3);
    /* three recoveries allowed, the fourth in a row is refused */
    CHECK(usb_eth_recovery_attempt(&r));
    CHECK(usb_eth_recovery_attempt(&r));
    CHECK(usb_eth_recovery_attempt(&r));
    CHECK(!usb_eth_recovery_attempt(&r));
    /* giving up is sticky: a late success does not re-arm it */
    usb_eth_recovery_success(&r);
    CHECK(!usb_eth_recovery_attempt(&r));
    CHECK(r.gave_up);
    /* a new device starts over */
    usb_eth_recovery_reset(&r);
    CHECK(!r.gave_up);
    CHECK_EQ(r.max, 3);
    /* successes in between keep resetting the streak */
    for (int i = 0; i < 10; i++) {
        CHECK(usb_eth_recovery_attempt(&r));
        CHECK(usb_eth_recovery_attempt(&r));
        usb_eth_recovery_success(&r);
    }
    CHECK(!r.gave_up);
    /* max 0: never recover */
    usb_eth_recovery_init(&r, 0);
    CHECK(!usb_eth_recovery_attempt(&r));
}

static void test_addr_set(void)
{
    usb_eth_addr_set_t s;
    usb_eth_addr_set_clear_all(&s);
    for (unsigned a = 0; a < 256; a++) {
        CHECK(!usb_eth_addr_set_contains(&s, (uint8_t)a));
    }
    usb_eth_addr_set_add(&s, 1);
    usb_eth_addr_set_add(&s, 31);
    usb_eth_addr_set_add(&s, 32);
    usb_eth_addr_set_add(&s, 127);
    usb_eth_addr_set_add(&s, 200);   /* out of range: ignored */
    CHECK(usb_eth_addr_set_contains(&s, 1));
    CHECK(usb_eth_addr_set_contains(&s, 31));
    CHECK(usb_eth_addr_set_contains(&s, 32));
    CHECK(usb_eth_addr_set_contains(&s, 127));
    CHECK(!usb_eth_addr_set_contains(&s, 2));
    CHECK(!usb_eth_addr_set_contains(&s, 33));
    CHECK(!usb_eth_addr_set_contains(&s, 200));
    usb_eth_addr_set_remove(&s, 32);
    CHECK(!usb_eth_addr_set_contains(&s, 32));
    CHECK(usb_eth_addr_set_contains(&s, 31));
    usb_eth_addr_set_remove(&s, 250);  /* out of range: no effect */
    usb_eth_addr_set_clear_all(&s);
    CHECK(!usb_eth_addr_set_contains(&s, 127));
}

static void test_synthetic_mii(void)
{
    uint16_t r[32];
    memset(r, 0xa5, sizeof(r));
    usb_eth_mii_idle(r);
    /* the values the RTL8152 driver has always answered with when no dongle is attached */
    CHECK_EQ(r[USB_ETH_MII_BMCR], 0x1000);
    CHECK_EQ(r[USB_ETH_MII_BMSR], 0x7829);
    CHECK_EQ(r[USB_ETH_MII_PHYID1], 0x001c);
    CHECK_EQ(r[USB_ETH_MII_PHYID2], 0xc800);
    CHECK_EQ(r[USB_ETH_MII_ANAR], 0x01e1);
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0);
    CHECK_EQ(r[31], 0);
    usb_eth_mii_set_link(r, true, true, 100);
    CHECK_EQ(r[USB_ETH_MII_BMSR], 0x782d);
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0x0101);
    usb_eth_mii_set_link(r, true, false, 1000);     /* gigabit: reported as 100 */
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0x0081);
    usb_eth_mii_set_link(r, true, true, 10);
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0x0041);
    usb_eth_mii_set_link(r, true, false, 10);
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0x0021);
    usb_eth_mii_set_link(r, false, true, 100);
    CHECK_EQ(r[USB_ETH_MII_BMSR], 0x7829);
    CHECK_EQ(r[USB_ETH_MII_ANLPAR], 0);
    CHECK_EQ(r[USB_ETH_MII_ANAR], 0x01e1);
}

int main(void)
{
    RUN(test_round_up_and_mps);
    RUN(test_recovery_limiter);
    RUN(test_addr_set);
    RUN(test_synthetic_mii);
    return REPORT();
}
