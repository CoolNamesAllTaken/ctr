/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Helpers shared by the usb_eth core and its drivers (no ESP-IDF dependencies).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define USB_ETH_ADDR_LEN            6

static inline uint32_t usb_eth_get_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static inline void usb_eth_put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

/** Round @p v up to a multiple of @p align (align > 0, need not be a power of 2). */
size_t usb_eth_round_up(size_t v, size_t align);

/* ---------------------------------------------------------------------------
 * USB endpoints
 * ------------------------------------------------------------------------- */

/** Max packet size from a raw wMaxPacketSize field (strips the HS mult bits). */
uint16_t usb_eth_ep_mps(uint16_t wMaxPacketSize);

/** True if a bulk-OUT transfer of @p total bytes must be followed by a ZLP. */
bool usb_eth_tx_needs_zlp(size_t total, uint16_t mps);

/** One piece of a frame to transmit (esp_eth hands frames over in up to 8 pieces). */
typedef struct {
    const void *data;
    size_t len;
} usb_eth_iov_t;

/** True for a usable station address: not all zero, not multicast/broadcast. */
bool usb_eth_mac_is_valid(const uint8_t mac[USB_ETH_ADDR_LEN]);

/* ---------------------------------------------------------------------------
 * Endpoint recovery limiter
 *
 * A bulk endpoint that fails (STALL, transaction error) is cleared and
 * restarted, but a device that fails every transfer must not keep the worker
 * busy forever: after `max` recoveries without a successful transfer in
 * between, recovery is refused until usb_eth_recovery_reset().
 * ------------------------------------------------------------------------- */

typedef struct {
    unsigned streak;    /* recoveries since the last successful transfer */
    unsigned max;
    bool gave_up;
} usb_eth_recovery_t;

void usb_eth_recovery_init(usb_eth_recovery_t *r, unsigned max);
/** A transfer completed successfully. Ends the streak unless already given up. */
void usb_eth_recovery_success(usb_eth_recovery_t *r);
/** Ask to recover once more. @return false once the limit is exceeded (sticky). */
bool usb_eth_recovery_attempt(usb_eth_recovery_t *r);
/** New device: start over. */
void usb_eth_recovery_reset(usb_eth_recovery_t *r);

/* ---------------------------------------------------------------------------
 * USB device address set (addresses 1..127)
 * ------------------------------------------------------------------------- */

typedef struct {
    uint32_t bits[4];
} usb_eth_addr_set_t;

void usb_eth_addr_set_clear_all(usb_eth_addr_set_t *s);
void usb_eth_addr_set_add(usb_eth_addr_set_t *s, uint8_t addr);
void usb_eth_addr_set_remove(usb_eth_addr_set_t *s, uint8_t addr);
bool usb_eth_addr_set_contains(const usb_eth_addr_set_t *s, uint8_t addr);

/* ---------------------------------------------------------------------------
 * MII register set the core answers with when there is no MDIO PHY to read:
 * without a device (link down), or for a driver without mdio_read whose link
 * comes from usb_eth_report_link(). The esp_eth IEEE 802.3 PHY driver derives
 * link, speed and duplex from BMSR and ANAR & ANLPAR.
 * ------------------------------------------------------------------------- */

#define USB_ETH_MII_BMCR            0
#define USB_ETH_MII_BMSR            1
#define USB_ETH_MII_PHYID1          2
#define USB_ETH_MII_PHYID2          3
#define USB_ETH_MII_ANAR            4
#define USB_ETH_MII_ANLPAR          5
#define USB_ETH_MII_BMCR_RESET      0x8000
#define USB_ETH_MII_BMCR_ANEG_EN    0x1000
#define USB_ETH_MII_BMSR_LINK       0x0004

/** Link down, autonegotiation enabled and complete, 10/100 HD/FD advertised. */
void usb_eth_mii_idle(uint16_t regs[32]);

/** Set the link bit and a link partner ability matching @p mbps / @p full_duplex
 *  (anything above 100 Mbit/s is reported as 100, the most the PHY driver knows). */
void usb_eth_mii_set_link(uint16_t regs[32], bool up, bool full_duplex, unsigned mbps);

#ifdef __cplusplus
}
#endif
