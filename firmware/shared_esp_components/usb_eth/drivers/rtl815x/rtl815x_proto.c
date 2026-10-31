/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * RTL815x (RTL8152/RTL8153) wire-format and sizing logic. No ESP-IDF dependencies.
 */
#include <string.h>
#include "rtl815x_proto.h"
#include "rtl815x_regs.h"

static size_t min_size(size_t a, size_t b)
{
    return a < b ? a : b;
}

static size_t gcd_size(size_t a, size_t b)
{
    while (b != 0) {
        size_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

/* ---------------------------------------------------------------------------
 * Chip variants
 * ------------------------------------------------------------------------- */

static const rtl815x_chip_t s_chips[] = {
    { RTL815X_VER_4C00, "RTL8152", RTL815X_FAMILY_8152 },
    { RTL815X_VER_4C10, "RTL8152", RTL815X_FAMILY_8152 },
    { RTL815X_VER_6000, "RTL8153B", RTL815X_FAMILY_8153B },
    { RTL815X_VER_6010, "RTL8153B", RTL815X_FAMILY_8153B },
};

const rtl815x_chip_t *rtl815x_chip_lookup(uint16_t ver)
{
    for (size_t i = 0; i < sizeof(s_chips) / sizeof(s_chips[0]); i++) {
        if (s_chips[i].ver == ver) {
            return &s_chips[i];
        }
    }
    return NULL;
}

uint16_t rtl815x_rx_early_size(size_t rx_size)
{
    const size_t reserve = RTL815X_RX_MAX_FRAME + RTL815X_RX_DESC_LEN + RTL815X_RX_ALIGN;  /* 1554 */
    if (rx_size < reserve + RTL815X_RX_ALIGN) {
        return 0;
    }
    size_t units = (rx_size - reserve) / RTL815X_RX_ALIGN;
    return (uint16_t)(units > 0xffff ? 0xffff : units);
}

/* ---------------------------------------------------------------------------
 * Buffer sizing
 * ------------------------------------------------------------------------- */

size_t rtl815x_rx_xfer_size(size_t requested, uint16_t mps)
{
    if (mps == 0) {
        return 0;
    }
    size_t unit = (size_t)mps / gcd_size(mps, RTL815X_RX_ALIGN) * RTL815X_RX_ALIGN;
    size_t min = RTL815X_RX_DESC_LEN + RTL815X_RX_MAX_FRAME;
    return usb_eth_round_up(requested > min ? requested : min, unit);
}

size_t rtl815x_tx_xfer_size(uint16_t mps)
{
    if (mps == 0) {
        return 0;
    }
    return usb_eth_round_up(RTL815X_TX_DESC_LEN + RTL815X_ETH_MAX_FRAME, mps);
}

/* ---------------------------------------------------------------------------
 * TX
 * ------------------------------------------------------------------------- */

void rtl815x_tx_desc_build(uint8_t desc[RTL815X_TX_DESC_LEN], size_t frame_len)
{
    usb_eth_put_le32(desc, ((uint32_t)frame_len & RTL815X_TX_LEN_MASK) | RTL815X_TX_FS | RTL815X_TX_LS);
    usb_eth_put_le32(desc + 4, 0);  /* no checksum or VLAN offload */
}

size_t rtl815x_tx_pack(uint8_t *dst, size_t cap, const usb_eth_iov_t *iov, size_t n)
{
    size_t len = 0;
    for (size_t i = 0; i < n; i++) {
        len += iov[i].len;
    }
    if (len == 0 || len > RTL815X_ETH_MAX_FRAME) {
        return 0;
    }
    size_t wire_len = len < RTL815X_ETH_MIN_FRAME ? RTL815X_ETH_MIN_FRAME : len;
    if (dst == NULL || cap < RTL815X_TX_DESC_LEN + wire_len) {
        return 0;
    }
    rtl815x_tx_desc_build(dst, wire_len);
    uint8_t *w = dst + RTL815X_TX_DESC_LEN;
    for (size_t i = 0; i < n; i++) {
        if (iov[i].len) {
            memcpy(w, iov[i].data, iov[i].len);
            w += iov[i].len;
        }
    }
    if (wire_len > len) {
        memset(w, 0, wire_len - len);
    }
    return RTL815X_TX_DESC_LEN + wire_len;
}

/* ---------------------------------------------------------------------------
 * RX stream parser
 * ------------------------------------------------------------------------- */

bool rtl815x_rx_parser_init(rtl815x_rx_parser_t *p, uint8_t *carry, size_t carry_size,
                            rtl815x_rx_frame_cb_t cb, void *ctx)
{
    if (p == NULL || carry == NULL || cb == NULL || carry_size < RTL815X_RX_MAX_FRAME) {
        return false;
    }
    memset(p, 0, sizeof(*p));
    p->carry = carry;
    p->carry_size = carry_size;
    p->cb = cb;
    p->ctx = ctx;
    return true;
}

void rtl815x_rx_parser_reset(rtl815x_rx_parser_t *p)
{
    p->desc_have = 0;
    p->frame_len = 0;
    p->frame_have = 0;
    p->pad_left = 0;
    p->resync = false;
    p->restart_check = false;
    p->pos = 0;
}

void rtl815x_rx_parser_discard_burst(rtl815x_rx_parser_t *p, bool end_of_burst)
{
    rtl815x_rx_parser_reset(p);
    if (!end_of_burst) {
        p->resync = true;   /* cleared by the next end_of_burst in rtl815x_rx_parser_feed */
        p->stats.discarded++;
    }
}

void rtl815x_rx_parser_restart(rtl815x_rx_parser_t *p)
{
    rtl815x_rx_parser_reset(p);
    p->resync = true;
    p->restart_check = true;
}

bool rtl815x_rx_burst_is_complete(const uint8_t *data, size_t len)
{
    size_t off = 0;
    if (data == NULL || len < RTL815X_RX_DESC_LEN) {
        return false;
    }
    while (off < len) {
        if (len - off < RTL815X_RX_DESC_LEN) {
            return false;
        }
        size_t flen = usb_eth_get_le32(data + off) & RTL815X_RX_LEN_MASK;
        if (flen < RTL815X_ETH_HDR_LEN + RTL815X_ETH_FCS_LEN || flen > RTL815X_RX_MAX_FRAME) {
            return false;
        }
        size_t end = off + RTL815X_RX_DESC_LEN + flen;
        if (end > len) {
            return false;
        }
        size_t next = usb_eth_round_up(end, RTL815X_RX_ALIGN);
        if (next >= len) {
            return true;    /* last record, padded or not */
        }
        off = next;
    }
    return false;
}

static void consume(rtl815x_rx_parser_t *p, size_t *off, size_t n)
{
    *off += n;
    p->pos = (uint32_t)((p->pos + n) & (RTL815X_RX_ALIGN - 1));
}

static void deliver(rtl815x_rx_parser_t *p, const uint8_t *frame, size_t len_with_fcs)
{
    if (len_with_fcs < RTL815X_ETH_HDR_LEN + RTL815X_ETH_FCS_LEN) {
        p->stats.runts++;
        return;
    }
    size_t len = len_with_fcs - RTL815X_ETH_FCS_LEN;
    p->stats.frames++;
    p->stats.bytes += (uint32_t)len;
    p->cb(p->ctx, frame, len, p->desc);
}

static void end_frame(rtl815x_rx_parser_t *p)
{
    p->frame_len = 0;
    p->frame_have = 0;
    p->pad_left = (RTL815X_RX_ALIGN - p->pos) & (RTL815X_RX_ALIGN - 1);
}

void rtl815x_rx_parser_feed(rtl815x_rx_parser_t *p, const uint8_t *data, size_t len, bool end_of_burst)
{
    size_t off = 0;

    if (p->restart_check) {
        /* First transfer after a restart: parse it only if it is provably a
         * whole burst starting at a record boundary. */
        p->restart_check = false;
        if (end_of_burst && rtl815x_rx_burst_is_complete(data, len)) {
            p->resync = false;
        } else {
            p->stats.discarded++;
        }
    }

    while (off < len && !p->resync) {
        size_t avail = len - off;

        if (p->pad_left) {
            size_t n = min_size(p->pad_left, avail);
            p->pad_left -= n;
            consume(p, &off, n);
            continue;
        }

        if (p->frame_len == 0) {
            /* Collect a (possibly split) descriptor. */
            size_t n = min_size(RTL815X_RX_DESC_LEN - p->desc_have, avail);
            memcpy(p->desc + p->desc_have, data + off, n);
            p->desc_have += n;
            consume(p, &off, n);
            if (p->desc_have < RTL815X_RX_DESC_LEN) {
                break;  /* rest of the descriptor is in the next transfer */
            }
            p->desc_have = 0;

            size_t flen = usb_eth_get_le32(p->desc) & RTL815X_RX_LEN_MASK;
            if (flen > RTL815X_RX_MAX_FRAME || flen > p->carry_size) {
                /* We cannot know where the next frame starts; trusting the
                 * rest of the burst would allow packet-in-packet injection. */
                p->stats.desync++;
                p->resync = true;
                break;
            }
            if (flen == 0) {
                p->stats.runts++;
                end_frame(p);
                continue;
            }
            p->frame_len = flen;
            p->frame_have = 0;
            continue;
        }

        size_t remaining = p->frame_len - p->frame_have;
        if (p->frame_have == 0 && avail >= remaining) {
            /* Fast path: whole frame in this transfer, no copy. */
            deliver(p, data + off, p->frame_len);
            consume(p, &off, remaining);
            end_frame(p);
            continue;
        }

        size_t n = min_size(remaining, avail);
        memcpy(p->carry + p->frame_have, data + off, n);
        p->frame_have += n;
        consume(p, &off, n);
        if (p->frame_have == p->frame_len) {
            p->stats.reassembled++;
            deliver(p, p->carry, p->frame_len);
            end_frame(p);
        }
    }

    if (end_of_burst) {
        if (p->frame_len != 0 || p->desc_have != 0) {
            p->stats.truncated++;
        }
        rtl815x_rx_parser_reset(p);
    }
}

/* ---------------------------------------------------------------------------
 * Register access encoding
 * ------------------------------------------------------------------------- */

rtl815x_reg_write_t rtl815x_reg_encode_write(uint16_t reg, unsigned width, uint32_t val)
{
    rtl815x_reg_write_t w;
    unsigned shift;

    switch (width) {
    case 1:
        shift = reg & 3u;
        w.byte_en = (uint16_t)(RTL815X_BYTE_EN_BYTE << shift);
        w.data = (val & 0xffu) << (shift * 8);
        break;
    case 2:
        shift = reg & 2u;
        w.byte_en = (uint16_t)(RTL815X_BYTE_EN_WORD << shift);
        w.data = (val & 0xffffu) << (shift * 8);
        break;
    default:
        w.byte_en = RTL815X_BYTE_EN_DWORD;
        w.data = val;
        break;
    }
    w.addr = (uint16_t)(reg & ~3u);
    return w;
}

uint32_t rtl815x_reg_decode_read(uint16_t reg, unsigned width, const uint8_t raw[4])
{
    uint32_t v = usb_eth_get_le32(raw);
    switch (width) {
    case 1:
        return (v >> ((reg & 3u) * 8)) & 0xffu;
    case 2:
        return (v >> ((reg & 2u) * 8)) & 0xffffu;
    default:
        return v;
    }
}

/* ---------------------------------------------------------------------------
 * RX filter
 * ------------------------------------------------------------------------- */

unsigned rtl815x_mcast_hash_bit(const uint8_t mac[USB_ETH_ADDR_LEN])
{
    uint32_t crc = 0xffffffffu;
    for (int i = 0; i < USB_ETH_ADDR_LEN; i++) {
        uint8_t b = mac[i];
        for (int bit = 0; bit < 8; bit++) {
            uint32_t carry = ((crc & 0x80000000u) ? 1u : 0u) ^ (b & 1u);
            crc <<= 1;
            b >>= 1;
            if (carry) {
                crc = (crc ^ 0x04c11db6u) | carry;
            }
        }
    }
    return crc >> 26;
}

static uint32_t bswap32(uint32_t v)
{
    return (v >> 24) | ((v >> 8) & 0xff00u) | ((v << 8) & 0xff0000u) | (v << 24);
}

void rtl815x_mcast_mar_values(const uint32_t hashes[2], uint32_t *mar0, uint32_t *mar4)
{
    *mar0 = bswap32(hashes[1]);
    *mar4 = bswap32(hashes[0]);
}

rtl815x_mac_src_t rtl815x_pick_mac(uint16_t chip_ver, const uint8_t idr[USB_ETH_ADDR_LEN],
                                   const uint8_t *backup, uint8_t out[USB_ETH_ADDR_LEN])
{
    if (chip_ver != RTL815X_VER_4C00 && backup && usb_eth_mac_is_valid(backup)) {
        memcpy(out, backup, USB_ETH_ADDR_LEN);
        return RTL815X_MAC_SRC_BACKUP;
    }
    if (usb_eth_mac_is_valid(idr)) {
        memcpy(out, idr, USB_ETH_ADDR_LEN);
        return RTL815X_MAC_SRC_IDR;
    }
    return RTL815X_MAC_SRC_NONE;
}

uint32_t rtl815x_rcr_bits(bool promiscuous, bool multicast)
{
    uint32_t rcr = RTL815X_RCR_APM | RTL815X_RCR_AB;
    if (multicast || promiscuous) {
        rcr |= RTL815X_RCR_AM;
    }
    if (promiscuous) {
        rcr |= RTL815X_RCR_AAP;
    }
    return rcr;
}

/* ---------------------------------------------------------------------------
 * Link status
 * ------------------------------------------------------------------------- */

rtl815x_link_t rtl815x_decode_phystatus(uint16_t phystatus)
{
    rtl815x_link_t l = {0};
    l.link = (phystatus & RTL815X_PHYSTATUS_LINK) != 0;
    l.full_duplex = (phystatus & RTL815X_PHYSTATUS_FDX) != 0;
    if (phystatus & RTL815X_PHYSTATUS_1000MBPS) {
        l.speed_mbps = 1000;
    } else if (phystatus & RTL815X_PHYSTATUS_100MBPS) {
        l.speed_mbps = 100;
    } else if (phystatus & RTL815X_PHYSTATUS_10MBPS) {
        l.speed_mbps = 10;
    }
    return l;
}

void rtl815x_rx_fifo_thresholds(bool full_speed, uint32_t *ctrl1, uint32_t *ctrl2)
{
    if (full_speed) {
        *ctrl1 = RTL815X_RXFIFO_THR2_FULL;
        *ctrl2 = RTL815X_RXFIFO_THR3_FULL;
    } else {
        *ctrl1 = RTL815X_RXFIFO_THR2_HIGH;
        *ctrl2 = RTL815X_RXFIFO_THR3_HIGH;
    }
}

