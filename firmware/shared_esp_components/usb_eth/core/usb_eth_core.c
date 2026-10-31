/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * usb_eth core, USB side: the host client, attach/detach, the bulk data path,
 * endpoint recovery and the worker/client tasks. Everything chip-specific is
 * behind the driver's ops (usb_eth_driver.h); usb_eth_mac.c has the esp_eth side.
 *
 * Threading model
 * ---------------
 *  - client task: runs usb_host_client_handle_events(). USB client events and
 *    all transfer completion callbacks (RX parsing, TX completion, control
 *    completion) run here. It never blocks on a transfer.
 *  - worker task: attach (open, claim, driver bind), detach (cancel, free,
 *    close) and endpoint recovery. Control transfers block on a semaphore that
 *    the client task gives, so they must never be issued from the client task.
 *  - callers of the esp_eth MAC ops (tcpip thread for TX, esp_timer task for
 *    PHY polling, application tasks for ioctl/start/stop).
 *  reg_lock (recursive) serialises register access and the device state.
 *  Lock order: reg_lock, then xfer_lock; rx_gate is never taken under reg_lock.
 */
#include <inttypes.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "esp_check.h"
#include "esp_cpu.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "usb/usb_helpers.h"
#include "usb_eth_priv.h"

static const char *TAG = "usb_eth";

#define CTRL_BUF_SIZE           (USB_SETUP_PACKET_SIZE + 64)
#define POLL_DELAY_MS           10
#define TEARDOWN_TIMEOUT_MS     2000
#define ZOMBIE_RETRY_MS         100
#define QUIT_ZOMBIE_WAIT_MS     5000
#define CLOSE_SETTLE_US         20000   /* after the last control completion, see dev_release() */
#define LIB_IDLE_WAIT_MS        500     /* max wait for the USB lib task to block, see dev_release() */

static void dev_teardown(usb_eth_dev_t *d);

static const char *chip_label(const usb_eth_dev_t *d)
{
    return d->chip_name ? d->chip_name : "adapter";
}

/* ===========================================================================
 * Control transfers (EP0)
 * ======================================================================== */

static void ctrl_xfer_cb(usb_transfer_t *transfer)
{
    usb_eth_dev_t *d = transfer->context;
    int64_t now = esp_timer_get_time();
    taskENTER_CRITICAL(&d->ctrl_mux);
    d->ctrl_done_us = now;
    taskEXIT_CRITICAL(&d->ctrl_mux);
    xSemaphoreGive(d->ctrl_done);
}

/* Submit one control transfer and wait for it. Caller holds reg_lock. A
 * transfer that times out stays owned by the USB stack (ctrl_stuck) until it
 * completes; until then every new request fails fast. */
static esp_err_t ctrl_run(usb_eth_dev_t *d, uint8_t bm_request_type, uint8_t b_request, uint16_t w_value,
                          uint16_t w_index, void *data, uint16_t len)
{
    bool in = (bm_request_type & USB_BM_REQUEST_TYPE_DIR_IN) != 0;
    if (len > CTRL_BUF_SIZE - USB_SETUP_PACKET_SIZE) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (d->ctrl_stuck) {
        /* A previous transfer timed out; it must come back before the
         * transfer object can be reused. */
        if (xSemaphoreTake(d->ctrl_done, 0) != pdTRUE) {
            return ESP_ERR_TIMEOUT;
        }
        d->ctrl_stuck = false;
    }

    usb_transfer_t *t = d->ctrl_xfer;
    usb_setup_packet_t *setup = (usb_setup_packet_t *)t->data_buffer;
    setup->bmRequestType = bm_request_type;
    setup->bRequest = b_request;
    setup->wValue = w_value;
    setup->wIndex = w_index;
    setup->wLength = len;
    if (!in && len) {
        memcpy(t->data_buffer + USB_SETUP_PACKET_SIZE, data, len);
    }
    t->num_bytes = USB_SETUP_PACKET_SIZE + len;
    t->device_handle = d->dev;
    t->bEndpointAddress = 0;
    t->callback = ctrl_xfer_cb;
    t->context = d;
    t->timeout_ms = d->ctrl_timeout_ms;

    esp_err_t ret = usb_host_transfer_submit_control(d->client, t);
    if (ret != ESP_OK) {
        return ret;
    }
    if (xSemaphoreTake(d->ctrl_done, pdMS_TO_TICKS(d->ctrl_timeout_ms)) != pdTRUE) {
        d->ctrl_stuck = true;
        ESP_LOGW(TAG, "control transfer %02x/%02x 0x%04x timed out", bm_request_type, b_request, w_value);
        return ESP_ERR_TIMEOUT;
    }
    if (t->status != USB_TRANSFER_STATUS_COMPLETED) {
        return t->status == USB_TRANSFER_STATUS_NO_DEVICE ? ESP_ERR_INVALID_STATE : ESP_FAIL;
    }
    if (in) {
        if (t->actual_num_bytes < USB_SETUP_PACKET_SIZE + len) {
            return ESP_ERR_INVALID_SIZE;
        }
        memcpy(data, t->data_buffer + USB_SETUP_PACKET_SIZE, len);
    }
    return ESP_OK;
}

esp_err_t usb_eth_ctrl(usb_eth_dev_t *d, uint8_t bm_request_type, uint8_t b_request, uint16_t w_value,
                       uint16_t w_index, void *data, uint16_t len)
{
    if (d->dev == NULL || (d->state != DEV_INIT && d->state != DEV_READY)) {
        return ESP_ERR_INVALID_STATE;
    }
    return ctrl_run(d, bm_request_type, b_request, w_value, w_index, data, len);
}

/* ===========================================================================
 * Services for drivers (usb_eth_driver.h)
 * ======================================================================== */

void *usb_eth_priv(usb_eth_dev_t *d)
{
    return d->priv;
}

usb_speed_t usb_eth_speed(const usb_eth_dev_t *d)
{
    return d->speed;
}

const usb_eth_eps_t *usb_eth_eps(const usb_eth_dev_t *d)
{
    return &d->eps;
}

void usb_eth_set_chip(usb_eth_dev_t *d, const char *name, uint32_t id)
{
    d->chip_name = name;
    d->chip_id = id;
}

void usb_eth_report_link(usb_eth_dev_t *d, const usb_eth_link_t *link)
{
    d->vphy_link = *link;
    usb_eth_mii_set_link(d->vphy, link->up, link->full_duplex, link->mbps);
}

void usb_eth_vphy_reset(usb_eth_dev_t *d)
{
    usb_eth_mii_idle(d->vphy);
    if (d->state == DEV_READY && d->drv && d->drv->mdio_read == NULL) {
        usb_eth_mii_set_link(d->vphy, d->vphy_link.up, d->vphy_link.full_duplex, d->vphy_link.mbps);
    }
}

/* ===========================================================================
 * Driver matching
 * ======================================================================== */

static bool any_interface_is(const usb_config_desc_t *cfg, uint8_t cls, uint8_t sub, uint8_t proto)
{
    for (uint8_t i = 0; i < cfg->bNumInterfaces; i++) {
        int off = 0;
        const usb_intf_desc_t *intf = usb_parse_interface_descriptor(cfg, i, 0, &off);
        if (intf && intf->bInterfaceClass == cls && intf->bInterfaceSubClass == sub &&
                intf->bInterfaceProtocol == proto) {
            return true;
        }
    }
    return false;
}

static bool id_matches(const usb_eth_id_t *id, const usb_device_desc_t *dd, const usb_config_desc_t *cfg)
{
    if (id->match == 0) {
        return false;
    }
    if ((id->match & USB_ETH_MATCH_VIDPID) && (dd->idVendor != id->vid || dd->idProduct != id->pid)) {
        return false;
    }
    if ((id->match & USB_ETH_MATCH_IFCLASS) &&
            !any_interface_is(cfg, id->if_class, id->if_subclass, id->if_protocol)) {
        return false;
    }
    return true;
}

/* First driver (in priority order) with an ID entry matching the device. */
static const usb_eth_driver_t *match_driver(usb_eth_dev_t *d, const usb_device_desc_t *dd,
                                            const usb_config_desc_t *cfg, const usb_eth_id_t **id_out)
{
    if (d->cfg.bind_filter && !d->cfg.bind_filter(dd, d->cfg.bind_filter_arg)) {
        return NULL;
    }
    for (size_t i = 0; i < d->n_drivers; i++) {
        const usb_eth_driver_t *drv = d->drivers[i];
        for (size_t j = 0; j < drv->n_ids; j++) {
            if (id_matches(&drv->ids[j], dd, cfg)) {
                *id_out = &drv->ids[j];
                return drv;
            }
        }
    }
    return NULL;
}

/* Default select_eps: the first vendor-specific interface (alternate 0) with
 * a bulk IN/OUT pair. */
static esp_err_t default_select_eps(const usb_config_desc_t *cfg, usb_eth_eps_t *eps)
{
    for (uint8_t i = 0; i < cfg->bNumInterfaces; i++) {
        int off = 0;
        const usb_intf_desc_t *intf = usb_parse_interface_descriptor(cfg, i, 0, &off);
        if (intf == NULL || intf->bInterfaceClass != USB_CLASS_VENDOR_SPEC) {
            continue;
        }
        uint8_t in = 0, out = 0;
        uint16_t mps_in = 0, mps_out = 0;
        for (int e = 0; e < intf->bNumEndpoints; e++) {
            int ep_off = off;
            const usb_ep_desc_t *ep = usb_parse_endpoint_descriptor_by_index(intf, e, cfg->wTotalLength, &ep_off);
            if (ep == NULL || USB_EP_DESC_GET_XFERTYPE(ep) != USB_TRANSFER_TYPE_BULK) {
                continue;
            }
            if (USB_EP_DESC_GET_EP_DIR(ep) && in == 0) {
                in = ep->bEndpointAddress;
                mps_in = usb_eth_ep_mps(ep->wMaxPacketSize);
            } else if (!USB_EP_DESC_GET_EP_DIR(ep) && out == 0) {
                out = ep->bEndpointAddress;
                mps_out = usb_eth_ep_mps(ep->wMaxPacketSize);
            }
        }
        if (in && out && mps_in && mps_out) {
            eps->iface = intf->bInterfaceNumber;
            eps->alt = 0;
            eps->ep_in = in;
            eps->ep_out = out;
            eps->mps_in = mps_in;
            eps->mps_out = mps_out;
            return ESP_OK;
        }
    }
    return ESP_ERR_NOT_FOUND;
}

/* ===========================================================================
 * Bulk data path
 * ======================================================================== */

static bool rx_wanted(usb_eth_dev_t *d)
{
    return atomic_load(&d->link_up) && !atomic_load(&d->admin_down);
}

/*
 * Called after link_up was cleared or admin_down set. Frames are only passed to
 * stack_input while rx_xfer_cb holds rx_gate and rx_wanted() is true, so once
 * this returns no frame can reach the stack any more, even though the bulk-IN
 * transfers are cancelled later by the worker. That makes esp_eth_stop() (which
 * reports link down) a safe point to delete the netif glue and netif.
 * Bounded: rx_xfer_cb holds the gate for one transfer's worth of frames.
 * Must not be called from the client task.
 */
void usb_eth_rx_gate_close(usb_eth_dev_t *d)
{
    xSemaphoreTake(d->rx_gate, portMAX_DELAY);
    xSemaphoreGive(d->rx_gate);
    xEventGroupSetBits(d->work_ev, WORK_RX_SYNC);
}

/* Client task: a bulk transfer completed with NO_DEVICE. Normally DEV_GONE
 * follows, but IDF drops client event messages when the client's queue is
 * full, so treat this as the unplug notification too (handle_gone ignores it
 * if the device was already torn down). */
static void note_no_device(usb_eth_dev_t *d, usb_transfer_t *t)
{
    if (d->state == DEV_READY) {
        atomic_store(&d->gone_hdl, t->device_handle);
        xEventGroupSetBits(d->work_ev, WORK_GONE);
    }
}

void usb_eth_rx_deliver(usb_eth_dev_t *d, const uint8_t *frame, size_t len)
{
    uint8_t *buf = malloc(len);
    if (buf == NULL) {
        atomic_fetch_add(&d->rx_nomem, 1);
        return;
    }
    memcpy(buf, frame, len);
    atomic_fetch_add(&d->rx_frames, 1);
    /* The stack takes ownership of buf and frees it. */
    d->eth->stack_input(d->eth, buf, (uint32_t)len);
}

/* Index of @p t in rx_xfer[] / tx_xfer[], or -1 if teardown abandoned it.
 * Caller holds xfer_lock. */
static int xfer_slot(usb_transfer_t *const *slots, const usb_transfer_t *t)
{
    for (int i = 0; i < USB_ETH_MAX_XFERS; i++) {
        if (slots[i] == t) {
            return i;
        }
    }
    return -1;
}

/* Runs in the client task. Ownership: a bulk-IN transfer belongs to its
 * rx_xfer[] slot; while rx_busy[slot] is set the USB stack holds it. If
 * teardown gave up waiting and abandoned the slot, this callback is the last
 * owner and frees the transfer. */
static void rx_xfer_cb(usb_transfer_t *t)
{
    usb_eth_dev_t *d = t->context;

    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    bool owned = xfer_slot(d->rx_xfer, t) >= 0;
    xSemaphoreGive(d->xfer_lock);
    if (!owned) {
        usb_host_transfer_free(t);
        atomic_fetch_sub(&d->n_abandoned, 1);
        return;
    }

    bool resubmit = d->state == DEV_READY && d->rx_enabled;
    switch (t->status) {
    case USB_TRANSFER_STATUS_COMPLETED:
        xSemaphoreTake(d->rx_gate, portMAX_DELAY);
        /* rx_wanted() is re-checked under the gate: see rx_gate_close().
         * Teardown resets the driver's RX state once it leaves DEV_READY. */
        resubmit = resubmit && rx_wanted(d);
        atomic_fetch_add(&d->rx_xfers, 1);
        if (resubmit) {
            atomic_store(&d->rx_good, true);
            d->drv->rx_feed(d, t->data_buffer, (size_t)t->actual_num_bytes, t->actual_num_bytes < t->num_bytes);
        } else {
            /* Dropped: these bytes are missing from the stream, so the next
             * fed transfer must not be spliced onto a partial frame. */
            d->drv->rx_discard(d, t->actual_num_bytes < t->num_bytes);
        }
        xSemaphoreGive(d->rx_gate);
        break;
    case USB_TRANSFER_STATUS_ERROR:
    case USB_TRANSFER_STATUS_TIMED_OUT:
    case USB_TRANSFER_STATUS_OVERFLOW:
    case USB_TRANSFER_STATUS_STALL:
        /* The HCD has halted and flushed the pipe; the other RX transfers come
         * back canceled. The worker clears the halt and restarts RX. */
        resubmit = false;
        if (t->status == USB_TRANSFER_STATUS_STALL) {
            atomic_store(&d->rx_was_stall, true);
        }
        atomic_store(&d->rx_err, true);
        if (d->state == DEV_READY) {
            xEventGroupSetBits(d->work_ev, WORK_RX_RECOVER);
        }
        break;
    case USB_TRANSFER_STATUS_NO_DEVICE:
        resubmit = false;
        note_no_device(d, t);
        break;
    default:    /* canceled */
        resubmit = false;
        break;
    }

    /* Decide and resubmit under xfer_lock: rx_stop() and dev_teardown() pass
     * through the lock after clearing rx_enabled / leaving DEV_READY, so no
     * resubmission can slip in after they halt and flush the endpoint. */
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    int slot = xfer_slot(d->rx_xfer, t);
    if (slot >= 0 && resubmit && d->state == DEV_READY && d->rx_enabled && rx_wanted(d)) {
        t->num_bytes = (int)d->rx_size;
        if (usb_host_transfer_submit(t) == ESP_OK) {
            xSemaphoreGive(d->xfer_lock);
            return;
        }
    }
    if (slot >= 0) {
        d->rx_busy[slot] = false;
        atomic_fetch_sub(&d->rx_inflight, 1);
    }
    xSemaphoreGive(d->xfer_lock);
    if (slot < 0) {
        usb_host_transfer_free(t);  /* abandoned while we were processing it */
        atomic_fetch_sub(&d->n_abandoned, 1);
    }
}

/* Submit every idle bulk-IN transfer. Transfers still in flight (e.g. after a
 * quick link down/up) keep resubmitting on their own. Caller holds reg_lock. */
static void rx_start(usb_eth_dev_t *d)
{
    if (d->state != DEV_READY || d->rx_rec.gave_up) {
        return;
    }
    if (!d->rx_enabled && atomic_load(&d->rx_inflight) == 0) {
        /* (Re)starting. After attach the device's stream is fresh. After a stop
         * or recovery the IN pipe was halted and its transfers cancelled (IDF
         * reports 0 bytes for them), so whether an aggregate was cut is
         * unknown: the driver's parser accepts the first transfer only if it is
         * provably a whole burst, else discards to the next burst boundary.
         * No rx_gate needed (and it must not nest under reg_lock): callbacks
         * finish with the parser before decrementing rx_inflight, so none can
         * touch it while rx_inflight is 0. */
        d->drv->rx_reset(d, d->rx_stream_fresh);
        d->rx_stream_fresh = false;
    }
    d->rx_enabled = true;
    for (int i = 0; i < d->n_rx; i++) {
        usb_transfer_t *t = d->rx_xfer[i];
        /* Same rule as the resubmit path: check and submit under xfer_lock,
         * and never touch a transfer the USB stack holds. */
        xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
        if (d->state == DEV_READY && d->rx_enabled && !d->rx_busy[i]) {
            t->num_bytes = (int)d->rx_size;
            t->device_handle = d->dev;
            t->bEndpointAddress = d->eps.ep_in;
            t->callback = rx_xfer_cb;
            t->context = d;
            t->timeout_ms = 0;
            d->rx_busy[i] = true;
            atomic_fetch_add(&d->rx_inflight, 1);
            if (usb_host_transfer_submit(t) != ESP_OK) {
                d->rx_busy[i] = false;
                atomic_fetch_sub(&d->rx_inflight, 1);
                ESP_LOGE(TAG, "RX submit failed");
            }
        }
        xSemaphoreGive(d->xfer_lock);
    }
}

static bool wait_rx_idle(usb_eth_dev_t *d, uint32_t timeout_ms)
{
    for (uint32_t waited = 0; atomic_load(&d->rx_inflight) != 0; waited += POLL_DELAY_MS) {
        if (waited >= timeout_ms) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
    }
    return true;
}

/* Cancel all bulk-IN transfers and wait until they are back. Completions run
 * in the client task, which never takes reg_lock, so callers may hold it. */
static void rx_stop(usb_eth_dev_t *d)
{
    d->rx_enabled = false;
    if (d->dev == NULL) {
        return;
    }
    /* Barrier: a completion callback that saw rx_enabled == true has finished
     * resubmitting before we halt the endpoint. */
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    xSemaphoreGive(d->xfer_lock);
    if (atomic_load(&d->rx_inflight) != 0) {
        usb_host_endpoint_halt(d->dev, d->eps.ep_in);
        usb_host_endpoint_flush(d->dev, d->eps.ep_in);
        if (!wait_rx_idle(d, TEARDOWN_TIMEOUT_MS)) {
            /* Leave the pipe halted: clearing it would restart the stuck
             * transfers. RX stays off (gave_up keeps rx_start out) until
             * teardown, which abandons them safely. */
            ESP_LOGE(TAG, "RX transfers did not complete, RX stopped until the adapter is replugged");
            d->rx_rec.gave_up = true;    /* keeps rx_start from submitting to the halted pipe */
            return;
        }
        usb_host_endpoint_clear(d->dev, d->eps.ep_in);
    }
}

/* Give a bulk-OUT transfer back to the pool (TX completion or a failed
 * transmit). If teardown abandoned it, free it instead. */
static void tx_release(usb_eth_dev_t *d, usb_transfer_t *t)
{
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    bool owned = xfer_slot(d->tx_xfer, t) >= 0;
    if (owned) {
        xQueueSend(d->tx_pool, &t, 0);
    }
    xSemaphoreGive(d->xfer_lock);
    if (!owned) {
        usb_host_transfer_free(t);
        atomic_fetch_sub(&d->n_abandoned, 1);
    }
}

/* Runs in the client task. */
static void tx_xfer_cb(usb_transfer_t *t)
{
    usb_eth_dev_t *d = t->context;
    switch (t->status) {
    case USB_TRANSFER_STATUS_COMPLETED:
        atomic_fetch_add(&d->tx_frames, 1);
        atomic_store(&d->tx_good, true);
        break;
    case USB_TRANSFER_STATUS_ERROR:
    case USB_TRANSFER_STATUS_TIMED_OUT:
    case USB_TRANSFER_STATUS_OVERFLOW:
    case USB_TRANSFER_STATUS_STALL:
        /* The HCD has halted and flushed the pipe; every later submit fails
         * until it is cleared. The worker recovers it (tx_recover). */
        atomic_fetch_add(&d->tx_errors, 1);
        if (t->status == USB_TRANSFER_STATUS_STALL) {
            atomic_store(&d->tx_was_stall, true);
        }
        atomic_store(&d->tx_err, true);
        if (d->state == DEV_READY) {
            d->tx_paused = true;
            xEventGroupSetBits(d->work_ev, WORK_TX_RECOVER);
        }
        break;
    case USB_TRANSFER_STATUS_NO_DEVICE:
        atomic_fetch_add(&d->tx_errors, 1);
        note_no_device(d, t);
        break;
    default:    /* canceled */
        atomic_fetch_add(&d->tx_errors, 1);
        break;
    }
    tx_release(d, t);
}

/* Ownership: the caller's frame buffer is only copied, never kept or freed.
 * A transfer taken from tx_pool is owned by this function until it is either
 * submitted (then tx_xfer_cb gives it back) or released on error. */
esp_err_t usb_eth_transmit_iov(usb_eth_dev_t *d, const usb_eth_iov_t *iov, size_t n)
{
    usb_transfer_t *t;
    if (d->state != DEV_READY || d->tx_paused) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xQueueReceive(d->tx_pool, &t, pdMS_TO_TICKS(d->cfg.tx_timeout_ms)) != pdTRUE) {
        atomic_fetch_add(&d->tx_errors, 1);
        return ESP_ERR_TIMEOUT;
    }
    size_t total = d->drv->tx_pack(d, t->data_buffer, t->data_buffer_size, iov, n);
    if (total == 0) {
        atomic_fetch_add(&d->tx_errors, 1);
        tx_release(d, t);
        return ESP_ERR_INVALID_SIZE;
    }
    t->num_bytes = (int)total;
    t->flags = USB_TRANSFER_FLAG_ZERO_PACK;

    /* Check and submit under xfer_lock so dev_teardown(), which passes through
     * the lock after leaving DEV_READY, never sees a submission to an endpoint
     * it already flushed or a device it already closed. */
    esp_err_t ret = ESP_ERR_INVALID_STATE;
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    if (d->state == DEV_READY && !d->tx_paused && xfer_slot(d->tx_xfer, t) >= 0) {
        ret = usb_host_transfer_submit(t);
    }
    xSemaphoreGive(d->xfer_lock);
    if (ret == ESP_OK) {
        return ESP_OK;
    }
    atomic_fetch_add(&d->tx_errors, 1);
    tx_release(d, t);
    return ret;
}

/* ===========================================================================
 * Attach / detach (worker task)
 * ======================================================================== */

/*
 * Free every bulk transfer this driver instance owns. A transfer is freed here
 * if it is idle (RX: !rx_busy, TX: sitting in tx_pool). One still held by the
 * USB stack after the teardown timeout is abandoned: its slot is cleared, and
 * rx_xfer_cb / tx_release free it when it finally completes. Freeing it here
 * would be a use-after-free in the USB stack.
 */
static void free_transfers(usb_eth_dev_t *d)
{
    usb_transfer_t *to_free[2 * USB_ETH_MAX_XFERS];
    int n_free = 0;
    int abandoned = 0;

    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    usb_transfer_t *t;
    while (xQueueReceive(d->tx_pool, &t, 0) == pdTRUE) {
        int slot = xfer_slot(d->tx_xfer, t);
        if (slot >= 0) {
            d->tx_xfer[slot] = NULL;
            to_free[n_free++] = t;
        }
    }
    for (int i = 0; i < USB_ETH_MAX_XFERS; i++) {
        if (d->tx_xfer[i]) {
            d->tx_xfer[i] = NULL;    /* still in flight or held by a transmitter */
            abandoned++;
        }
        if (d->rx_xfer[i]) {
            if (d->rx_busy[i]) {
                abandoned++;
            } else {
                to_free[n_free++] = d->rx_xfer[i];
            }
            d->rx_xfer[i] = NULL;
            d->rx_busy[i] = false;
        }
    }
    atomic_store(&d->rx_inflight, 0);
    atomic_fetch_add(&d->n_abandoned, abandoned);
    d->n_rx = 0;
    d->n_tx = 0;
    xSemaphoreGive(d->xfer_lock);

    for (int i = 0; i < n_free; i++) {
        usb_host_transfer_free(to_free[i]);
    }
    if (abandoned) {
        ESP_LOGW(TAG, "%d transfer(s) still owned by the USB stack; freed on completion", abandoned);
    }
}

/* On failure the transfers allocated so far stay in their slots and are
 * freed by dev_teardown() -> free_transfers(). */
static esp_err_t alloc_transfers(usb_eth_dev_t *d)
{
    usb_eth_sizing_t sz = {0};
    d->drv->sizing(d, &d->eps, d->speed, d->cfg.rx_xfer_size, &sz);
    ESP_RETURN_ON_FALSE(sz.rx_size && sz.tx_size && sz.rx_size % d->eps.mps_in == 0, ESP_ERR_INVALID_SIZE, TAG,
                        "%s: bad transfer sizes %u/%u", d->drv->name, (unsigned)sz.rx_size, (unsigned)sz.tx_size);
    d->rx_size = sz.rx_size;
    d->tx_size = sz.tx_size;
    for (int i = 0; i < d->cfg.rx_xfer_count; i++) {
        usb_transfer_t *t;
        ESP_RETURN_ON_ERROR(usb_host_transfer_alloc(d->rx_size, 0, &t), TAG, "RX alloc");
        xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
        d->rx_xfer[i] = t;
        d->rx_busy[i] = false;
        d->n_rx++;
        xSemaphoreGive(d->xfer_lock);
    }
    for (int i = 0; i < d->cfg.tx_xfer_count; i++) {
        usb_transfer_t *t;
        ESP_RETURN_ON_ERROR(usb_host_transfer_alloc(d->tx_size, 0, &t), TAG, "TX alloc");
        t->device_handle = d->dev;
        t->bEndpointAddress = d->eps.ep_out;
        t->callback = tx_xfer_cb;
        t->context = d;
        t->timeout_ms = 0;
        xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
        d->tx_xfer[i] = t;
        d->n_tx++;
        xQueueSend(d->tx_pool, &t, 0);
        xSemaphoreGive(d->xfer_lock);
    }
    return ESP_OK;
}

/* The driver's private state belongs to @p drv from now on. */
static esp_err_t priv_take(usb_eth_dev_t *d, const usb_eth_driver_t *drv)
{
    if (d->drv != drv) {
        if (d->drv && d->priv_ready && d->drv->priv_deinit) {
            d->drv->priv_deinit(d, d->priv);
        }
        d->drv = drv;
        d->priv_ready = false;
    }
    if (!d->priv_ready) {
        memset(d->priv, 0, d->priv_size);
        if (drv->priv_init) {
            ESP_RETURN_ON_ERROR(drv->priv_init(d, d->priv), TAG, "%s: private state init failed", drv->name);
        }
        d->priv_ready = true;
    }
    return ESP_OK;
}

/* Chip init (driver bind), transfers, MAC address policy, then bring the MAC up.
 * Caller holds reg_lock, state DEV_INIT. */
static esp_err_t dev_bring_up(usb_eth_dev_t *d)
{
    d->chip_name = NULL;
    d->chip_id = 0;
    ESP_RETURN_ON_ERROR(d->drv->bind(d, d->drv_id), TAG, "%s: bind failed", d->drv->name);
    ESP_RETURN_ON_ERROR(alloc_transfers(d), TAG, "transfer alloc failed");
    if (!d->addr_valid) {
        uint8_t mac[ETH_ADDR_LEN];
        esp_err_t err = d->drv->read_mac(d, mac);
        if (err == ESP_ERR_NOT_FOUND) {
            ESP_LOGW(TAG, "adapter has no valid MAC address, using the ESP Ethernet MAC");
            err = esp_read_mac(mac, ESP_MAC_ETH);
        }
        ESP_RETURN_ON_ERROR(err, TAG, "MAC read");
        memcpy(d->addr, mac, ETH_ADDR_LEN);
        d->addr_valid = true;
    }
    usb_eth_filter_t f = {
        .promisc = d->promisc, .all_multi = d->all_multi, .n_mcast = d->n_mcast, .mcast = d->mcast,
    };
    return d->drv->up(d, d->addr, &f);
}

static void skip_addr(usb_eth_dev_t *d, uint8_t addr)
{
    taskENTER_CRITICAL(&d->skip_mux);
    usb_eth_addr_set_add(&d->skip_addrs, addr);
    taskEXIT_CRITICAL(&d->skip_mux);
}

static bool is_skipped(usb_eth_dev_t *d, uint8_t addr)
{
    taskENTER_CRITICAL(&d->skip_mux);
    bool skip = usb_eth_addr_set_contains(&d->skip_addrs, addr);
    taskEXIT_CRITICAL(&d->skip_mux);
    return skip;
}

/* Try to bind the device at @p addr. A device that does not match or fails
 * to initialise is added to skip_addrs while we still hold it open, so its
 * address cannot be reused by a new device (whose NEW_DEV clears it) in
 * between. A failed open is not skipped (see below). */
static void handle_new_dev(usb_eth_dev_t *d, uint8_t addr)
{
    esp_err_t ret = ESP_OK;
    if (d->dev != NULL) {
        return;     /* one adapter per driver instance */
    }
    usb_device_handle_t dev;
    if (usb_host_device_open(d->client, addr, &dev) != ESP_OK) {
        /* Not skipped: we hold nothing, so the address may already belong to
         * a new device whose NEW_DEV was handled meanwhile. The device is gone
         * (or about to produce a NEW_DEV), and retrying on the next scan is cheap. */
        return;
    }
    const usb_device_desc_t *dd;
    const usb_config_desc_t *cfg;
    usb_device_info_t info;
    const usb_eth_id_t *id = NULL;
    const usb_eth_driver_t *drv = NULL;
    if (usb_host_get_device_descriptor(dev, &dd) != ESP_OK ||
            usb_host_get_active_config_descriptor(dev, &cfg) != ESP_OK ||
            (drv = match_driver(d, dd, cfg, &id)) == NULL) {
        skip_addr(d, addr);
        usb_host_device_close(d->client, dev);
        return;
    }

    ESP_GOTO_ON_FALSE(usb_host_device_info(dev, &info) == ESP_OK, ESP_FAIL, close, TAG, "device info failed");
    usb_eth_eps_t eps = {0};
    if ((drv->select_eps ? drv->select_eps(d, cfg, id, &eps) : default_select_eps(cfg, &eps)) != ESP_OK) {
        ESP_LOGE(TAG, "%04x:%04x config %d: no usable data interface for %s", dd->idVendor, dd->idProduct,
                 info.bConfigurationValue, drv->name);
        goto close;
    }
    ESP_GOTO_ON_FALSE(usb_host_interface_claim(d->client, dev, eps.iface, eps.alt) == ESP_OK, ESP_FAIL, close, TAG,
                      "interface claim failed");

    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    d->dev = dev;
    d->eps = eps;
    d->vid = dd->idVendor;
    d->pid = dd->idProduct;
    d->drv_id = id;
    d->iface_claimed = true;
    d->phy_cache_valid = 0;
    d->rx_stream_fresh = true;
    usb_eth_recovery_reset(&d->rx_rec);
    usb_eth_recovery_reset(&d->tx_rec);
    atomic_store(&d->rx_was_stall, false);
    atomic_store(&d->tx_was_stall, false);
    atomic_store(&d->rx_err, false);
    atomic_store(&d->tx_err, false);
    d->tx_paused = false;
    d->speed = info.speed;
    d->state = DEV_INIT;
    ret = priv_take(d, drv);
    if (ret == ESP_OK) {
        ret = dev_bring_up(d);
    }
    if (ret != ESP_OK) {
        xSemaphoreGiveRecursive(d->reg_lock);
        ESP_LOGE(TAG, "%04x:%04x (%s) init failed: %s; replug it to retry", d->vid, d->pid, drv->name,
                 esp_err_to_name(ret));
        skip_addr(d, addr);
        dev_teardown(d);
        return;
    }
    d->state = DEV_READY;
    d->attach_count++;
    ESP_LOGI(TAG, "%s (%04x:%04x, %s 0x%04" PRIx32 ") attached at %s speed, bulk MPS in/out %u/%u, "
             "RX transfer %u bytes, MAC %02x:%02x:%02x:%02x:%02x:%02x", chip_label(d), d->vid, d->pid, d->drv->name,
             d->chip_id, d->speed == USB_SPEED_HIGH ? "high" : "full", d->eps.mps_in, d->eps.mps_out, (unsigned)d->rx_size,
             d->addr[0], d->addr[1], d->addr[2], d->addr[3], d->addr[4], d->addr[5]);
    if (rx_wanted(d)) {
        rx_start(d);     /* link already reported up (quick replug) */
    }
    xSemaphoreGiveRecursive(d->reg_lock);
    xEventGroupSetBits(d->events, EVT_FIRST_ATTACH);
    return;

close:
    (void)ret;
    skip_addr(d, addr);
    usb_host_device_close(d->client, dev);
}

/*
 * usbh decrements its in-flight control-transfer counter in the USB library
 * task *after* dispatching our completion (usbh.c handle_ep0_dequeue), and
 * usbh_dev_close() asserts that it is zero once the last opener closes. No
 * public API exposes the counter, so:
 *  1. Wait until CLOSE_SETTLE_US have passed since the last control completion
 *     (measured in microseconds, then rounded up to whole ticks plus one).
 *     With the USB library task above this driver's tasks (see README) the
 *     decrement runs within microseconds of the callback.
 *  2. If the application gave us that task's handle, also wait (bounded) until
 *     it is Blocked or Suspended. Between dispatching our callback and the
 *     decrement it never blocks (only critical sections), so seeing it blocked
 *     after our callback ran proves the decrement is done.
 */
static void wait_ctrl_bookkeeping(usb_eth_dev_t *d)
{
    taskENTER_CRITICAL(&d->ctrl_mux);
    int64_t done = d->ctrl_done_us;
    taskEXIT_CRITICAL(&d->ctrl_mux);
    int64_t remaining = CLOSE_SETTLE_US - (esp_timer_get_time() - done);
    if (remaining > 0) {
        int64_t tick_us = 1000LL * portTICK_PERIOD_MS;
        vTaskDelay((TickType_t)((remaining + tick_us - 1) / tick_us) + 1);
    }
    TaskHandle_t lib = d->cfg.usb_lib_task;
    if (lib == NULL) {
        return;
    }
    for (int waited = 0;; waited += portTICK_PERIOD_MS) {
        eTaskState st = eTaskGetState(lib);
        if (st == eBlocked || st == eSuspended || st == eDeleted || st == eInvalid) {
            return;
        }
        if (waited >= LIB_IDLE_WAIT_MS) {
            ESP_LOGW(TAG, "USB lib task still busy after %d ms, closing the device anyway", LIB_IDLE_WAIT_MS);
            return;
        }
        vTaskDelay(1);
    }
}

/*
 * Release the interface and close the device. IDF refuses (or asserts, for
 * close with a control transfer in flight) while the USB stack still holds
 * anything of ours, so this only proceeds once every transfer is back:
 *  - a timed-out control transfer has completed (ctrl_stuck cleared);
 *  - abandoned bulk transfers have completed and been freed;
 *  - usb_host_interface_release() no longer reports in-flight URBs (it lags
 *    our callbacks slightly, so INVALID_STATE is retried for a few ticks).
 * Returns false if the device must stay open ("zombie"); the worker retries.
 * Caller holds reg_lock.
 */
static bool dev_release(usb_eth_dev_t *d)
{
    if (d->dev == NULL) {
        return true;
    }
    if (d->ctrl_stuck) {
        if (xSemaphoreTake(d->ctrl_done, 0) != pdTRUE) {
            return false;
        }
        d->ctrl_stuck = false;
    }
    if (atomic_load(&d->n_abandoned) > 0) {
        return false;
    }
    if (d->iface_claimed) {
        esp_err_t err = ESP_ERR_INVALID_STATE;
        for (int i = 0; i < 5 && err == ESP_ERR_INVALID_STATE; i++) {
            err = usb_host_interface_release(d->client, d->dev, d->eps.iface);
            if (err == ESP_ERR_INVALID_STATE) {
                vTaskDelay(1);
            }
        }
        if (err != ESP_OK) {
            return false;
        }
        d->iface_claimed = false;
    }
    wait_ctrl_bookkeeping(d);
    if (usb_host_device_close(d->client, d->dev) != ESP_OK) {
        return false;
    }
    /* A DEV_GONE for this handle may already be queued for the client task,
     * and the handle can be reused by the next device. Have the client task
     * drain its events before the worker binds anything new (see client_task). */
    d->closed_hdl = d->dev;
    atomic_store(&d->drain_pending, true);
    usb_host_client_unblock(d->client);
    d->dev = NULL;
    d->zombie = false;
    return true;
}

static void dev_teardown(usb_eth_dev_t *d)
{
    if (d->dev == NULL || d->zombie) {
        return;
    }
    d->state = DEV_CLOSING;
    /* Barrier: transmitters and RX callbacks that checked the state before
     * this point have finished submitting (they submit under xfer_lock). */
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    xSemaphoreGive(d->xfer_lock);
    rx_stop(d);

    /* Get every TX transfer back (in flight ones are canceled). */
    if (uxQueueMessagesWaiting(d->tx_pool) != d->n_tx) {
        usb_host_endpoint_halt(d->dev, d->eps.ep_out);
        usb_host_endpoint_flush(d->dev, d->eps.ep_out);
        for (int waited = 0; uxQueueMessagesWaiting(d->tx_pool) != d->n_tx; waited += POLL_DELAY_MS) {
            if (waited >= TEARDOWN_TIMEOUT_MS) {
                ESP_LOGE(TAG, "TX transfers did not complete");
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
        }
    }

    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    if (d->ctrl_stuck && xSemaphoreTake(d->ctrl_done, pdMS_TO_TICKS(TEARDOWN_TIMEOUT_MS)) == pdTRUE) {
        d->ctrl_stuck = false;
    }
    free_transfers(d);
    d->state = DEV_ABSENT;
    if (d->priv_ready) {
        d->drv->rx_reset(d, true);
    }
    usb_eth_mii_set_link(d->vphy, false, false, 0);
    if (dev_release(d)) {
        ESP_LOGI(TAG, "%s detached", chip_label(d));
    } else {
        d->zombie = true;
        ESP_LOGW(TAG, "USB stack still holds transfers of the old device; closing it once they return");
    }
    xSemaphoreGiveRecursive(d->reg_lock);
}

/* Worker: retry a deferred release. Returns true when the device is closed. */
static bool zombie_retry(usb_eth_dev_t *d)
{
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    bool done = dev_release(d);
    xSemaphoreGiveRecursive(d->reg_lock);
    if (done) {
        ESP_LOGI(TAG, "%s detached (deferred)", chip_label(d));
    }
    return done;
}

/* CLEAR_FEATURE(ENDPOINT_HALT) for @p ep: clears a halt and resets the
 * device's data toggle to DATA0. Caller holds reg_lock. */
static esp_err_t clear_device_halt(usb_eth_dev_t *d, uint8_t ep)
{
    return ctrl_run(d, USB_BM_REQUEST_TYPE_DIR_OUT | USB_BM_REQUEST_TYPE_TYPE_STANDARD |
                    USB_BM_REQUEST_TYPE_RECIP_ENDPOINT, USB_B_REQUEST_CLEAR_FEATURE, 0 /* ENDPOINT_HALT */, ep,
                    NULL, 0);
}

/* Account for a recovery attempt on one endpoint. Caller is the worker. */
static bool recovery_allowed(usb_eth_recovery_t *rec, atomic_bool *good)
{
    if (atomic_exchange(good, false)) {
        usb_eth_recovery_success(rec);
    }
    return usb_eth_recovery_attempt(rec);
}

/* Halt and flush both bulk pipes and wait until every transfer is back.
 * Caller holds reg_lock and has set tx_paused / cleared rx_enabled. */
static bool quiesce_bulk(usb_eth_dev_t *d)
{
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);     /* barrier, see rx_stop() */
    xSemaphoreGive(d->xfer_lock);
    usb_host_endpoint_halt(d->dev, d->eps.ep_in);     /* idempotent */
    usb_host_endpoint_flush(d->dev, d->eps.ep_in);
    usb_host_endpoint_halt(d->dev, d->eps.ep_out);
    usb_host_endpoint_flush(d->dev, d->eps.ep_out);
    if (!wait_rx_idle(d, TEARDOWN_TIMEOUT_MS)) {
        return false;
    }
    for (int waited = 0; uxQueueMessagesWaiting(d->tx_pool) != d->n_tx; waited += POLL_DELAY_MS) {
        if (waited >= TEARDOWN_TIMEOUT_MS) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
    }
    return true;
}

/*
 * A bulk endpoint STALLed. Clearing the halt on the host pipe is not enough:
 * CLEAR_FEATURE(ENDPOINT_HALT) resets the device's data toggle to DATA0
 * (USB 2.0 9.4.5), but IDF 5.4 never resets the host toggle of a bulk pipe;
 * DATA0 is only loaded when the pipe's channel is allocated, which happens
 * when the interface is claimed (usb_dwc_hal_chan_alloc -> hctsiz_init). So
 * re-sync both directions:
 *   quiesce both pipes -> release + re-claim the interface (fresh host pipes
 *   at DATA0) -> CLEAR_FEATURE(ENDPOINT_HALT) on both endpoints (device
 *   toggles at DATA0, whether or not the endpoint was halted) -> restart.
 * Transfers stay allocated throughout; they only name the device and endpoint
 * address. If the re-claim or either CLEAR_FEATURE fails, the device is torn
 * down; the drain/scan that follows binds it again from scratch (it is not
 * skipped), which puts both sides in a known state.
 * Caller holds reg_lock, state is DEV_READY.
 */
static void stall_recover(usb_eth_dev_t *d)
{
    bool in_stall = atomic_exchange(&d->rx_was_stall, false);
    bool out_stall = atomic_exchange(&d->tx_was_stall, false);
    /* This recovers both directions, including any non-STALL error pending. */
    atomic_store(&d->rx_err, false);
    atomic_store(&d->tx_err, false);

    bool rx_ok = !in_stall || recovery_allowed(&d->rx_rec, &d->rx_good);
    bool tx_ok = !out_stall || recovery_allowed(&d->tx_rec, &d->tx_good);
    if (!rx_ok) {
        ESP_LOGE(TAG, "bulk-IN keeps stalling, RX stopped until the adapter is replugged");
    }
    if (!tx_ok) {
        ESP_LOGE(TAG, "bulk-OUT keeps stalling, TX stopped until the adapter is replugged");
    }
    ESP_LOGW(TAG, "bulk-%s stalled, re-synchronising both pipes", in_stall ? (out_stall ? "IN/OUT" : "IN") : "OUT");

    d->tx_paused = true;
    d->rx_enabled = false;
    if (!quiesce_bulk(d)) {
        ESP_LOGE(TAG, "transfers did not return, RX/TX stopped until the adapter is replugged");
        d->rx_rec.gave_up = true;
        return;     /* tx_paused stays set */
    }

    esp_err_t err = ESP_ERR_INVALID_STATE;
    for (int i = 0; i < 5 && err == ESP_ERR_INVALID_STATE; i++) {
        err = usb_host_interface_release(d->client, d->dev, d->eps.iface);
        if (err == ESP_ERR_INVALID_STATE) {
            vTaskDelay(1);  /* IDF's in-flight count lags our callbacks */
        }
    }
    if (err == ESP_OK) {
        d->iface_claimed = false;
        err = usb_host_interface_claim(d->client, d->dev, d->eps.iface, d->eps.alt);
        d->iface_claimed = err == ESP_OK;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "interface re-claim failed (%s), re-binding the adapter", esp_err_to_name(err));
        dev_teardown(d);
        return;
    }
    /* Both host pipes are now at DATA0. Restarting without both device-side
     * clears would leave a halted endpoint or a mismatched toggle (and burn the
     * recovery limit), so if either fails, re-bind the adapter instead. */
    esp_err_t in_err = clear_device_halt(d, d->eps.ep_in);
    esp_err_t out_err = in_err == ESP_OK ? clear_device_halt(d, d->eps.ep_out) : in_err;
    if (out_err != ESP_OK) {
        ESP_LOGE(TAG, "CLEAR_FEATURE(ENDPOINT_HALT) failed (%s), re-binding the adapter", esp_err_to_name(out_err));
        dev_teardown(d);
        return;
    }

    d->tx_paused = d->tx_rec.gave_up;
    if (rx_wanted(d)) {
        rx_start(d);     /* refuses if RX gave up; discards to a burst boundary */
    }
}

/*
 * Bulk-IN failed: the HCD halted and flushed the pipe. After a STALL see
 * stall_recover(). Otherwise (transaction error, overflow) the device side is
 * not halted and its toggle advanced with the last ACK, so wait for every RX
 * transfer to come back and just clear the host pipe. Gives up after
 * MAX_RECOVERIES (usb_eth_mac.c) attempts without a good transfer in between.
 */
static void rx_recover(usb_eth_dev_t *d)
{
    if (d->dev == NULL || d->state != DEV_READY || !atomic_load(&d->rx_err)) {
        return;     /* nothing pending (already handled by stall_recover) */
    }
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    if (atomic_load(&d->rx_was_stall) || atomic_load(&d->tx_was_stall)) {
        stall_recover(d);
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    atomic_store(&d->rx_err, false);
    if (!recovery_allowed(&d->rx_rec, &d->rx_good)) {
        ESP_LOGE(TAG, "bulk-IN keeps failing, RX stopped until the adapter is replugged");
        d->rx_enabled = false;
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    ESP_LOGW(TAG, "bulk-IN transfer error, recovering");
    d->rx_enabled = false;
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);     /* barrier, see rx_stop() */
    xSemaphoreGive(d->xfer_lock);
    usb_host_endpoint_halt(d->dev, d->eps.ep_in);     /* idempotent */
    usb_host_endpoint_flush(d->dev, d->eps.ep_in);
    if (!wait_rx_idle(d, TEARDOWN_TIMEOUT_MS)) {
        ESP_LOGE(TAG, "RX transfers did not return, RX stopped until the adapter is replugged");
        d->rx_rec.gave_up = true;
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    /* Fails when the pipe is invalid, i.e. the adapter was unplugged (DEV_GONE
     * follows). Re-bind instead of submitting into a dead pipe; the scan finds
     * nothing if the adapter is really gone. */
    esp_err_t err = usb_host_endpoint_clear(d->dev, d->eps.ep_in);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "bulk-IN pipe clear failed (%s), unplugged? re-binding", esp_err_to_name(err));
        dev_teardown(d);
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    if (rx_wanted(d)) {
        rx_start(d);
    }
    xSemaphoreGiveRecursive(d->reg_lock);
}

/*
 * Bulk-OUT failed: same as rx_recover for the OUT pipe. transmit() refuses
 * while tx_paused is set; it is cleared once the pipe is active again. If the
 * transfers do not come back or the limit is hit, TX stays paused until the
 * adapter is replugged (the transfers are freed by teardown as usual).
 */
static void tx_recover(usb_eth_dev_t *d)
{
    if (d->dev == NULL || d->state != DEV_READY || !atomic_load(&d->tx_err)) {
        return;     /* nothing pending (already handled by stall_recover) */
    }
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    if (atomic_load(&d->rx_was_stall) || atomic_load(&d->tx_was_stall)) {
        stall_recover(d);
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    atomic_store(&d->tx_err, false);
    if (!recovery_allowed(&d->tx_rec, &d->tx_good)) {
        ESP_LOGE(TAG, "bulk-OUT keeps failing, TX stopped until the adapter is replugged");
        d->tx_paused = true;
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    ESP_LOGW(TAG, "bulk-OUT transfer error, recovering");
    d->tx_paused = true;
    /* Barrier: transmitters that saw tx_paused == false have submitted. */
    xSemaphoreTake(d->xfer_lock, portMAX_DELAY);
    xSemaphoreGive(d->xfer_lock);
    usb_host_endpoint_halt(d->dev, d->eps.ep_out);    /* idempotent */
    usb_host_endpoint_flush(d->dev, d->eps.ep_out);
    for (int waited = 0; uxQueueMessagesWaiting(d->tx_pool) != d->n_tx; waited += POLL_DELAY_MS) {
        if (waited >= TEARDOWN_TIMEOUT_MS) {
            ESP_LOGE(TAG, "TX transfers did not return, TX stopped until the adapter is replugged");
            xSemaphoreGiveRecursive(d->reg_lock);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
    }
    esp_err_t err = usb_host_endpoint_clear(d->dev, d->eps.ep_out);
    if (err != ESP_OK) {    /* as in rx_recover */
        ESP_LOGW(TAG, "bulk-OUT pipe clear failed (%s), unplugged? re-binding", esp_err_to_name(err));
        dev_teardown(d);
        xSemaphoreGiveRecursive(d->reg_lock);
        return;
    }
    d->tx_paused = false;
    xSemaphoreGiveRecursive(d->reg_lock);
}

/* Worker: make the bulk-IN state follow rx_wanted(). */
static void rx_sync(usb_eth_dev_t *d)
{
    xSemaphoreTakeRecursive(d->reg_lock, portMAX_DELAY);
    if (d->state == DEV_READY) {
        if (rx_wanted(d)) {
            rx_start(d);     /* no-op if already running */
        } else {
            rx_stop(d);
        }
    }
    xSemaphoreGiveRecursive(d->reg_lock);
}

/* ===========================================================================
 * Tasks
 * ======================================================================== */

static void client_event_cb(const usb_host_client_event_msg_t *msg, void *arg)
{
    usb_eth_dev_t *d = arg;
    if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
        /* A new device, possibly at an address we skipped before. */
        taskENTER_CRITICAL(&d->skip_mux);
        usb_eth_addr_set_remove(&d->skip_addrs, msg->new_dev.address);
        taskEXIT_CRITICAL(&d->skip_mux);
        xEventGroupSetBits(d->work_ev, WORK_SCAN);
    } else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
        /* We only ever have one device open, so one slot is enough. */
        atomic_store(&d->gone_hdl, msg->dev_gone.dev_hdl);
        xEventGroupSetBits(d->work_ev, WORK_GONE);
    }
}

static void client_task(void *arg)
{
    usb_eth_dev_t *d = arg;
    while (!d->client_stop) {
        usb_host_client_handle_events(d->client, portMAX_DELAY);
        if (atomic_load(&d->drain_pending)) {
            /* The worker closed closed_hdl. Any DEV_GONE for it was queued
             * before the close (IDF only notifies clients that have the device
             * open), so one more non-blocking pass delivers it. Then discard
             * it: the handle may belong to the next device. */
            usb_host_client_handle_events(d->client, 0);
            usb_device_handle_t closed = d->closed_hdl;
            atomic_compare_exchange_strong(&d->gone_hdl, &closed, NULL);
            atomic_store(&d->drain_pending, false);
            xEventGroupSetBits(d->work_ev, WORK_SCAN);
        }
    }
    xEventGroupSetBits(d->events, EVT_CLIENT_EXITED);
    vTaskDelete(NULL);
}

/* Look for a supported adapter among the attached devices. Covers devices enumerated
 * before this client registered and NEW_DEV events for any device. */
static void scan_devices(usb_eth_dev_t *d)
{
    uint8_t addrs[16];
    int n = 0;
    if (usb_host_device_addr_list_fill((int)sizeof(addrs), addrs, &n) == ESP_OK) {
        for (int i = 0; i < n && d->dev == NULL; i++) {
            if (!is_skipped(d, addrs[i])) {
                handle_new_dev(d, addrs[i]);
            }
        }
    }
}

static void handle_gone(usb_eth_dev_t *d)
{
    usb_device_handle_t gone = atomic_exchange(&d->gone_hdl, NULL);
    if (gone == NULL || gone != d->dev) {
        return;     /* stale: a device we already closed */
    }
    if (d->zombie) {
        zombie_retry(d);
    } else {
        dev_teardown(d);
    }
}

static void worker_task(void *arg)
{
    usb_eth_dev_t *d = arg;

    for (;;) {
        TickType_t wait = d->zombie ? pdMS_TO_TICKS(ZOMBIE_RETRY_MS) : portMAX_DELAY;
        EventBits_t bits = xEventGroupWaitBits(d->work_ev, WORK_ALL, pdTRUE, pdFALSE, wait);

        if (bits & WORK_QUIT) {
            dev_teardown(d);
            /* Keep the client task alive (it delivers the completions we are
             * waiting for) until the device can be closed. */
            for (int waited = 0; d->zombie && waited < QUIT_ZOMBIE_WAIT_MS; waited += ZOMBIE_RETRY_MS) {
                vTaskDelay(pdMS_TO_TICKS(ZOMBIE_RETRY_MS));
                zombie_retry(d);
            }
            if (d->zombie) {
                ESP_LOGE(TAG, "USB stack never returned our transfers; device handle and client leak");
            }
            xEventGroupSetBits(d->events, EVT_WORKER_EXITED);
            vTaskDelete(NULL);
            return;
        }
        if (bits & WORK_GONE) {
            handle_gone(d);
        }
        if (d->zombie) {
            zombie_retry(d);     /* success starts a drain, which requests a scan */
        }
        if (bits & WORK_RX_RECOVER) {
            rx_recover(d);
        }
        if (bits & WORK_RX_SYNC) {
            rx_sync(d);
        }
        if (bits & WORK_TX_RECOVER) {
            tx_recover(d);
        }
        /* While a drain is pending the client task requests the scan itself. */
        if ((bits & WORK_SCAN) && d->dev == NULL && !atomic_load(&d->drain_pending)) {
            scan_devices(d);
        }
    }
}

/* ===========================================================================
 * Start / shutdown (mac->init, mac->deinit, mac->del)
 * ======================================================================== */

/*
 * Undo everything usb_eth_core_start() created, in reverse order: the worker
 * closes the adapter (transfers, interface, device handle), then the client
 * task exits, then the control transfer and the USB host client are freed.
 * Idempotent and safe on a partially initialised instance. Does not use the
 * mediator, because esp_eth_driver_install() frees it without calling
 * mac->deinit() when phy->init() fails; mac->del() then lands here.
 */
void usb_eth_usb_shutdown(usb_eth_dev_t *d)
{
    if (d->worker_task) {
        xEventGroupSetBits(d->work_ev, WORK_QUIT);
        xEventGroupWaitBits(d->events, EVT_WORKER_EXITED, pdFALSE, pdFALSE, portMAX_DELAY);
        d->worker_task = NULL;
    }
    if (d->client_task) {
        d->client_stop = true;
        usb_host_client_unblock(d->client);
        xEventGroupWaitBits(d->events, EVT_CLIENT_EXITED, pdFALSE, pdFALSE, portMAX_DELAY);
        d->client_task = NULL;
    }
    if (d->ctrl_xfer) {
        /* The client task, which runs ctrl_xfer_cb, has exited: a transfer that
         * has not completed by now never will, so do not wait. */
        if (d->ctrl_stuck && xSemaphoreTake(d->ctrl_done, 0) != pdTRUE) {
            /* Still owned by the USB stack: freeing it would be a use-after-free. */
            ESP_LOGE(TAG, "control transfer never completed, leaking it");
        } else {
            usb_host_transfer_free(d->ctrl_xfer);
        }
        d->ctrl_stuck = false;
        d->ctrl_xfer = NULL;
    }
    if (d->client) {
        /* Fails only if the worker could not close the device (the USB stack
         * never returned a transfer, logged above). Nothing can be freed safely
         * then; the client and device stay allocated in the USB library. */
        esp_err_t err = usb_host_client_deregister(d->client);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "USB client deregister failed: %s (leaked)", esp_err_to_name(err));
        }
        d->client = NULL;
    }
    d->dev = NULL;
    d->zombie = false;
    d->iface_claimed = false;
    atomic_store(&d->n_abandoned, 0);
    atomic_store(&d->rx_was_stall, false);
    atomic_store(&d->tx_was_stall, false);
    xEventGroupClearBits(d->work_ev, WORK_ALL);
    atomic_store(&d->gone_hdl, NULL);
    atomic_store(&d->drain_pending, false);
    atomic_store(&d->link_up, false);    /* esp_eth reports it again after the next install */
    xEventGroupClearBits(d->events, EVT_FIRST_ATTACH | EVT_WORKER_EXITED | EVT_CLIENT_EXITED);
    d->initialized = false;
}

/* Register the USB host client and start the client and worker tasks. On
 * failure everything is undone (usb_eth_usb_shutdown). */
esp_err_t usb_eth_core_start(usb_eth_dev_t *d)
{
    esp_err_t ret = ESP_OK;
    const usb_host_client_config_t client_cfg = {
        .is_synchronous = false,
        .max_num_event_msg = 16,   /* IDF drops event messages when this queue is full */
        .async = {
            .client_event_callback = client_event_cb,
            .callback_arg = d,
        },
    };
    if (d->cfg.usb_lib_task) {
        UBaseType_t lib_prio = uxTaskPriorityGet(d->cfg.usb_lib_task);
        if (lib_prio <= d->mac_cfg.rx_task_prio || lib_prio <= d->cfg.worker_task_prio) {
            ESP_LOGW(TAG, "USB lib task priority %u is not above the driver tasks (rx %u, worker %u); "
                     "closing a connected adapter may wait up to %d ms (see README, Task priorities)",
                     (unsigned)lib_prio, (unsigned)d->mac_cfg.rx_task_prio,
                     (unsigned)d->cfg.worker_task_prio, LIB_IDLE_WAIT_MS);
        }
    }
    ESP_GOTO_ON_ERROR(usb_host_client_register(&client_cfg, &d->client), err, TAG,
                      "USB host client register failed (is usb_host_install() done?)");
    ESP_GOTO_ON_ERROR(usb_host_transfer_alloc(CTRL_BUF_SIZE, 0, &d->ctrl_xfer), err, TAG, "ctrl transfer alloc");

    xEventGroupClearBits(d->events, EVT_FIRST_ATTACH | EVT_WORKER_EXITED | EVT_CLIENT_EXITED);
    usb_eth_addr_set_clear_all(&d->skip_addrs);
    xEventGroupSetBits(d->work_ev, WORK_SCAN);   /* devices attached before we registered */
    d->client_stop = false;
    BaseType_t core = tskNO_AFFINITY;
    if (d->mac_cfg.flags & ETH_MAC_FLAG_PIN_TO_CORE) {
        core = (BaseType_t)esp_cpu_get_core_id();
    }
    ESP_GOTO_ON_FALSE(xTaskCreatePinnedToCore(client_task, "usb_eth_usb", d->mac_cfg.rx_task_stack_size, d,
                                              d->mac_cfg.rx_task_prio, &d->client_task, core) == pdPASS,
                      ESP_ERR_NO_MEM, err, TAG, "client task create failed");
    ESP_GOTO_ON_FALSE(xTaskCreatePinnedToCore(worker_task, "usb_eth_wrk", d->cfg.worker_task_stack_size, d,
                                              d->cfg.worker_task_prio, &d->worker_task, core) == pdPASS,
                      ESP_ERR_NO_MEM, err, TAG, "worker task create failed");
    d->initialized = true;
    return ESP_OK;

err:
    /* xTaskCreate leaves the handle untouched on failure, so only tasks that
     * really exist are stopped. */
    usb_eth_usb_shutdown(d);
    return ret;
}
