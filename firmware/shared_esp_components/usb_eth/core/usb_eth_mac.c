/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * usb_eth core, esp_eth side: the esp_eth_mac_t object (PHY register access,
 * address, link, filters, ioctls, transmit) and the constructors. The USB side
 * is in usb_eth_core.c.
 */
#include <inttypes.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/cdefs.h>
#include "esp_check.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "usb_eth_priv.h"

static const char *TAG = "usb_eth";

#define PHY_LOCK_TIMEOUT_MS     50      /* read_phy_reg runs in the esp_timer task */
#define PHY_CTRL_TIMEOUT_MS     100     /* control timeout for PHY polls, see read_phy_reg() */
#define MAX_RECOVERIES          5       /* per endpoint, without a good transfer in between */

/* Program the filter state into the adapter. Caller holds reg_lock, state DEV_READY. */
esp_err_t usb_eth_apply_filter(usb_eth_dev_t *d)
{
    usb_eth_filter_t f = {
        .promisc = d->promisc, .all_multi = d->all_multi, .n_mcast = d->n_mcast, .mcast = d->mcast,
    };
    return d->drv->set_filter(d, &f);
}

static esp_err_t usb_eth_mac_set_mediator(esp_eth_mac_t *mac, esp_eth_mediator_t *eth)
{
    ESP_RETURN_ON_FALSE(eth, ESP_ERR_INVALID_ARG, TAG, "can't set mac's mediator to null");
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    d->eth = eth;
    return ESP_OK;
}

static esp_err_t usb_eth_mac_read_phy_reg(esp_eth_mac_t *mac, uint32_t phy_addr, uint32_t phy_reg, uint32_t *reg_value)
{
    ESP_RETURN_ON_FALSE(reg_value && phy_reg < 32, ESP_ERR_INVALID_ARG, TAG, "invalid argument");
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    (void)phy_addr;
    /* This runs in the esp_timer task (esp_eth link poll), so it must not wait
     * long. Without a ready adapter (or a driver with no MDIO PHY) answer from the virtual PHY (link down). If
     * reg_lock is busy (attach, endpoint recovery, a slow register access),
     * answer with the last value read from the real PHY instead of blocking
     * every esp_timer callback in the system. */
    if (d->state != DEV_READY || d->drv->mdio_read == NULL) {
        *reg_value = d->vphy[phy_reg];
        return ESP_OK;
    }
    if (xSemaphoreTakeRecursive(d->reg_lock, pdMS_TO_TICKS(PHY_LOCK_TIMEOUT_MS)) != pdTRUE) {
        bool cached = d->state == DEV_READY && (d->phy_cache_valid & (1u << phy_reg));
        *reg_value = cached ? d->phy_cache[phy_reg] : d->vphy[phy_reg];  /* teardown: link down */
        return ESP_OK;
    }
    /* A wedged adapter that NAKs EP0 would otherwise hold the esp_timer task
     * for ctrl_timeout_ms per control transfer (two per OCP read). A live chip
     * answers in about a millisecond, so poll with a short timeout. After a
     * timeout ctrl_stuck makes further register access fail fast until the
     * stuck transfer completes (or the adapter is unplugged). */
    uint32_t saved_timeout = d->ctrl_timeout_ms;
    d->ctrl_timeout_ms = PHY_CTRL_TIMEOUT_MS < saved_timeout ? PHY_CTRL_TIMEOUT_MS : saved_timeout;
    esp_err_t ret = ESP_OK;
    uint16_t v;
    if (d->state == DEV_READY && (ret = d->drv->mdio_read(d, phy_reg, &v)) == ESP_OK) {
        *reg_value = v;
        d->phy_cache[phy_reg] = v;
        d->phy_cache_valid |= 1u << phy_reg;
    } else if (d->state != DEV_READY) {
        *reg_value = d->vphy[phy_reg];   /* unplugged meanwhile */
        ret = ESP_OK;
    } else if (d->phy_cache_valid & (1u << phy_reg)) {
        /* Control transfer failed or timed out (ctrl_stuck): keep reporting the
         * last known value rather than an error, like a reg_lock timeout. */
        *reg_value = d->phy_cache[phy_reg];
        ret = ESP_OK;
    }
    d->ctrl_timeout_ms = saved_timeout;
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

static esp_err_t usb_eth_mac_write_phy_reg(esp_eth_mac_t *mac, uint32_t phy_addr, uint32_t phy_reg, uint32_t reg_value)
{
    ESP_RETURN_ON_FALSE(phy_reg < 32, ESP_ERR_INVALID_ARG, TAG, "invalid argument");
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    (void)phy_addr;
    if (d->state != DEV_READY || d->drv->mdio_write == NULL) {
        if (phy_reg == USB_ETH_MII_BMCR && (reg_value & USB_ETH_MII_BMCR_RESET)) {
            usb_eth_vphy_reset(d);
        } else if (phy_reg != USB_ETH_MII_BMSR) {
            d->vphy[phy_reg] = (uint16_t)reg_value;
        }
        return ESP_OK;
    }
    esp_err_t ret = ESP_OK;
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    if (d->state == DEV_READY) {
        ret = d->drv->mdio_write(d, phy_reg, (uint16_t)reg_value);
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

static esp_err_t usb_eth_mac_set_addr(esp_eth_mac_t *mac, uint8_t *addr)
{
    ESP_RETURN_ON_FALSE(addr, ESP_ERR_INVALID_ARG, TAG, "can't set mac addr to null");
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    esp_err_t ret = ESP_OK;
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    memcpy(d->addr, addr, ETH_ADDR_LEN);
    d->addr_valid = true;
    if (d->state == DEV_READY) {
        ret = d->drv->set_mac ? d->drv->set_mac(d, d->addr) : ESP_ERR_NOT_SUPPORTED;
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

static esp_err_t usb_eth_mac_get_addr(esp_eth_mac_t *mac, uint8_t *addr)
{
    ESP_RETURN_ON_FALSE(addr, ESP_ERR_INVALID_ARG, TAG, "can't set mac addr to null");
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    ESP_RETURN_ON_FALSE(d->addr_valid, ESP_ERR_INVALID_STATE, TAG, "MAC address not known yet");
    memcpy(addr, d->addr, ETH_ADDR_LEN);
    return ESP_OK;
}

/*
 * esp_eth starts and stops the MAC only through the link state: the PHY poll
 * reports link changes here, and esp_eth_stop() reports link down. Runs in the
 * esp_timer task (link poll) or the caller of esp_eth_stop(); it never waits
 * for USB, the worker submits or cancels the transfers.
 */
static esp_err_t usb_eth_mac_set_link(esp_eth_mac_t *mac, eth_link_t link)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    switch (link) {
    case ETH_LINK_UP:
        atomic_store(&d->link_up, true);
        xEventGroupSetBits(d->work_ev, WORK_RX_SYNC);
        break;
    case ETH_LINK_DOWN:
        atomic_store(&d->link_up, false);
        usb_eth_rx_gate_close(d);
        break;
    default:
        return ESP_ERR_INVALID_ARG;
    }
    ESP_LOGD(TAG, "link %s", link == ETH_LINK_UP ? "up" : "down");
    return ESP_OK;
}

static esp_err_t usb_eth_mac_set_speed(esp_eth_mac_t *mac, eth_speed_t speed)
{
    /* The adapter's MAC follows its PHY on its own. */
    (void)mac;
    return speed <= ETH_SPEED_100M ? ESP_OK : ESP_ERR_INVALID_ARG;
}

static esp_err_t usb_eth_mac_set_duplex(esp_eth_mac_t *mac, eth_duplex_t duplex)
{
    (void)mac;
    (void)duplex;
    return ESP_OK;
}

static esp_err_t usb_eth_mac_set_promiscuous(esp_eth_mac_t *mac, bool enable)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    esp_err_t ret = ESP_OK;
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    d->promisc = enable;
    if (d->state == DEV_READY) {
        ret = usb_eth_apply_filter(d);
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

static esp_err_t usb_eth_mac_enable_flow_ctrl(esp_eth_mac_t *mac, bool enable)
{
    /* Pause frames are not configured. */
    (void)mac;
    (void)enable;
    return ESP_OK;
}

static esp_err_t usb_eth_mac_set_peer_pause_ability(esp_eth_mac_t *mac, uint32_t ability)
{
    (void)mac;
    (void)ability;
    return ESP_OK;
}

static esp_err_t mcast_update(usb_eth_dev_t *d, const uint8_t *addr, bool add)
{
    ESP_RETURN_ON_FALSE(addr && (addr[0] & 0x01), ESP_ERR_INVALID_ARG, TAG, "not a multicast address");
    esp_err_t ret = ESP_OK;
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    int idx = -1;
    for (int i = 0; i < d->n_mcast; i++) {
        if (memcmp(d->mcast[i], addr, ETH_ADDR_LEN) == 0) {
            idx = i;
            break;
        }
    }
    if (add && idx < 0) {
        if (d->n_mcast < USB_ETH_MAX_MCAST) {
            memcpy(d->mcast[d->n_mcast++], addr, ETH_ADDR_LEN);
        } else {
            ret = ESP_ERR_NO_MEM;
        }
    } else if (!add && idx >= 0) {
        memmove(d->mcast[idx], d->mcast[idx + 1], (size_t)(d->n_mcast - idx - 1) * ETH_ADDR_LEN);
        d->n_mcast--;
    }
    if (ret == ESP_OK && d->state == DEV_READY) {
        ret = usb_eth_apply_filter(d);
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

static esp_err_t all_multi_update(usb_eth_dev_t *d, bool enable)
{
    esp_err_t ret = ESP_OK;
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    d->all_multi = enable;
    if (d->state == DEV_READY) {
        ret = usb_eth_apply_filter(d);
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    return ret;
}

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
/* IDF >= 5.5: esp_netif calls these when lwIP joins or leaves a multicast group (IGMP/MLD).
 * The chip has a single unicast address, so only multicast addresses can be filtered. */
static esp_err_t usb_eth_mac_add_mac_filter(esp_eth_mac_t *mac, uint8_t *addr)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    ESP_RETURN_ON_FALSE(addr, ESP_ERR_INVALID_ARG, TAG, "null address");
    ESP_RETURN_ON_FALSE(addr[0] & 0x01, ESP_ERR_NOT_SUPPORTED, TAG, "only multicast addresses can be filtered");
    return mcast_update(d, addr, true);
}

static esp_err_t usb_eth_mac_rm_mac_filter(esp_eth_mac_t *mac, uint8_t *addr)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    ESP_RETURN_ON_FALSE(addr, ESP_ERR_INVALID_ARG, TAG, "null address");
    ESP_RETURN_ON_FALSE(addr[0] & 0x01, ESP_ERR_NOT_SUPPORTED, TAG, "only multicast addresses can be filtered");
    return mcast_update(d, addr, false);
}

static esp_err_t usb_eth_mac_set_all_multicast(esp_eth_mac_t *mac, bool enable)
{
    return all_multi_update(__containerof(mac, usb_eth_dev_t, parent), enable);
}
#endif

static esp_err_t usb_eth_mac_custom_ioctl(esp_eth_mac_t *mac, int cmd, void *data)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);

    switch (cmd) {
    case USB_ETH_CMD_ADD_MCAST:
        return mcast_update(d, data, true);
    case USB_ETH_CMD_DEL_MCAST:
        return mcast_update(d, data, false);
    case USB_ETH_CMD_S_ALL_MULTICAST:
        ESP_RETURN_ON_FALSE(data, ESP_ERR_INVALID_ARG, TAG, "null data");
        return all_multi_update(d, *(bool *)data);
    case USB_ETH_CMD_G_INFO: {
        ESP_RETURN_ON_FALSE(data, ESP_ERR_INVALID_ARG, TAG, "null data");
        usb_eth_info_t *info = data;
        xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
        memset(info, 0, sizeof(*info));
        usb_eth_drv_stats_t ds = {0};
        if (d->priv_ready && d->drv->get_stats) {
            d->drv->get_stats(d, &ds);
        }
        info->attached = d->state == DEV_READY;
        info->usb_speed = (uint8_t)d->speed;
        info->vid = d->vid;
        info->pid = d->pid;
        info->driver = d->drv ? d->drv->name : NULL;
        info->chip = d->chip_name;
        info->chip_id = d->chip_id;
        info->mps_in = d->eps.mps_in;
        info->mps_out = d->eps.mps_out;
        info->rx_xfer_size = (uint32_t)d->rx_size;
        info->rx_frames = atomic_load(&d->rx_frames);
        info->rx_xfers = atomic_load(&d->rx_xfers);
        info->rx_dropped = atomic_load(&d->rx_nomem) + ds.rx_dropped;
        info->rx_reassembled = ds.rx_reassembled;
        info->tx_frames = atomic_load(&d->tx_frames);
        info->tx_errors = atomic_load(&d->tx_errors);
        info->attach_count = d->attach_count;
        xSemaphoreGiveRecursive(d->reg_lock);
        return ESP_OK;
    }
    default:
        return ESP_ERR_NOT_SUPPORTED;
    }
}

static esp_err_t usb_eth_mac_transmit(esp_eth_mac_t *mac, uint8_t *buf, uint32_t length)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    usb_eth_iov_t iov = {buf, length};
    return usb_eth_transmit_iov(d, &iov, 1);
}

static esp_err_t usb_eth_mac_transmit_vargs(esp_eth_mac_t *mac, uint32_t argc, va_list args)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    ESP_RETURN_ON_FALSE(argc > 0 && argc <= 8, ESP_ERR_INVALID_ARG, TAG, "unsupported number of buffers");
    usb_eth_iov_t iov[8];
    for (uint32_t i = 0; i < argc; i++) {
        iov[i].data = va_arg(args, uint8_t *);
        iov[i].len = va_arg(args, uint32_t);
    }
    return usb_eth_transmit_iov(d, iov, argc);
}

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
/* IDF >= 5.5: esp_eth_transmit_vargs() and esp_eth_transmit_ctrl_vargs() call this one, with argc
 * counting the variable arguments (two per buffer). ctrl requests a PTP time stamp, which only the
 * internal EMAC supports. */
static esp_err_t usb_eth_mac_transmit_ctrl_vargs(esp_eth_mac_t *mac, void *ctrl, uint32_t argc, va_list args)
{
    ESP_RETURN_ON_FALSE(ctrl == NULL, ESP_ERR_NOT_SUPPORTED, TAG, "time stamping not supported");
    ESP_RETURN_ON_FALSE(argc % 2 == 0, ESP_ERR_INVALID_ARG, TAG, "buffer without length");
    return usb_eth_mac_transmit_vargs(mac, argc / 2, args);
}
#endif

static esp_err_t usb_eth_mac_receive(esp_eth_mac_t *mac, uint8_t *buf, uint32_t *length)
{
    /* Frames are pushed to the stack from the USB RX callback. */
    (void)mac;
    (void)buf;
    (void)length;
    return ESP_ERR_NOT_SUPPORTED;
}

/* esp_eth itself never calls mac->start/stop (it uses set_link); these are
 * for applications that want to pause reception explicitly. */
static esp_err_t usb_eth_mac_start(esp_eth_mac_t *mac)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    atomic_store(&d->admin_down, false);
    xEventGroupSetBits(d->work_ev, WORK_RX_SYNC);
    return ESP_OK;
}

static esp_err_t usb_eth_mac_stop(esp_eth_mac_t *mac)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    atomic_store(&d->admin_down, true);
    usb_eth_rx_gate_close(d);
    return ESP_OK;
}

static esp_err_t usb_eth_mac_init(esp_eth_mac_t *mac)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    esp_eth_mediator_t *eth = d->eth;

    ESP_RETURN_ON_FALSE(!d->initialized, ESP_ERR_INVALID_STATE, TAG, "already initialised");
    ESP_RETURN_ON_ERROR(eth->on_state_changed(eth, ETH_STATE_LLINIT, NULL), TAG, "lowlevel init failed");
    esp_err_t ret = usb_eth_core_start(d);
    if (ret != ESP_OK) {
        eth->on_state_changed(eth, ETH_STATE_DEINIT, NULL);
        return ret;
    }

    EventBits_t bits = xEventGroupWaitBits(d->events, EVT_FIRST_ATTACH, pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(d->cfg.attach_timeout_ms));
    if (!(bits & EVT_FIRST_ATTACH)) {
        xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
        if (!d->addr_valid) {
            esp_read_mac(d->addr, ESP_MAC_ETH);
            d->addr_valid = true;
        }
        xSemaphoreGiveRecursive(d->reg_lock);
        ESP_LOGW(TAG, "no supported USB Ethernet adapter attached after %u ms; continuing, waiting for hot-plug",
                 (unsigned)d->cfg.attach_timeout_ms);
    }
    return ESP_OK;
}

static esp_err_t usb_eth_mac_deinit(esp_eth_mac_t *mac)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    esp_eth_mediator_t *eth = d->eth;
    usb_eth_usb_shutdown(d);
    eth->on_state_changed(eth, ETH_STATE_DEINIT, NULL);
    return ESP_OK;
}

/* Frees what usb_eth_mac_new() created. */
static void usb_eth_free(usb_eth_dev_t *d)
{
    if (d->drv && d->priv_ready && d->drv->priv_deinit) {
        d->drv->priv_deinit(d, d->priv);
    }
    if (d->work_ev) {
        vEventGroupDelete(d->work_ev);
    }
    if (d->tx_pool) {
        vQueueDelete(d->tx_pool);
    }
    if (d->events) {
        vEventGroupDelete(d->events);
    }
    if (d->reg_lock) {
        vSemaphoreDelete(d->reg_lock);
    }
    if (d->xfer_lock) {
        vSemaphoreDelete(d->xfer_lock);
    }
    if (d->rx_gate) {
        vSemaphoreDelete(d->rx_gate);
    }
    if (d->ctrl_done) {
        vSemaphoreDelete(d->ctrl_done);
    }
    free(d->priv);
    free(d);
}

static esp_err_t usb_eth_mac_del(esp_eth_mac_t *mac)
{
    usb_eth_dev_t *d = __containerof(mac, usb_eth_dev_t, parent);
    if (d->initialized) {
        usb_eth_usb_shutdown(d);     /* install failed after mac->init, or deinit was skipped */
    }
    usb_eth_free(d);
    return ESP_OK;
}

esp_eth_mac_t *usb_eth_mac_new(const usb_eth_config_t *config, const eth_mac_config_t *mac_config)
{
    esp_eth_mac_t *ret = NULL;
    usb_eth_dev_t *d = NULL;
    ESP_GOTO_ON_FALSE(config && mac_config, NULL, err, TAG, "invalid argument");
    ESP_GOTO_ON_FALSE(config->rx_xfer_count >= 1 && config->rx_xfer_count <= USB_ETH_MAX_XFERS &&
                      config->tx_xfer_count >= 1 && config->tx_xfer_count <= USB_ETH_MAX_XFERS,
                      NULL, err, TAG, "transfer counts must be 1..%d", USB_ETH_MAX_XFERS);
    ESP_GOTO_ON_FALSE((config->drivers == NULL) == (config->n_drivers == 0), NULL, err, TAG,
                      "drivers and n_drivers must be set together");

    d = calloc(1, sizeof(usb_eth_dev_t));
    ESP_GOTO_ON_FALSE(d, NULL, err, TAG, "no mem for MAC instance");
    d->cfg = *config;
    d->drivers = config->drivers ? config->drivers : usb_eth_builtin_drivers;
    d->n_drivers = config->drivers ? config->n_drivers : usb_eth_builtin_driver_count;
    for (size_t i = 0; i < d->n_drivers; i++) {
        const usb_eth_driver_t *drv = d->drivers[i];
        ESP_GOTO_ON_FALSE(drv && drv->sizing && drv->bind && drv->read_mac && drv->up && drv->set_filter &&
                          drv->rx_reset && drv->rx_feed && drv->rx_discard && drv->tx_pack &&
                          (drv->mdio_read == NULL) == (drv->mdio_write == NULL), NULL, err, TAG,
                          "driver %u (%s) lacks a required op", (unsigned)i, drv ? drv->name : "NULL");
        if (drv->priv_size > d->priv_size) {
            d->priv_size = drv->priv_size;
        }
    }
    d->ctrl_timeout_ms = config->ctrl_timeout_ms;
    d->mac_cfg = *mac_config;
    d->all_multi = config->accept_all_multicast;
    d->state = DEV_ABSENT;
    atomic_init(&d->rx_inflight, 0);
    usb_eth_recovery_init(&d->rx_rec, MAX_RECOVERIES);
    usb_eth_recovery_init(&d->tx_rec, MAX_RECOVERIES);
    usb_eth_vphy_reset(d);

    d->work_ev = xEventGroupCreate();
    d->skip_mux = (portMUX_TYPE)portMUX_INITIALIZER_UNLOCKED;
    d->ctrl_mux = (portMUX_TYPE)portMUX_INITIALIZER_UNLOCKED;
    d->tx_pool = xQueueCreate(USB_ETH_MAX_XFERS, sizeof(usb_transfer_t *));
    d->events = xEventGroupCreate();
    d->reg_lock = xSemaphoreCreateRecursiveMutex();
    d->xfer_lock = xSemaphoreCreateMutex();
    d->rx_gate = xSemaphoreCreateMutex();
    d->ctrl_done = xSemaphoreCreateBinary();
    d->priv = calloc(1, d->priv_size ? d->priv_size : 1);
    ESP_GOTO_ON_FALSE(d->work_ev && d->tx_pool && d->events && d->reg_lock && d->xfer_lock && d->rx_gate &&
                      d->ctrl_done && d->priv, NULL, err, TAG, "no mem for driver resources");

    d->parent.set_mediator = usb_eth_mac_set_mediator;
    d->parent.init = usb_eth_mac_init;
    d->parent.deinit = usb_eth_mac_deinit;
    d->parent.start = usb_eth_mac_start;
    d->parent.stop = usb_eth_mac_stop;
    d->parent.del = usb_eth_mac_del;
    d->parent.write_phy_reg = usb_eth_mac_write_phy_reg;
    d->parent.read_phy_reg = usb_eth_mac_read_phy_reg;
    d->parent.set_addr = usb_eth_mac_set_addr;
    d->parent.get_addr = usb_eth_mac_get_addr;
    d->parent.set_speed = usb_eth_mac_set_speed;
    d->parent.set_duplex = usb_eth_mac_set_duplex;
    d->parent.set_link = usb_eth_mac_set_link;
    d->parent.set_promiscuous = usb_eth_mac_set_promiscuous;
    d->parent.set_peer_pause_ability = usb_eth_mac_set_peer_pause_ability;
    d->parent.enable_flow_ctrl = usb_eth_mac_enable_flow_ctrl;
    d->parent.transmit = usb_eth_mac_transmit;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
    d->parent.transmit_ctrl_vargs = usb_eth_mac_transmit_ctrl_vargs;
    d->parent.add_mac_filter = usb_eth_mac_add_mac_filter;
    d->parent.rm_mac_filter = usb_eth_mac_rm_mac_filter;
    d->parent.set_all_multicast = usb_eth_mac_set_all_multicast;
#else
    d->parent.transmit_vargs = usb_eth_mac_transmit_vargs;
#endif
    d->parent.receive = usb_eth_mac_receive;
    d->parent.custom_ioctl = usb_eth_mac_custom_ioctl;
    return &d->parent;

err:
    if (d) {
        usb_eth_free(d);
    }
    return ret;
}

esp_err_t usb_eth_new(const usb_eth_config_t *config, const eth_mac_config_t *mac_config,
                      const eth_phy_config_t *phy_config, esp_eth_mac_t **mac, esp_eth_phy_t **phy)
{
    ESP_RETURN_ON_FALSE(config && mac_config && phy_config && mac && phy, ESP_ERR_INVALID_ARG, TAG,
                        "invalid argument");
    *mac = usb_eth_mac_new(config, mac_config);
    *phy = *mac ? usb_eth_phy_new(phy_config) : NULL;
    if (*phy == NULL) {
        if (*mac) {
            (*mac)->del(*mac);
            *mac = NULL;
        }
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t usb_eth_install(const usb_eth_config_t *config, esp_eth_handle_t *eth)
{
    ESP_RETURN_ON_FALSE(config && eth, ESP_ERR_INVALID_ARG, TAG, "invalid argument");
    eth_mac_config_t mac_cfg = ETH_MAC_DEFAULT_CONFIG();
    eth_phy_config_t phy_cfg = ETH_PHY_DEFAULT_CONFIG();
    esp_eth_mac_t *mac;
    esp_eth_phy_t *phy;
    ESP_RETURN_ON_ERROR(usb_eth_new(config, &mac_cfg, &phy_cfg, &mac, &phy), TAG, "MAC/PHY create failed");
    esp_eth_config_t eth_cfg = ETH_DEFAULT_CONFIG(mac, phy);
    esp_err_t ret = esp_eth_driver_install(&eth_cfg, eth);
    if (ret != ESP_OK) {
        phy->del(phy);
        mac->del(mac);
    }
    return ret;
}

esp_err_t usb_eth_uninstall(esp_eth_handle_t eth)
{
    esp_eth_mac_t *mac = NULL;
    esp_eth_phy_t *phy = NULL;
    ESP_RETURN_ON_ERROR(esp_eth_get_mac_instance(eth, &mac), TAG, "not an esp_eth handle");
    ESP_RETURN_ON_ERROR(esp_eth_get_phy_instance(eth, &phy), TAG, "not an esp_eth handle");
    ESP_RETURN_ON_ERROR(esp_eth_driver_uninstall(eth), TAG, "esp_eth_driver_uninstall failed");
    phy->del(phy);
    mac->del(mac);
    return ESP_OK;
}
