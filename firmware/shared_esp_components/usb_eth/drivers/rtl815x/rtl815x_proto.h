/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * RTL815x (RTL8152/RTL8153) wire-format and sizing logic with no ESP-IDF dependencies, so it can
 * be unit tested on the host (see test/host/).
 *
 * Wire formats (as used by FreeBSD ure(4), struct ure_rxpkt / ure_txpkt):
 *   RX: every frame in a bulk-IN stream is preceded by a 24-byte descriptor
 *       (six little-endian 32-bit words; word 0 bits 14..0 = frame length
 *       including the 4-byte FCS). The next descriptor starts at the next
 *       8-byte boundary. One bulk transfer can carry many frames (RX
 *       aggregation) and a frame may continue in the next transfer when the
 *       host buffer fills up.
 *   TX: every frame is preceded by an 8-byte descriptor (word 0 = length |
 *       first-segment | last-segment, word 1 = checksum/VLAN offload = 0).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "usb_eth_util.h"

#ifdef __cplusplus
extern "C" {
#endif

#define RTL815X_RX_DESC_LEN         24
#define RTL815X_RX_ALIGN            8
#define RTL815X_RX_LEN_MASK         0x7fffu
#define RTL815X_TX_DESC_LEN         8
#define RTL815X_TX_ALIGN            4
#define RTL815X_TX_FS               (1u << 31)
#define RTL815X_TX_LS               (1u << 30)
#define RTL815X_TX_LEN_MASK         0xffffu

#define RTL815X_ETH_HDR_LEN         14
#define RTL815X_ETH_FCS_LEN         4
#define RTL815X_ETH_VLAN_LEN        4
#define RTL815X_ETH_MIN_FRAME       60      /* without FCS */
/* Largest frame on the wire without FCS: 1500 MTU + header + one VLAN tag. */
#define RTL815X_ETH_MAX_FRAME       (1500 + RTL815X_ETH_HDR_LEN + RTL815X_ETH_VLAN_LEN)
/* Largest frame the RX parser accepts, as counted by the descriptor (with FCS). */
#define RTL815X_RX_MAX_FRAME        (RTL815X_ETH_MAX_FRAME + RTL815X_ETH_FCS_LEN)

/* ---------------------------------------------------------------------------
 * Chip variants (PLA_TCR1 version bits)
 * ------------------------------------------------------------------------- */

typedef enum {
    RTL815X_FAMILY_8152,        /* RTL8152: 10/100, USB 2.0 */
    RTL815X_FAMILY_8153B,       /* RTL8153B: 10/100/1000, USB 3.0 (2.0 on the ESP) */
} rtl815x_family_t;

typedef struct {
    uint16_t ver;               /* PLA_TCR1 & RTL815X_VERSION_MASK */
    const char *name;
    rtl815x_family_t family;
} rtl815x_chip_t;

/** The supported chip with this version, or NULL (RTL8153A 0x5cx0, RTL8156: not supported). */
const rtl815x_chip_t *rtl815x_chip_lookup(uint16_t ver);

/**
 * USB_RX_EARLY_SIZE for the RTL8153B (8-byte units): the chip closes an RX
 * aggregate once it holds this much, leaving room for one more maximum-size
 * record (descriptor + frame + alignment) in a @p rx_size transfer, as FreeBSD
 * ure_init does for its 32 KiB buffers. 0 if @p rx_size is smaller than that.
 */
uint16_t rtl815x_rx_early_size(size_t rx_size);

/* ---------------------------------------------------------------------------
 * USB buffer sizing (depends on the endpoint max packet size, 64 at FS and
 * 512 at HS; never hard-coded)
 * ------------------------------------------------------------------------- */

/**
 * Size of a bulk-IN transfer. IDF requires IN transfer lengths to be a multiple
 * of MPS; the RX parser additionally needs them to be a multiple of 8 so the
 * 8-byte descriptor alignment carries across transfers. The result is at least
 * one descriptor + one maximum-size frame, rounded up to a multiple of
 * lcm(mps, 8). Returns 0 if mps is 0.
 */
size_t rtl815x_rx_xfer_size(size_t requested, uint16_t mps);

/** Size of a bulk-OUT transfer buffer holding one descriptor + one max frame. */
size_t rtl815x_tx_xfer_size(uint16_t mps);

/* ---------------------------------------------------------------------------
 * TX descriptor
 * ------------------------------------------------------------------------- */

/** Write the 8-byte TX descriptor for a frame of @p frame_len bytes (no FCS). */
void rtl815x_tx_desc_build(uint8_t desc[RTL815X_TX_DESC_LEN], size_t frame_len);

/**
 * Build a complete bulk-OUT payload (descriptor + frame) from @p n pieces into
 * @p dst. Frames shorter than 60 bytes are zero-padded to 60.
 *
 * @return total bytes written, or 0 if the frame is empty, larger than
 *         RTL815X_ETH_MAX_FRAME, or does not fit in @p cap.
 */
size_t rtl815x_tx_pack(uint8_t *dst, size_t cap, const usb_eth_iov_t *iov, size_t n);

/* ---------------------------------------------------------------------------
 * RX stream parser
 * ------------------------------------------------------------------------- */

/**
 * Called once per received frame. @p frame points at the Ethernet header and
 * @p len excludes the FCS. The pointer is only valid during the call.
 * @p desc is the raw 24-byte RX descriptor (checksum / VLAN bits).
 */
typedef void (*rtl815x_rx_frame_cb_t)(void *ctx, const uint8_t *frame, size_t len, const uint8_t *desc);

typedef struct {
    uint32_t frames;        /* frames delivered */
    uint32_t bytes;         /* bytes delivered (without FCS) */
    uint32_t runts;         /* frames shorter than header + FCS, dropped */
    uint32_t desync;        /* impossible descriptor length, rest of burst dropped */
    uint32_t truncated;     /* burst ended in the middle of a frame or descriptor */
    uint32_t reassembled;   /* frames that spanned two or more transfers */
    uint32_t discarded;     /* bursts discarded after a gap in the stream */
} rtl815x_rx_stats_t;

typedef struct {
    rtl815x_rx_frame_cb_t cb;
    void *ctx;
    uint8_t *carry;         /* caller buffer for frames split across transfers */
    size_t carry_size;      /* >= RTL815X_RX_MAX_FRAME */
    uint8_t desc[RTL815X_RX_DESC_LEN];
    size_t desc_have;       /* bytes of a split descriptor collected so far */
    size_t frame_len;       /* length from descriptor (with FCS); 0 = between frames */
    size_t frame_have;      /* bytes of a split frame collected in carry */
    size_t pad_left;        /* alignment bytes still to skip */
    bool resync;            /* discard everything until the end of this burst */
    bool restart_check;     /* resync after a restart: first transfer may prove a clean start */
    uint32_t pos;           /* stream position modulo RTL815X_RX_ALIGN */
    rtl815x_rx_stats_t stats;
} rtl815x_rx_parser_t;

/** @return false if the carry buffer is too small or arguments are invalid. */
bool rtl815x_rx_parser_init(rtl815x_rx_parser_t *p, uint8_t *carry, size_t carry_size,
                            rtl815x_rx_frame_cb_t cb, void *ctx);

/** Drop any partial frame and start from a clean, aligned state. Keeps stats. */
void rtl815x_rx_parser_reset(rtl815x_rx_parser_t *p);

/**
 * A completed bulk-IN transfer was not fed (dropped while RX was off), or the
 * stream is being restarted after the IN pipe was halted mid-aggregate. Its
 * bytes are missing from the stream, so the parser must not splice the next
 * data onto an old partial frame. If the dropped transfer itself ended the
 * burst (@p end_of_burst), the next transfer starts a fresh aggregate and the
 * parser is simply reset. Otherwise everything is discarded until the end of
 * the current burst (the next short transfer).
 */
void rtl815x_rx_parser_discard_burst(rtl815x_rx_parser_t *p, bool end_of_burst);

/**
 * RX restarts after the bulk-IN pipe was halted and its transfers cancelled.
 * IDF reports 0 bytes for cancelled transfers, so whether the halt cut an
 * aggregate is unknown. Like rtl815x_rx_parser_discard_burst(p, false), except
 * that the first transfer after the restart is accepted when it is provably a
 * whole burst on its own: a short transfer that rtl815x_rx_burst_is_complete()
 * accepts. On a lightly loaded link that is the usual case, so the first frame
 * after a link-up is not lost. Anything else is discarded to the burst boundary.
 */
void rtl815x_rx_parser_restart(rtl815x_rx_parser_t *p);

/**
 * True if @p data (a short bulk-IN transfer, i.e. a whole burst) is exactly a
 * chain of RX records: each descriptor length is plausible (header + FCS ..
 * RTL815X_RX_MAX_FRAME), each record starts 8-byte aligned, and the last record
 * ends at @p len (with or without its alignment padding).
 */
bool rtl815x_rx_burst_is_complete(const uint8_t *data, size_t len);

/**
 * Feed the payload of one completed bulk-IN transfer.
 *
 * @param end_of_burst true when the transfer was short (actual < requested):
 *        the device ended its aggregate, so nothing continues in the next
 *        transfer. A full-length transfer may end mid-frame.
 */
void rtl815x_rx_parser_feed(rtl815x_rx_parser_t *p, const uint8_t *data, size_t len, bool end_of_burst);

/* ---------------------------------------------------------------------------
 * Vendor register access encoding
 * ------------------------------------------------------------------------- */

typedef struct {
    uint16_t addr;      /* dword-aligned register address (wValue) */
    uint16_t byte_en;   /* byte-enable mask OR'ed into wIndex */
    uint32_t data;      /* value shifted into its byte lane, sent little-endian */
} rtl815x_reg_write_t;

/** Encode a 1-, 2- or 4-byte register write. @p width must be 1, 2 or 4. */
rtl815x_reg_write_t rtl815x_reg_encode_write(uint16_t reg, unsigned width, uint32_t val);

/** Register reads always fetch the containing dword. */
static inline uint16_t rtl815x_reg_read_addr(uint16_t reg)
{
    return (uint16_t)(reg & ~3u);
}

/** Extract a 1-, 2- or 4-byte value from the 4 bytes read at rtl815x_reg_read_addr(reg). */
uint32_t rtl815x_reg_decode_read(uint16_t reg, unsigned width, const uint8_t raw[4]);

/**
 * OCP (PHY-side) registers are reached through a 4 KiB window: write the
 * upper bits to PLA_OCP_GPHY_BASE, then access 0xb000 | (addr & 0xfff) in PLA.
 */
static inline uint16_t rtl815x_ocp_base(uint16_t ocp_addr)
{
    return (uint16_t)(ocp_addr & 0xf000);
}

static inline uint16_t rtl815x_ocp_window_reg(uint16_t ocp_addr)
{
    return (uint16_t)((ocp_addr & 0x0fff) | 0xb000);
}

/** OCP address of standard MII register @p mii_reg of the internal PHY. */
static inline uint16_t rtl815x_ocp_mii_addr(uint32_t mii_reg)
{
    return (uint16_t)(0xa400 + mii_reg * 2);
}

/* ---------------------------------------------------------------------------
 * RX filter
 * ------------------------------------------------------------------------- */

/** Big-endian Ethernet CRC32 of a MAC address, top 6 bits: hash bit 0..63. */
unsigned rtl815x_mcast_hash_bit(const uint8_t mac[USB_ETH_ADDR_LEN]);

/**
 * Convert a 64-bit hash table (bit n set = accept multicast hash n, stored as
 * hashes[0] = bits 0..31, hashes[1] = bits 32..63) to the values written to
 * PLA_MAR0 and PLA_MAR4.
 */
void rtl815x_mcast_mar_values(const uint32_t hashes[2], uint32_t *mar0, uint32_t *mar4);

/** RCR bits for the given filter mode (unicast match and broadcast always on). */
uint32_t rtl815x_rcr_bits(bool promiscuous, bool multicast);

/* ---------------------------------------------------------------------------
 * Station address
 * ------------------------------------------------------------------------- */

typedef enum {
    RTL815X_MAC_SRC_NONE,       /* neither register holds a valid address */
    RTL815X_MAC_SRC_BACKUP,     /* PLA_BACKUP: copy loaded from efuse/EEPROM at power-up */
    RTL815X_MAC_SRC_IDR,        /* PLA_IDR: the live unicast filter address */
} rtl815x_mac_src_t;

/**
 * Pick the dongle's own address. PLA_IDR is the address the MAC filters on and
 * keeps whatever a host last wrote to it until the dongle loses power, so after
 * a warm restart of the ESP it can hold the address a previous run programmed.
 * As in Linux r8152 (determine_ethernet_addr), chips after 0x4c00 read the
 * factory copy in PLA_BACKUP; PLA_IDR is the fallback, and the only source on
 * 0x4c00. @p backup may be NULL (not read). The result is copied to @p out.
 */
rtl815x_mac_src_t rtl815x_pick_mac(uint16_t chip_ver, const uint8_t idr[USB_ETH_ADDR_LEN],
                                   const uint8_t *backup, uint8_t out[USB_ETH_ADDR_LEN]);

/* ---------------------------------------------------------------------------
 * Link status
 * ------------------------------------------------------------------------- */

typedef struct {
    bool link;
    bool full_duplex;
    unsigned speed_mbps;    /* 10, 100 or 1000, 0 when unknown */
} rtl815x_link_t;

rtl815x_link_t rtl815x_decode_phystatus(uint16_t phystatus);

/** RX FIFO thresholds for PLA_RXFIFO_CTRL1/2, which depend on the USB speed. */
void rtl815x_rx_fifo_thresholds(bool full_speed, uint32_t *ctrl1, uint32_t *ctrl2);

#ifdef __cplusplus
}
#endif
