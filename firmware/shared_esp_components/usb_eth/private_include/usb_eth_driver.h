/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * The contract between the usb_eth core and a chip or class driver.
 *
 * The core owns everything that does not depend on the adapter: the USB host
 * client, attach/detach (including unplug and zombie handling), the bulk
 * transfer pools and their ownership, RX gating, endpoint error and STALL
 * recovery, the worker and client tasks, the MAC address policy, the
 * multicast/promiscuous filter state, and the esp_eth MAC/PHY objects.
 *
 * A driver describes which devices it handles (ids), which interface and
 * endpoints carry the data (select_eps), how big the transfers are (sizing),
 * and implements the chip side: identification and init (bind), the station
 * address (read_mac/set_mac), the filter (set_filter), the PHY (mdio_*), and
 * the wire format (rx_* parser, tx_pack). A class driver with no vendor
 * registers fits the same table: leave mdio_* NULL and report the link with
 * usb_eth_report_link(), leave set_mac NULL for a fixed station address, and
 * pick a data interface alternate setting in select_eps.
 *
 * Threads (see core/usb_eth_core.c):
 *  - "control" ops (bind, read_mac, up, set_mac, set_filter, mdio_*) run in the
 *    worker task or an esp_eth caller with the core's register lock held, the
 *    device in DEV_INIT (bind, read_mac, up) or DEV_READY. They may block on
 *    control transfers (usb_eth_ctrl).
 *  - "data" ops (rx_feed, rx_discard) run in the client task from the bulk-IN
 *    completion, rx_reset in the worker with RX idle, tx_pack in the caller of
 *    esp_eth_transmit. They must not block or do control transfers.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "usb/usb_host.h"
#include "usb_eth_util.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Core-owned per-instance state; opaque to drivers. */
typedef struct usb_eth_dev usb_eth_dev_t;
typedef struct usb_eth_driver usb_eth_driver_t;     /* also declared in usb_eth.h */

/* ---- matching ------------------------------------------------------------ */

#define USB_ETH_MATCH_VIDPID    0x01    /* idVendor/idProduct */
#define USB_ETH_MATCH_IFCLASS   0x02    /* bInterfaceClass/SubClass/Protocol of any interface
                                           of the active configuration */

typedef struct {
    uint8_t match;          /* USB_ETH_MATCH_* (both: both must match) */
    uint16_t vid;
    uint16_t pid;
    uint8_t if_class;
    uint8_t if_subclass;
    uint8_t if_protocol;
    uint32_t info;          /* driver-private (variant, quirk flags) */
} usb_eth_id_t;

/* ---- what select_eps chose --------------------------------------------- */

typedef struct {
    uint8_t iface;          /* interface the core claims; carries the bulk pair */
    uint8_t alt;            /* its alternate setting (CDC data interfaces: 1) */
    uint8_t ep_in;
    uint8_t ep_out;
    uint16_t mps_in;
    uint16_t mps_out;
} usb_eth_eps_t;

/* ---- sizing ---------------------------------------------------------------- */

typedef struct {
    size_t rx_size;         /* bytes per bulk-IN transfer, a multiple of mps_in */
    size_t tx_size;         /* bytes per bulk-OUT transfer buffer (header + one frame + padding) */
} usb_eth_sizing_t;

/* ---- filter state kept by the core (survives re-plugging) ---------------- */

typedef struct {
    bool promisc;
    bool all_multi;
    uint8_t n_mcast;
    const uint8_t (*mcast)[USB_ETH_ADDR_LEN];
} usb_eth_filter_t;

/* ---- link, for drivers without an MDIO PHY -------------------------------- */

typedef struct {
    bool up;
    bool full_duplex;
    uint16_t mbps;          /* 10, 100, 1000, ... (the PHY object reports at most 100) */
} usb_eth_link_t;

/* ---- statistics a driver keeps (cumulative since priv_init) --------------- */

typedef struct {
    uint32_t rx_dropped;    /* frames lost in the wire format: runts, desync, truncated */
    uint32_t rx_reassembled;/* frames that spanned more than one bulk transfer */
} usb_eth_drv_stats_t;

/* ---- the driver -------------------------------------------------------------- */

struct usb_eth_driver {
    const char *name;
    const usb_eth_id_t *ids;
    size_t n_ids;
    size_t priv_size;       /* per-instance state, see usb_eth_priv() */

    /* Lifetime of the private state. priv_init runs before the first bind of
     * this driver on an instance (zeroed memory), priv_deinit when the instance
     * is deleted or another driver takes over. Optional. */
    esp_err_t (*priv_init)(usb_eth_dev_t *d, void *priv);
    void (*priv_deinit)(usb_eth_dev_t *d, void *priv);

    /* Device open, nothing claimed. Fill eps. NULL: the first vendor-specific
     * (class 0xff) interface, alternate 0, with a bulk IN/OUT pair. */
    esp_err_t (*select_eps)(usb_eth_dev_t *d, const usb_config_desc_t *cfg, const usb_eth_id_t *id,
                            usb_eth_eps_t *eps);
    /* Transfer sizes for this device. @p rx_request is the configured size
     * (0 = driver default). Required. */
    void (*sizing)(usb_eth_dev_t *d, const usb_eth_eps_t *eps, usb_speed_t speed, size_t rx_request,
                   usb_eth_sizing_t *out);

    /* Control ops (see threads above) */
    esp_err_t (*bind)(usb_eth_dev_t *d, const usb_eth_id_t *id);   /* identify + init; ESP_ERR_NOT_SUPPORTED:
                                                                      not a chip this driver handles */
    esp_err_t (*read_mac)(usb_eth_dev_t *d, uint8_t mac[USB_ETH_ADDR_LEN]); /* ESP_ERR_NOT_FOUND: none */
    esp_err_t (*up)(usb_eth_dev_t *d, const uint8_t mac[USB_ETH_ADDR_LEN], const usb_eth_filter_t *f);
    esp_err_t (*set_mac)(usb_eth_dev_t *d, const uint8_t mac[USB_ETH_ADDR_LEN]);    /* NULL: fixed */
    esp_err_t (*set_filter)(usb_eth_dev_t *d, const usb_eth_filter_t *f);
    esp_err_t (*mdio_read)(usb_eth_dev_t *d, uint32_t reg, uint16_t *val);  /* NULL: no MDIO PHY, see */
    esp_err_t (*mdio_write)(usb_eth_dev_t *d, uint32_t reg, uint16_t val);  /* usb_eth_report_link() */

    /* Data ops */
    void (*rx_reset)(usb_eth_dev_t *d, bool fresh);    /* RX (re)starts. fresh: new device, the stream
                                                          starts clean; else the IN pipe was halted and
                                                          an aggregate may have been cut */
    void (*rx_feed)(usb_eth_dev_t *d, const uint8_t *data, size_t len, bool end_of_burst);
    void (*rx_discard)(usb_eth_dev_t *d, bool end_of_burst);   /* a completed transfer was dropped */
    size_t (*tx_pack)(usb_eth_dev_t *d, uint8_t *dst, size_t cap, const usb_eth_iov_t *iov, size_t n);
    void (*get_stats)(usb_eth_dev_t *d, usb_eth_drv_stats_t *s);   /* optional */
};

/* ---- core services for drivers --------------------------------------------- */

/** The driver's private state (priv_size bytes). */
void *usb_eth_priv(usb_eth_dev_t *d);

/** USB speed of the device being bound / attached. */
usb_speed_t usb_eth_speed(const usb_eth_dev_t *d);

/** Endpoints chosen by select_eps. */
const usb_eth_eps_t *usb_eth_eps(const usb_eth_dev_t *d);

/**
 * Control transfer on EP0 (control ops only). @p data is read (IN, bmRequestType
 * bit 7 set) or written (OUT); at most 64 bytes. Fails with ESP_ERR_INVALID_STATE
 * once the device is going away, ESP_ERR_TIMEOUT after ctrl_timeout_ms.
 */
esp_err_t usb_eth_ctrl(usb_eth_dev_t *d, uint8_t bm_request_type, uint8_t b_request, uint16_t w_value,
                       uint16_t w_index, void *data, uint16_t len);

/** Name and identifier of the chip, for logs and USB_ETH_CMD_G_INFO (call from bind). */
void usb_eth_set_chip(usb_eth_dev_t *d, const char *name, uint32_t id);

/** rx_feed: pass one frame (Ethernet header first, no FCS) to the stack. Copies it. */
void usb_eth_rx_deliver(usb_eth_dev_t *d, const uint8_t *frame, size_t len);

/** Drivers without mdio_*: report the link; the PHY object sees a matching MII register set. */
void usb_eth_report_link(usb_eth_dev_t *d, const usb_eth_link_t *link);

/* ---- registry (core/usb_eth_registry.c) ------------------------------------ */

/** Every driver enabled in menuconfig, vendor drivers before class drivers. */
extern const usb_eth_driver_t *const usb_eth_builtin_drivers[];
extern const size_t usb_eth_builtin_driver_count;

#ifdef __cplusplus
}
#endif
