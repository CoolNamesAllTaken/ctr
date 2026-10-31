/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * usb_eth core internals shared by usb_eth_core.c (USB side) and
 * usb_eth_mac.c (esp_eth side). Drivers never include this file.
 */
#pragma once

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_eth_mac.h"
#include "usb/usb_host.h"
#include "usb_eth.h"
#include "usb_eth_driver.h"
#include "usb_eth_util.h"

#define USB_ETH_MAX_MCAST       16

#define EVT_FIRST_ATTACH        BIT0
#define EVT_WORKER_EXITED       BIT1
#define EVT_CLIENT_EXITED       BIT2

/* Work for the worker task, as event-group bits: requests coalesce and can
 * never be lost (unlike messages in a full queue). */
#define WORK_QUIT               BIT0    /* tear down and exit */
#define WORK_SCAN               BIT1    /* look for a supported adapter among the attached devices */
#define WORK_GONE               BIT2    /* gone_hdl was unplugged */
#define WORK_RX_RECOVER         BIT3    /* bulk-IN pipe halted by an error */
#define WORK_RX_SYNC            BIT4    /* rx_wanted() changed: start or stop bulk-IN */
#define WORK_TX_RECOVER         BIT5    /* bulk-OUT pipe halted by an error */
#define WORK_ALL                (WORK_QUIT | WORK_SCAN | WORK_GONE | WORK_RX_RECOVER | WORK_RX_SYNC | \
                                 WORK_TX_RECOVER)

typedef enum {
    DEV_ABSENT,     /* no adapter, PHY registers are virtual */
    DEV_INIT,       /* opened and claimed, chip being initialised */
    DEV_READY,      /* data path usable */
    DEV_CLOSING,    /* teardown in progress */
} dev_state_t;

struct usb_eth_dev {
    esp_eth_mac_t parent;
    esp_eth_mediator_t *eth;
    usb_eth_config_t cfg;
    eth_mac_config_t mac_cfg;
    const usb_eth_driver_t *const *drivers;    /* cfg.drivers or the built-in table */
    size_t n_drivers;

    /* USB host client */
    usb_host_client_handle_t client;
    TaskHandle_t client_task;
    TaskHandle_t worker_task;
    EventGroupHandle_t work_ev;     /* WORK_* bits */
    _Atomic(usb_device_handle_t) gone_hdl;  /* set by DEV_GONE, consumed by the worker */
    usb_device_handle_t closed_hdl;         /* last handle we closed, for the drain in client_task */
    atomic_bool drain_pending;      /* client task must drain stale DEV_GONE before a rescan */
    portMUX_TYPE skip_mux;
    usb_eth_addr_set_t skip_addrs;  /* USB addresses that did not bind; cleared by NEW_DEV */
    EventGroupHandle_t events;
    volatile bool client_stop;

    /* Register access */
    SemaphoreHandle_t reg_lock;
    SemaphoreHandle_t ctrl_done;
    int64_t ctrl_done_us;       /* esp_timer time of the last ctrl_xfer_cb; under ctrl_mux */
    portMUX_TYPE ctrl_mux;      /* 64-bit stores are not atomic on these 32-bit cores */
    usb_transfer_t *ctrl_xfer;
    bool ctrl_stuck;
    uint32_t ctrl_timeout_ms;   /* cfg.ctrl_timeout_ms, lowered for PHY polls; under reg_lock */

    /* Driver. drv owns priv (priv_ready: priv_init done) from its first bind
     * until another driver binds or the instance is deleted. */
    const usb_eth_driver_t *drv;
    const usb_eth_id_t *drv_id;
    void *priv;
    size_t priv_size;
    bool priv_ready;
    const char *chip_name;
    uint32_t chip_id;

    /* Attached device */
    usb_device_handle_t dev;
    volatile dev_state_t state;
    usb_speed_t speed;
    uint16_t vid;
    uint16_t pid;
    usb_eth_eps_t eps;
    size_t rx_size;
    size_t tx_size;
    usb_transfer_t *rx_xfer[USB_ETH_MAX_XFERS];
    usb_transfer_t *tx_xfer[USB_ETH_MAX_XFERS];
    uint8_t n_rx;
    uint8_t n_tx;
    QueueHandle_t tx_pool;
    atomic_int rx_inflight;
    SemaphoreHandle_t xfer_lock;    /* guards rx_xfer/rx_busy/tx_xfer slots and n_rx/n_tx */
    bool rx_busy[USB_ETH_MAX_XFERS];
    volatile bool rx_enabled;
    bool rx_stream_fresh;       /* no bulk-IN submitted since attach */
    /* Endpoint error recovery (worker). The callbacks only set the *_good /
     * *_was_stall flags; the limiters are touched by the worker alone. */
    atomic_bool rx_err;         /* bulk-IN pipe halted by an error, recovery pending */
    atomic_bool rx_was_stall;
    atomic_bool rx_good;
    usb_eth_recovery_t rx_rec;
    atomic_bool tx_err;
    atomic_bool tx_was_stall;
    atomic_bool tx_good;
    usb_eth_recovery_t tx_rec;
    volatile bool tx_paused;    /* bulk-OUT halted: transmit refuses until recovered */
    atomic_int n_abandoned;     /* transfers abandoned by teardown, not yet returned */
    bool zombie;                /* dev still open: USB stack holds resources, retry release */
    bool iface_claimed;

    /* Configuration that survives re-plugging */
    uint8_t addr[USB_ETH_ADDR_LEN];
    bool addr_valid;
    bool promisc;
    bool all_multi;
    uint8_t mcast[USB_ETH_MAX_MCAST][USB_ETH_ADDR_LEN];
    uint8_t n_mcast;
    /* RX runs while the link is up (esp_eth reports it through set_link, it
     * never calls mac->start/stop) and the MAC is not administratively
     * stopped (mac->stop). rx_gate makes turning it off synchronous for frame
     * delivery: see usb_eth_rx_gate_close(). */
    atomic_bool link_up;
    atomic_bool admin_down;
    SemaphoreHandle_t rx_gate;
    bool initialized;   /* mac->init succeeded and usb_eth_usb_shutdown() has not run */
    uint16_t vphy[32];          /* MII registers without a device, or of a driver without mdio_* */
    usb_eth_link_t vphy_link;   /* link reported by usb_eth_report_link() */
    uint16_t phy_cache[32];     /* last values read from the real PHY */
    uint32_t phy_cache_valid;   /* bit n: phy_cache[n] valid; cleared on attach */

    /* Statistics */
    atomic_uint rx_frames;
    atomic_uint rx_xfers;
    atomic_uint rx_nomem;
    atomic_uint tx_frames;
    atomic_uint tx_errors;
    uint32_t attach_count;
};

extern const char *const USB_ETH_TAG;

/* usb_eth_core.c, called from usb_eth_mac.c */
esp_err_t usb_eth_core_start(usb_eth_dev_t *d);     /* client + tasks (mac->init) */
void usb_eth_usb_shutdown(usb_eth_dev_t *d);        /* reverse of usb_eth_core_start */
void usb_eth_rx_gate_close(usb_eth_dev_t *d);
esp_err_t usb_eth_transmit_iov(usb_eth_dev_t *d, const usb_eth_iov_t *iov, size_t n);
esp_err_t usb_eth_apply_filter(usb_eth_dev_t *d);   /* caller holds reg_lock, state DEV_READY */
void usb_eth_vphy_reset(usb_eth_dev_t *d);
