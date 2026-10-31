/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Helpers shared by the usb_eth core and its drivers. No ESP-IDF dependencies,
 * so they are unit tested on the host (test/host/core).
 */
#include <string.h>
#include "usb_eth_util.h"

size_t usb_eth_round_up(size_t v, size_t align)
{
    if (align == 0) {
        return v;
    }
    return ((v + align - 1) / align) * align;
}

/* ---------------------------------------------------------------------------
 * USB sizing
 * ------------------------------------------------------------------------- */

uint16_t usb_eth_ep_mps(uint16_t wMaxPacketSize)
{
    return (uint16_t)(wMaxPacketSize & 0x07ff);
}

bool usb_eth_tx_needs_zlp(size_t total, uint16_t mps)
{
    return mps != 0 && total != 0 && (total % mps) == 0;
}

/* ---------------------------------------------------------------------------
 * Station address
 * ------------------------------------------------------------------------- */

bool usb_eth_mac_is_valid(const uint8_t mac[USB_ETH_ADDR_LEN])
{
    static const uint8_t zero[USB_ETH_ADDR_LEN];
    return !(mac[0] & 0x01) && memcmp(mac, zero, USB_ETH_ADDR_LEN) != 0;
}

/* ---------------------------------------------------------------------------
 * Endpoint recovery limiter
 * ------------------------------------------------------------------------- */

void usb_eth_recovery_init(usb_eth_recovery_t *r, unsigned max)
{
    r->streak = 0;
    r->max = max;
    r->gave_up = false;
}

void usb_eth_recovery_success(usb_eth_recovery_t *r)
{
    if (!r->gave_up) {
        r->streak = 0;
    }
}

bool usb_eth_recovery_attempt(usb_eth_recovery_t *r)
{
    if (r->gave_up) {
        return false;
    }
    if (++r->streak > r->max) {
        r->gave_up = true;
        return false;
    }
    return true;
}

void usb_eth_recovery_reset(usb_eth_recovery_t *r)
{
    usb_eth_recovery_init(r, r->max);
}

/* ---------------------------------------------------------------------------
 * USB device address set
 * ------------------------------------------------------------------------- */

void usb_eth_addr_set_clear_all(usb_eth_addr_set_t *s)
{
    memset(s, 0, sizeof(*s));
}

void usb_eth_addr_set_add(usb_eth_addr_set_t *s, uint8_t addr)
{
    if (addr < 128) {
        s->bits[addr >> 5] |= 1u << (addr & 31);
    }
}

void usb_eth_addr_set_remove(usb_eth_addr_set_t *s, uint8_t addr)
{
    if (addr < 128) {
        s->bits[addr >> 5] &= ~(1u << (addr & 31));
    }
}

bool usb_eth_addr_set_contains(const usb_eth_addr_set_t *s, uint8_t addr)
{
    return addr < 128 && (s->bits[addr >> 5] & (1u << (addr & 31))) != 0;
}

/* ---------------------------------------------------------------------------
 * Synthetic MII registers
 * ------------------------------------------------------------------------- */

void usb_eth_mii_idle(uint16_t regs[32])
{
    memset(regs, 0, 32 * sizeof(regs[0]));
    regs[USB_ETH_MII_BMCR] = USB_ETH_MII_BMCR_ANEG_EN;
    /* 10/100 HD/FD, autoneg able, extended caps, link down. Autoneg complete is
     * set so that an autoneg restart without a device (esp_eth_start) returns at
     * once instead of polling for the 802.3 driver's 4 s timeout. */
    regs[USB_ETH_MII_BMSR] = 0x7829;
    regs[USB_ETH_MII_PHYID1] = 0x001c;
    regs[USB_ETH_MII_PHYID2] = 0xc800;
    regs[USB_ETH_MII_ANAR] = 0x01e1;
}

void usb_eth_mii_set_link(uint16_t regs[32], bool up, bool full_duplex, unsigned mbps)
{
    uint16_t lpa = 0;
    if (up) {
        if (mbps >= 100) {
            lpa = full_duplex ? 0x0100 : 0x0080;
        } else {
            lpa = full_duplex ? 0x0040 : 0x0020;
        }
        lpa |= 0x0001;      /* selector: IEEE 802.3 */
    }
    regs[USB_ETH_MII_BMSR] = (uint16_t)((regs[USB_ETH_MII_BMSR] & ~USB_ETH_MII_BMSR_LINK) |
                                        (up ? USB_ETH_MII_BMSR_LINK : 0));
    regs[USB_ETH_MII_ANLPAR] = lpa;
}
