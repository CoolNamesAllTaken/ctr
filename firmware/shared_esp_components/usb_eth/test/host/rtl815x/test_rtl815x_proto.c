/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Host-side unit tests for rtl815x_proto.c. Build and run with `make` in
 * test/host (plain gcc, no ESP-IDF needed).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rtl815x_proto.h"
#include "rtl815x_regs.h"

#include "test_harness.h"

/* -------------------------------------------------------- stream builder -- */

/* Collected frames from the parser callback. */
#define MAX_FRAMES 512
typedef struct {
    size_t n;
    size_t len[MAX_FRAMES];
    uint8_t *data[MAX_FRAMES];
} sink_t;

static void sink_cb(void *ctx, const uint8_t *frame, size_t len, const uint8_t *desc)
{
    sink_t *s = ctx;
    (void)desc;
    if (s->n >= MAX_FRAMES) {
        return;
    }
    s->data[s->n] = malloc(len);
    memcpy(s->data[s->n], frame, len);
    s->len[s->n] = len;
    s->n++;
}

static void sink_free(sink_t *s)
{
    for (size_t i = 0; i < s->n; i++) {
        free(s->data[i]);
    }
    s->n = 0;
}

typedef struct {
    uint8_t *buf;
    size_t len;
    size_t cap;
} stream_t;

static void stream_put(stream_t *s, const void *p, size_t n)
{
    if (s->len + n > s->cap) {
        s->cap = (s->len + n) * 2;
        s->buf = realloc(s->buf, s->cap);
    }
    memcpy(s->buf + s->len, p, n);
    s->len += n;
}

/* Append one RX record the way the chip does: descriptor, frame, FCS, pad to 8. */
static void stream_add_frame(stream_t *s, const uint8_t *frame, size_t len, int pad_last)
{
    uint8_t desc[RTL815X_RX_DESC_LEN] = {0};
    uint8_t fcs[4] = {0xde, 0xad, 0xbe, 0xef};
    usb_eth_put_le32(desc, (uint32_t)(len + 4));
    usb_eth_put_le32(desc + 4, 0x00080000u);   /* arbitrary csum bits */
    stream_put(s, desc, sizeof(desc));
    stream_put(s, frame, len);
    stream_put(s, fcs, 4);
    if (pad_last) {
        static const uint8_t zeros[8];
        size_t pad = (8 - (s->len & 7)) & 7;
        stream_put(s, zeros, pad);
    }
}

static void fill_frame(uint8_t *f, size_t len, unsigned seed)
{
    for (size_t i = 0; i < len; i++) {
        f[i] = (uint8_t)(seed * 131u + i * 7u + (i >> 8));
    }
}

/* Feed @p s to the parser as a sequence of bulk transfers of @p xfer bytes. */
static void feed_as_transfers(rtl815x_rx_parser_t *p, const stream_t *s, size_t xfer)
{
    size_t off = 0;
    while (off < s->len) {
        size_t n = s->len - off < xfer ? s->len - off : xfer;
        rtl815x_rx_parser_feed(p, s->buf + off, n, n < xfer);
        off += n;
    }
    if (s->len % xfer == 0) {
        /* Device would end the burst with a zero-length packet. */
        rtl815x_rx_parser_feed(p, NULL, 0, true);
    }
}

/* ------------------------------------------------------------------ tests -- */

static void test_rx_xfer_size(void)
{
    const size_t min = RTL815X_RX_DESC_LEN + RTL815X_RX_MAX_FRAME;   /* 1546 */
    CHECK_EQ(min, 1546);
    /* Full speed (ESP32-S3): 64-byte packets */
    CHECK_EQ(rtl815x_rx_xfer_size(0, 64), 1600);
    CHECK_EQ(rtl815x_rx_xfer_size(16384, 64), 16384);
    CHECK_EQ(rtl815x_rx_xfer_size(4000, 64), 4032);
    /* High speed (ESP32-P4): 512-byte packets */
    CHECK_EQ(rtl815x_rx_xfer_size(0, 512), 2048);
    CHECK_EQ(rtl815x_rx_xfer_size(16384, 512), 16384);
    CHECK_EQ(rtl815x_rx_xfer_size(5000, 512), 5120);
    /* Odd sizes still give a multiple of both mps and 8 */
    size_t v = rtl815x_rx_xfer_size(0, 60);
    CHECK_EQ(v % 60, 0);
    CHECK_EQ(v % 8, 0);
    CHECK(v >= min);
    CHECK_EQ(rtl815x_rx_xfer_size(0, 8), 1552);
    CHECK_EQ(rtl815x_rx_xfer_size(1000, 0), 0);
}

static void test_tx_xfer_size_and_zlp(void)
{
    CHECK_EQ(rtl815x_tx_xfer_size(64), 1536);
    CHECK_EQ(rtl815x_tx_xfer_size(512), 1536);
    CHECK_EQ(rtl815x_tx_xfer_size(8), 1528);
    CHECK_EQ(rtl815x_tx_xfer_size(0), 0);
    CHECK(usb_eth_tx_needs_zlp(64, 64));
    CHECK(usb_eth_tx_needs_zlp(1024, 512));
    CHECK(!usb_eth_tx_needs_zlp(1024 + 8, 512));
    CHECK(usb_eth_tx_needs_zlp(1024 + 64, 64));
    CHECK(!usb_eth_tx_needs_zlp(0, 64));
    CHECK(!usb_eth_tx_needs_zlp(100, 0));
}

static void test_tx_desc(void)
{
    uint8_t d[8];
    rtl815x_tx_desc_build(d, 60);
    const uint8_t exp[8] = {0x3c, 0x00, 0x00, 0xc0, 0, 0, 0, 0};
    CHECK(memcmp(d, exp, 8) == 0);
    rtl815x_tx_desc_build(d, 1518);
    CHECK_EQ(usb_eth_get_le32(d), 1518u | RTL815X_TX_FS | RTL815X_TX_LS);
    CHECK_EQ(usb_eth_get_le32(d + 4), 0);
}

static void test_tx_pack(void)
{
    uint8_t frame[1600];
    uint8_t out[1600];
    fill_frame(frame, sizeof(frame), 3);

    /* Two pieces, normal size */
    usb_eth_iov_t iov[2] = {{frame, 14}, {frame + 14, 86}};
    size_t n = rtl815x_tx_pack(out, sizeof(out), iov, 2);
    CHECK_EQ(n, 8 + 100);
    CHECK_EQ(usb_eth_get_le32(out) & RTL815X_TX_LEN_MASK, 100);
    CHECK(memcmp(out + 8, frame, 100) == 0);

    /* Short frame (ARP, 42 bytes) is zero padded to 60 */
    memset(out, 0xaa, sizeof(out));
    usb_eth_iov_t arp = {frame, 42};
    n = rtl815x_tx_pack(out, sizeof(out), &arp, 1);
    CHECK_EQ(n, 8 + 60);
    CHECK_EQ(usb_eth_get_le32(out) & RTL815X_TX_LEN_MASK, 60);
    CHECK(memcmp(out + 8, frame, 42) == 0);
    int zero = 1;
    for (int i = 8 + 42; i < 8 + 60; i++) {
        zero &= out[i] == 0;
    }
    CHECK(zero);
    CHECK_EQ(out[8 + 60], 0xaa);   /* nothing written past the frame */

    /* Max frame with VLAN tag fits, one more byte does not */
    usb_eth_iov_t big = {frame, RTL815X_ETH_MAX_FRAME};
    CHECK_EQ(rtl815x_tx_pack(out, sizeof(out), &big, 1), 8 + 1518);
    big.len++;
    CHECK_EQ(rtl815x_tx_pack(out, sizeof(out), &big, 1), 0);

    /* Too small destination, empty frame */
    usb_eth_iov_t f100 = {frame, 100};
    CHECK_EQ(rtl815x_tx_pack(out, 107, &f100, 1), 0);
    CHECK_EQ(rtl815x_tx_pack(out, 108, &f100, 1), 108);
    usb_eth_iov_t empty = {frame, 0};
    CHECK_EQ(rtl815x_tx_pack(out, sizeof(out), &empty, 1), 0);
    /* The buffer size computed for any MPS holds a max frame */
    big.len = RTL815X_ETH_MAX_FRAME;
    CHECK(rtl815x_tx_pack(out, rtl815x_tx_xfer_size(64), &big, 1) != 0);
    CHECK(rtl815x_tx_pack(out, rtl815x_tx_xfer_size(512), &big, 1) != 0);
}

static void test_rx_single_and_aggregated(void)
{
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    CHECK(rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink));

    uint8_t f[3][1514];
    size_t lens[3] = {60, 61, 1514};   /* 61 + 4 + 24 forces padding */
    stream_t s = {0};
    for (int i = 0; i < 3; i++) {
        fill_frame(f[i], lens[i], (unsigned)i);
        stream_add_frame(&s, f[i], lens[i], 1);
    }
    /* One short transfer containing all three frames */
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(sink.n, 3);
    for (size_t i = 0; i < sink.n && i < 3; i++) {
        CHECK_EQ(sink.len[i], lens[i]);         /* FCS stripped */
        CHECK(memcmp(sink.data[i], f[i], lens[i]) == 0);
    }
    CHECK_EQ(p.stats.frames, 3);
    CHECK_EQ(p.stats.reassembled, 0);
    CHECK_EQ(p.stats.truncated, 0);
    sink_free(&sink);

    /* Same stream without padding after the last frame */
    s.len = 0;
    stream_add_frame(&s, f[0], 61, 0);
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(sink.n, 1);
    CHECK_EQ(p.stats.truncated, 0);
    sink_free(&sink);
    free(s.buf);
}

static void test_rx_split_across_transfers(void)
{
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    static uint8_t frames[40][1514];
    size_t lens[40];
    const size_t xfers[] = {64, 128, 192, 512, 1024, 1600, 2048, 4096, 16384};
    unsigned seed = 12345;

    stream_t s = {0};
    for (int i = 0; i < 40; i++) {
        seed = seed * 1103515245u + 12345u;
        lens[i] = 60 + (seed >> 8) % (1514 - 60 + 1);
        if (i == 0) {
            lens[i] = 60;
        } else if (i == 1) {
            lens[i] = 1514;
        }
        fill_frame(frames[i], lens[i], (unsigned)i + 100);
        stream_add_frame(&s, frames[i], lens[i], 1);
    }

    for (size_t x = 0; x < sizeof(xfers) / sizeof(xfers[0]); x++) {
        rtl815x_rx_parser_t p;
        sink_t sink = {0};
        rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);
        feed_as_transfers(&p, &s, xfers[x]);
        int ok = sink.n == 40;
        for (size_t i = 0; ok && i < 40; i++) {
            ok = sink.len[i] == lens[i] && memcmp(sink.data[i], frames[i], lens[i]) == 0;
        }
        if (!ok) {
            printf("  transfer size %zu: got %zu frames\n", xfers[x], sink.n);
        }
        CHECK(ok);
        CHECK_EQ(p.stats.truncated, 0);
        CHECK_EQ(p.stats.desync, 0);
        if (xfers[x] < 1600) {
            CHECK(p.stats.reassembled > 0);
        }
        sink_free(&sink);
    }
    free(s.buf);
}

static void test_rx_descriptor_split(void)
{
    /* Put a descriptor exactly across a 64-byte boundary: the first record
     * is 24 + 36 + 4 = 64 bytes, then a second record starts at 64 and
     * we cut the transfer at 72, in the middle of the second descriptor. */
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    uint8_t f1[36], f2[200];
    fill_frame(f1, sizeof(f1), 1);
    fill_frame(f2, sizeof(f2), 2);
    stream_t s = {0};
    stream_add_frame(&s, f1, sizeof(f1), 1);
    CHECK_EQ(s.len, 64);
    stream_add_frame(&s, f2, sizeof(f2), 1);

    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);
    rtl815x_rx_parser_feed(&p, s.buf, 72, false);
    CHECK_EQ(sink.n, 1);
    CHECK_EQ(p.desc_have, 8);
    rtl815x_rx_parser_feed(&p, s.buf + 72, s.len - 72, true);
    CHECK_EQ(sink.n, 2);
    CHECK(sink.n == 2 && sink.len[1] == sizeof(f2) && memcmp(sink.data[1], f2, sizeof(f2)) == 0);
    sink_free(&sink);
    free(s.buf);
}

static void test_rx_truncated_burst(void)
{
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    uint8_t f[300];
    fill_frame(f, sizeof(f), 9);
    stream_t s = {0};
    stream_add_frame(&s, f, sizeof(f), 1);

    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);
    /* Burst ends 10 bytes early: frame must be dropped, not delivered */
    rtl815x_rx_parser_feed(&p, s.buf, s.len - 20, true);
    CHECK_EQ(sink.n, 0);
    CHECK_EQ(p.stats.truncated, 1);
    /* Next burst is parsed from a clean state */
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);
    free(s.buf);
}

static void test_rx_desync_and_runt(void)
{
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);

    uint8_t f[100];
    fill_frame(f, sizeof(f), 4);

    /* Impossible length: everything after it in the burst is dropped */
    stream_t s = {0};
    uint8_t bad[RTL815X_RX_DESC_LEN] = {0};
    usb_eth_put_le32(bad, 0x7fff);
    stream_put(&s, bad, sizeof(bad));
    stream_add_frame(&s, f, sizeof(f), 1);
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(sink.n, 0);
    CHECK_EQ(p.stats.desync, 1);
    /* ... including later transfers of the same burst */
    rtl815x_rx_parser_reset(&p);
    p.resync = true;
    rtl815x_rx_parser_feed(&p, s.buf + 24, s.len - 24, false);
    CHECK_EQ(sink.n, 0);
    rtl815x_rx_parser_feed(&p, NULL, 0, true);
    CHECK(!p.resync);

    /* Length just above the maximum is rejected too */
    s.len = 0;
    usb_eth_put_le32(bad, RTL815X_RX_MAX_FRAME + 1);
    stream_put(&s, bad, sizeof(bad));
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(p.stats.desync, 2);

    /* Runt (len 10 < 18) is skipped and the following frame still arrives */
    s.len = 0;
    uint8_t runt[10] = {0};
    uint8_t d[RTL815X_RX_DESC_LEN] = {0};
    usb_eth_put_le32(d, sizeof(runt));
    stream_put(&s, d, sizeof(d));
    stream_put(&s, runt, sizeof(runt));
    static const uint8_t zeros[8];
    stream_put(&s, zeros, (8 - (s.len & 7)) & 7);
    stream_add_frame(&s, f, sizeof(f), 1);
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(p.stats.runts, 1);
    CHECK_EQ(sink.n, 1);
    CHECK(sink.n == 1 && sink.len[0] == sizeof(f));

    /* Zero-length descriptor counts as a runt and parsing continues */
    sink_free(&sink);
    s.len = 0;
    memset(d, 0, sizeof(d));
    stream_put(&s, d, sizeof(d));
    stream_add_frame(&s, f, sizeof(f), 1);
    rtl815x_rx_parser_feed(&p, s.buf, s.len, true);
    CHECK_EQ(p.stats.runts, 2);
    CHECK_EQ(sink.n, 1);

    sink_free(&sink);
    free(s.buf);
}

static void test_rx_parser_init_args(void)
{
    uint8_t small[100];
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    CHECK(!rtl815x_rx_parser_init(&p, small, sizeof(small), sink_cb, &sink));
    CHECK(!rtl815x_rx_parser_init(&p, NULL, sizeof(carry), sink_cb, &sink));
    CHECK(!rtl815x_rx_parser_init(&p, carry, sizeof(carry), NULL, &sink));
    CHECK(rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, NULL));
}

static void test_reg_encoding(void)
{
    rtl815x_reg_write_t w;

    w = rtl815x_reg_encode_write(RTL815X_PLA_CR, 1, 0x10);          /* 0xe813 */
    CHECK_EQ(w.addr, 0xe810);
    CHECK_EQ(w.byte_en, 0x88);
    CHECK_EQ(w.data, 0x10000000u);

    w = rtl815x_reg_encode_write(0xe811, 1, 0x1ff);                 /* value masked */
    CHECK_EQ(w.addr, 0xe810);
    CHECK_EQ(w.byte_en, 0x22);
    CHECK_EQ(w.data, 0x0000ff00u);

    w = rtl815x_reg_encode_write(RTL815X_PLA_OCP_GPHY_BASE, 2, 0xa000);  /* 0xe86c */
    CHECK_EQ(w.addr, 0xe86c);
    CHECK_EQ(w.byte_en, 0x33);
    CHECK_EQ(w.data, 0xa000u);

    w = rtl815x_reg_encode_write(RTL815X_USB_TX_AGG, 2, 0x1234);    /* 0xd40a */
    CHECK_EQ(w.addr, 0xd408);
    CHECK_EQ(w.byte_en, 0xcc);
    CHECK_EQ(w.data, 0x12340000u);

    w = rtl815x_reg_encode_write(RTL815X_PLA_RCR, 4, 0xdeadbeef);
    CHECK_EQ(w.addr, 0xc010);
    CHECK_EQ(w.byte_en, 0xff);
    CHECK_EQ(w.data, 0xdeadbeefu);

    const uint8_t raw[4] = {0x11, 0x22, 0x33, 0x44};
    CHECK_EQ(rtl815x_reg_read_addr(0xe813), 0xe810);
    CHECK_EQ(rtl815x_reg_decode_read(0xe810, 1, raw), 0x11);
    CHECK_EQ(rtl815x_reg_decode_read(0xe813, 1, raw), 0x44);
    CHECK_EQ(rtl815x_reg_decode_read(0xe812, 2, raw), 0x4433);
    CHECK_EQ(rtl815x_reg_decode_read(0xe810, 2, raw), 0x2211);
    CHECK_EQ(rtl815x_reg_decode_read(0xe810, 4, raw), 0x44332211u);

    /* OCP window */
    CHECK_EQ(rtl815x_ocp_mii_addr(1), 0xa402);       /* BMSR */
    CHECK_EQ(rtl815x_ocp_base(0xa402), 0xa000);
    CHECK_EQ(rtl815x_ocp_window_reg(0xa402), 0xb402);
    CHECK_EQ(rtl815x_ocp_base(RTL815X_OCP_ALDPS_CONFIG), 0x2000);
    CHECK_EQ(rtl815x_ocp_window_reg(RTL815X_OCP_ALDPS_CONFIG), 0xb010);
}

static void test_mcast(void)
{
    /* Expected values computed independently as bitrev32(~zlib.crc32(mac)) >> 26 */
    const uint8_t all_hosts[6] = {0x01, 0x00, 0x5e, 0x00, 0x00, 0x01};
    const uint8_t v6_all[6] = {0x33, 0x33, 0x00, 0x00, 0x00, 0x01};
    const uint8_t mdns[6] = {0x01, 0x00, 0x5e, 0x00, 0x00, 0xfb};
    const uint8_t bcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    CHECK_EQ(rtl815x_mcast_hash_bit(all_hosts), 31);
    CHECK_EQ(rtl815x_mcast_hash_bit(v6_all), 62);
    CHECK_EQ(rtl815x_mcast_hash_bit(mdns), 15);
    CHECK_EQ(rtl815x_mcast_hash_bit(bcast), 63);

    uint32_t h[2] = {0x00000001u, 0x80000000u}, m0, m4;
    rtl815x_mcast_mar_values(h, &m0, &m4);
    CHECK_EQ(m0, 0x00000080u);
    CHECK_EQ(m4, 0x01000000u);
    h[0] = h[1] = 0xffffffffu;
    rtl815x_mcast_mar_values(h, &m0, &m4);
    CHECK_EQ(m0, 0xffffffffu);
    CHECK_EQ(m4, 0xffffffffu);

    CHECK_EQ(rtl815x_rcr_bits(false, false), RTL815X_RCR_APM | RTL815X_RCR_AB);
    CHECK_EQ(rtl815x_rcr_bits(false, true), RTL815X_RCR_APM | RTL815X_RCR_AB | RTL815X_RCR_AM);
    CHECK_EQ(rtl815x_rcr_bits(true, false), RTL815X_RCR_ACPT_ALL);
}

static void test_pick_mac(void)
{
    const uint8_t factory[6] = {0xc8, 0x4d, 0x44, 0x35, 0x8f, 0x72};
    const uint8_t esp[6] = {0xf4, 0x12, 0xfa, 0x50, 0x66, 0xcb};    /* written by a previous run */
    const uint8_t zero[6] = {0};
    const uint8_t mcast[6] = {0x01, 0x00, 0x5e, 0x00, 0x00, 0x01};
    uint8_t out[6];

    CHECK(usb_eth_mac_is_valid(factory));
    CHECK(!usb_eth_mac_is_valid(zero));
    CHECK(!usb_eth_mac_is_valid(mcast));

    /* 0x4c10: the factory copy wins over a stale PLA_IDR */
    memset(out, 0, sizeof(out));
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C10, esp, factory, out), RTL815X_MAC_SRC_BACKUP);
    CHECK(memcmp(out, factory, 6) == 0);
    /* 0x4c10 with an empty or unread PLA_BACKUP: PLA_IDR */
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C10, factory, zero, out), RTL815X_MAC_SRC_IDR);
    CHECK(memcmp(out, factory, 6) == 0);
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C10, factory, NULL, out), RTL815X_MAC_SRC_IDR);
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C10, factory, mcast, out), RTL815X_MAC_SRC_IDR);
    /* 0x4c00: PLA_IDR only, as in Linux and FreeBSD */
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C00, factory, esp, out), RTL815X_MAC_SRC_IDR);
    CHECK(memcmp(out, factory, 6) == 0);
    /* nothing valid: caller falls back to the ESP's address; out untouched */
    memcpy(out, esp, 6);
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C10, zero, zero, out), RTL815X_MAC_SRC_NONE);
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_4C00, mcast, factory, out), RTL815X_MAC_SRC_NONE);
    CHECK(memcmp(out, esp, 6) == 0);
}

static void test_link_and_fifo(void)
{
    rtl815x_link_t l = rtl815x_decode_phystatus(0x000b);
    CHECK(l.link && l.full_duplex);
    CHECK_EQ(l.speed_mbps, 100);
    l = rtl815x_decode_phystatus(0x0006);
    CHECK(l.link && !l.full_duplex);
    CHECK_EQ(l.speed_mbps, 10);
    l = rtl815x_decode_phystatus(0x0000);
    CHECK(!l.link);
    CHECK_EQ(l.speed_mbps, 0);

    uint32_t c1, c2;
    rtl815x_rx_fifo_thresholds(true, &c1, &c2);
    CHECK_EQ(c1, 0x60);
    CHECK_EQ(c2, 0x78);
    rtl815x_rx_fifo_thresholds(false, &c1, &c2);
    CHECK_EQ(c1, 0x38);
    CHECK_EQ(c2, 0x48);
}

static void test_rx_discard_after_gap(void)
{
    /* A burst of three large frames spans several 512-byte transfers. One full
     * transfer in the middle is dropped (RX was briefly off). The parser must
     * not splice the following bytes onto the partial frame: it discards the
     * rest of the burst and parses the next burst normally. */
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    static uint8_t f[3][1000];
    stream_t burst = {0}, next = {0};
    for (int i = 0; i < 3; i++) {
        fill_frame(f[i], sizeof(f[i]), (unsigned)i + 40);
        stream_add_frame(&burst, f[i], sizeof(f[i]), 1);
    }
    uint8_t g[100];
    fill_frame(g, sizeof(g), 77);
    stream_add_frame(&next, g, sizeof(g), 1);
    CHECK(burst.len > 4 * 512);

    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);

    /* Without the discard, splicing produces garbage or a desync: show that
     * the gap is really harmful, so the test below is meaningful. */
    rtl815x_rx_parser_feed(&p, burst.buf, 512, false);
    rtl815x_rx_parser_feed(&p, burst.buf + 1024, 512, false);   /* 512..1023 lost */
    rtl815x_rx_parser_feed(&p, burst.buf + 1536, burst.len - 1536, true);
    int spliced_ok = 1;
    for (size_t i = 0; i < sink.n; i++) {
        int match = 0;
        for (int k = 0; k < 3; k++) {
            match |= sink.len[i] == sizeof(f[k]) && memcmp(sink.data[i], f[k], sizeof(f[k])) == 0;
        }
        spliced_ok &= match;
    }
    CHECK(!spliced_ok || sink.n < 3);   /* something was lost or corrupted */
    sink_free(&sink);

    /* With the discard: frame 0 is still partial when the gap happens. */
    rtl815x_rx_parser_reset(&p);
    uint32_t frames_before = p.stats.frames;
    rtl815x_rx_parser_feed(&p, burst.buf, 512, false);
    CHECK_EQ(sink.n, 0);
    rtl815x_rx_parser_discard_burst(&p, false);                  /* 512..1023 dropped */
    CHECK_EQ(p.stats.discarded, 1);
    rtl815x_rx_parser_feed(&p, burst.buf + 1024, 512, false);
    rtl815x_rx_parser_feed(&p, burst.buf + 1536, burst.len - 1536, true);
    CHECK_EQ(sink.n, 0);                                         /* nothing from the damaged burst */
    CHECK_EQ(p.stats.frames, frames_before);
    rtl815x_rx_parser_feed(&p, next.buf, next.len, true);        /* next burst is clean */
    CHECK_EQ(sink.n, 1);
    CHECK(sink.n == 1 && sink.len[0] == sizeof(g) && memcmp(sink.data[0], g, sizeof(g)) == 0);
    sink_free(&sink);

    /* A dropped transfer that ended the burst: the next one starts fresh,
     * so nothing is discarded. */
    rtl815x_rx_parser_feed(&p, burst.buf, 512, false);
    rtl815x_rx_parser_discard_burst(&p, true);
    CHECK_EQ(p.stats.discarded, 1);
    CHECK(!p.resync);
    CHECK_EQ(p.frame_len, 0);
    rtl815x_rx_parser_feed(&p, next.buf, next.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);

    /* Restart after the pipe was halted mid-aggregate: the device continues
     * with the tail of the old aggregate, which must not be parsed as a
     * descriptor. The tail is delivered as a full transfer then a short one. */
    rtl815x_rx_parser_reset(&p);
    uint32_t desync_before = p.stats.desync;
    rtl815x_rx_parser_discard_burst(&p, false);                  /* rx_start */
    rtl815x_rx_parser_feed(&p, burst.buf + 512, 512, false);
    rtl815x_rx_parser_feed(&p, burst.buf + 1024, burst.len - 1024, true);
    CHECK_EQ(sink.n, 0);
    CHECK_EQ(p.stats.desync, desync_before);
    rtl815x_rx_parser_feed(&p, next.buf, next.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);
    free(burst.buf);
    free(next.buf);
}

static void test_rx_restart(void)
{
    static uint8_t carry[RTL815X_RX_MAX_FRAME];
    rtl815x_rx_parser_t p;
    sink_t sink = {0};
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);

    uint8_t a[61], b[300], big[1400];
    fill_frame(a, sizeof(a), 1);
    fill_frame(b, sizeof(b), 2);
    fill_frame(big, sizeof(big), 3);

    /* 1. Clean single-frame burst right after a restart is delivered: the
     *    first frame after a link-up is not lost. */
    stream_t one = {0};
    stream_add_frame(&one, a, sizeof(a), 0);         /* last record not padded */
    CHECK(rtl815x_rx_burst_is_complete(one.buf, one.len));
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, one.buf, one.len, true);
    CHECK_EQ(sink.n, 1);
    CHECK(sink.n == 1 && sink.len[0] == sizeof(a) && memcmp(sink.data[0], a, sizeof(a)) == 0);
    sink_free(&sink);

    /* 2. Several frames in one short transfer, last one padded: delivered. */
    stream_t two = {0};
    stream_add_frame(&two, a, sizeof(a), 1);
    stream_add_frame(&two, b, sizeof(b), 1);
    CHECK(rtl815x_rx_burst_is_complete(two.buf, two.len));
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, two.buf, two.len, true);
    CHECK_EQ(sink.n, 2);
    sink_free(&sink);

    /* 3. Tail of an old aggregate (starts mid-frame) after a restart: dropped,
     *    and the next burst is clean. */
    stream_t old = {0};
    stream_add_frame(&old, big, sizeof(big), 1);
    stream_add_frame(&old, b, sizeof(b), 1);
    size_t cut = 600;                                 /* inside big's payload */
    CHECK(!rtl815x_rx_burst_is_complete(old.buf + cut, old.len - cut));
    uint32_t discarded = p.stats.discarded;
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, old.buf + cut, old.len - cut, true);
    CHECK_EQ(sink.n, 0);
    CHECK_EQ(p.stats.discarded, discarded + 1);
    rtl815x_rx_parser_feed(&p, one.buf, one.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);

    /* 4. A tail that happens to start exactly at a record boundary is real,
     *    complete data and is delivered. */
    size_t boundary = usb_eth_round_up(RTL815X_RX_DESC_LEN + sizeof(big) + 4, 8);
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, old.buf + boundary, old.len - boundary, true);
    CHECK_EQ(sink.n, 1);
    CHECK(sink.n == 1 && sink.len[0] == sizeof(b) && memcmp(sink.data[0], b, sizeof(b)) == 0);
    sink_free(&sink);

    /* 5. First transfer after the restart is full (burst continues): the whole
     *    burst is dropped, including its short tail. */
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, old.buf, 512, false);
    rtl815x_rx_parser_feed(&p, old.buf + 512, old.len - 512, true);
    CHECK_EQ(sink.n, 0);
    rtl815x_rx_parser_feed(&p, one.buf, one.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);

    /* 6. A ZLP ending the burst right after the restart: nothing delivered,
     *    next burst clean. */
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, NULL, 0, true);
    rtl815x_rx_parser_feed(&p, one.buf, one.len, true);
    CHECK_EQ(sink.n, 1);
    sink_free(&sink);

    /* 7. A transfer dropped mid-burst by the callback keeps the strict
     *    discard: the next short transfer is that burst's tail, never accepted,
     *    even if it looks complete. */
    rtl815x_rx_parser_discard_burst(&p, false);
    rtl815x_rx_parser_feed(&p, one.buf, one.len, true);
    CHECK_EQ(sink.n, 0);
    sink_free(&sink);

    /* is_complete edge cases */
    CHECK(!rtl815x_rx_burst_is_complete(NULL, 0));
    CHECK(!rtl815x_rx_burst_is_complete(one.buf, RTL815X_RX_DESC_LEN - 1));
    CHECK(!rtl815x_rx_burst_is_complete(one.buf, one.len - 1));     /* frame cut short */
    uint8_t runt[RTL815X_RX_DESC_LEN + 10] = {0};
    usb_eth_put_le32(runt, 10);
    CHECK(!rtl815x_rx_burst_is_complete(runt, sizeof(runt)));       /* implausible length */
    stream_t trail = {0};
    stream_add_frame(&trail, a, sizeof(a), 1);
    static const uint8_t junk[12] = {1, 2, 3};
    stream_put(&trail, junk, sizeof(junk));                          /* bytes after the last record */
    CHECK(!rtl815x_rx_burst_is_complete(trail.buf, trail.len));

    free(one.buf);
    free(two.buf);
    free(old.buf);
    free(trail.buf);
}

static void test_chip_variants(void)
{
    const rtl815x_chip_t *c = rtl815x_chip_lookup(RTL815X_VER_4C10);
    CHECK(c != NULL);
    CHECK_EQ(c->family, RTL815X_FAMILY_8152);
    CHECK(strcmp(c->name, "RTL8152") == 0);
    c = rtl815x_chip_lookup(RTL815X_VER_4C00);
    CHECK(c != NULL && c->family == RTL815X_FAMILY_8152);
    /* the Elecrow kit's adapter: PLA_TCR1 word 0x6310 & 0x7cf0 */
    CHECK_EQ(0x6310 & RTL815X_VERSION_MASK, RTL815X_VER_6010);
    c = rtl815x_chip_lookup(0x6310 & RTL815X_VERSION_MASK);
    CHECK(c != NULL);
    CHECK_EQ(c->family, RTL815X_FAMILY_8153B);
    CHECK(strcmp(c->name, "RTL8153B") == 0);
    c = rtl815x_chip_lookup(RTL815X_VER_6000);
    CHECK(c != NULL && c->family == RTL815X_FAMILY_8153B);
    /* RTL8153A (0x5c00..0x5c30), RTL8156 (0x7020/0x7030, 0x74x0) and junk: not supported */
    CHECK(rtl815x_chip_lookup(0x5c00) == NULL);
    CHECK(rtl815x_chip_lookup(0x5c30) == NULL);
    CHECK(rtl815x_chip_lookup(0x7030) == NULL);
    CHECK(rtl815x_chip_lookup(0x7410) == NULL);
    CHECK(rtl815x_chip_lookup(0) == NULL);
}

static void test_rtl8153b_rx_sizing(void)
{
    /* FreeBSD: (32 KiB - (FRAMELEN(1500) + sizeof(struct ure_rxpkt) + URE_RXPKT_ALIGN)) / 8 */
    CHECK_EQ(rtl815x_rx_early_size(32768), (32768 - (1522 + 24 + 8)) / 8);
    CHECK_EQ(rtl815x_rx_early_size(32768), 3901);
    CHECK_EQ(rtl815x_rx_early_size(16384), 1853);
    CHECK_EQ(rtl815x_rx_early_size(4096), 317);
    /* below one record + 8: no room, 0 */
    CHECK_EQ(rtl815x_rx_early_size(1554), 0);
    CHECK_EQ(rtl815x_rx_early_size(1561), 0);
    CHECK_EQ(rtl815x_rx_early_size(1562), 1);
    CHECK_EQ(rtl815x_rx_early_size(0), 0);
    /* the threshold always leaves room for a whole maximum-size record */
    for (size_t sz = 2048; sz <= 65536; sz += 512) {
        size_t thr = (size_t)rtl815x_rx_early_size(sz) * 8;
        CHECK(thr + RTL815X_RX_DESC_LEN + RTL815X_RX_MAX_FRAME + RTL815X_RX_ALIGN <= sz);
    }
    /* the 8153B default at high speed is a valid transfer size as is */
    CHECK_EQ(rtl815x_rx_xfer_size(32768, 512), 32768);
    CHECK_EQ(rtl815x_rx_xfer_size(32768, 64), 32768);
}

static void test_rtl8153b_mac_and_link(void)
{
    const uint8_t factory[6] = {0x00, 0xe0, 0x4c, 0x68, 0x01, 0x02};
    const uint8_t stale[6] = {0xe8, 0xf6, 0x0a, 0xe2, 0x74, 0xc3};
    uint8_t out[6];
    /* the RTL8153B keeps its factory address in PLA_BACKUP too (FreeBSD ure_attach_post) */
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_6010, stale, factory, out), RTL815X_MAC_SRC_BACKUP);
    CHECK(memcmp(out, factory, 6) == 0);
    CHECK_EQ(rtl815x_pick_mac(RTL815X_VER_6000, factory, NULL, out), RTL815X_MAC_SRC_IDR);
    /* gigabit link */
    rtl815x_link_t l = rtl815x_decode_phystatus(RTL815X_PHYSTATUS_LINK | RTL815X_PHYSTATUS_FDX |
                                                RTL815X_PHYSTATUS_1000MBPS);
    CHECK(l.link && l.full_duplex);
    CHECK_EQ(l.speed_mbps, 1000);
}

/* First bulk-IN transfer after re-attaching an RTL8153B (ESP32-S3, full speed,
 * driver reinstall; the RTL8152 does the same): the 80-byte tail of an MLDv2
 * report from a cut aggregate.
 * The first 48 bytes are from the capture; the rest completes the address
 * records and the FCS. */
static void test_stale_tail_after_attach(void)
{
    static const uint8_t tail[80] = {
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x16, 0x3a, 0x00,
        0x05, 0x02, 0x00, 0x00, 0x01, 0x00, 0x8f, 0x00, 0xa7, 0x62, 0x00, 0x00, 0x00, 0x02, 0x04, 0x00,
        0x00, 0x00, 0xff, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x01, 0xff, 0x00, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0xff, 0x02, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xff, 0x20, 0xf6, 0xd9, 0xde, 0xad, 0xbe, 0xef,
    };
    uint8_t carry[RTL815X_RX_MAX_FRAME];
    sink_t sink = {0};
    rtl815x_rx_parser_t p;

    /* As a fresh stream (the rule before): counted as a runt + desync, i.e. rx_dropped 2 */
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);
    rtl815x_rx_parser_reset(&p);
    rtl815x_rx_parser_feed(&p, tail, sizeof(tail), true);
    CHECK_EQ(p.stats.runts + p.stats.desync + p.stats.truncated, 2);
    CHECK_EQ(sink.n, 0);

    /* As a restart (what rtl815x now does on every attach): discarded as a cut
     * burst, nothing counted as dropped, and the next burst is delivered */
    rtl815x_rx_parser_init(&p, carry, sizeof(carry), sink_cb, &sink);
    rtl815x_rx_parser_restart(&p);
    rtl815x_rx_parser_feed(&p, tail, sizeof(tail), true);
    CHECK_EQ(p.stats.runts + p.stats.desync + p.stats.truncated, 0);
    CHECK_EQ(p.stats.discarded, 1);
    CHECK_EQ(sink.n, 0);
    stream_t st = {0};
    uint8_t f[98];
    fill_frame(f, sizeof(f), 9);
    stream_add_frame(&st, f, sizeof(f), 1);
    rtl815x_rx_parser_feed(&p, st.buf, st.len, true);
    CHECK_EQ(sink.n, 1);
    CHECK_EQ(sink.len[0], sizeof(f));
    CHECK(memcmp(sink.data[0], f, sizeof(f)) == 0);
    CHECK_EQ(p.stats.runts + p.stats.desync + p.stats.truncated, 0);
    sink_free(&sink);
    free(st.buf);
}

int main(void)
{
    RUN(test_rx_xfer_size);
    RUN(test_tx_xfer_size_and_zlp);
    RUN(test_tx_desc);
    RUN(test_tx_pack);
    RUN(test_rx_single_and_aggregated);
    RUN(test_rx_split_across_transfers);
    RUN(test_rx_descriptor_split);
    RUN(test_rx_truncated_burst);
    RUN(test_rx_desync_and_runt);
    RUN(test_rx_parser_init_args);
    RUN(test_reg_encoding);
    RUN(test_mcast);
    RUN(test_pick_mac);
    RUN(test_link_and_fifo);
    RUN(test_rx_discard_after_gap);
    RUN(test_rx_restart);
    RUN(test_chip_variants);
    RUN(test_rtl8153b_rx_sizing);
    RUN(test_rtl8153b_mac_and_link);
    RUN(test_stale_tail_after_attach);
    return REPORT();
}
