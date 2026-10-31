/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * usb_eth: USB Ethernet adapters for the ESP-IDF USB Host library, exposed as a
 * standard esp_eth MAC + PHY pair. The adapter is detected when it is plugged
 * in (and on every re-plug) and bound to the first compiled-in driver that
 * supports it; see the component README for the supported chips.
 *
 *     usb_eth_config_t cfg = USB_ETH_DEFAULT_CONFIG();
 *     esp_eth_handle_t eth;
 *     ESP_ERROR_CHECK(usb_eth_install(&cfg, &eth));      // MAC + PHY + esp_eth_driver_install
 *     ... esp_eth_new_netif_glue(eth), esp_netif_attach(), esp_eth_start(eth) ...
 *     ESP_ERROR_CHECK(usb_eth_uninstall(eth));           // after esp_eth_stop() and the glue
 *
 * The USB Host library must be installed (usb_host_install) and its event
 * loop running (usb_host_lib_handle_events) before usb_eth_install().
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_eth_mac.h"      /* before esp_eth_driver.h, see Kconfig */
#include "esp_eth_driver.h"
#include "esp_eth_phy.h"
#include "usb/usb_types_ch9.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Upper bound for rx_xfer_count / tx_xfer_count. */
#define USB_ETH_MAX_XFERS       16

typedef struct usb_eth_driver usb_eth_driver_t;    /* see private_include/usb_eth_driver.h */

/**
 * @brief Optional filter: return false to leave a supported device alone
 *        (e.g. to pin an instance to one VID:PID when several adapters are attached).
 */
typedef bool (*usb_eth_bind_filter_t)(const usb_device_desc_t *dev_desc, void *arg);

/**
 * @brief usb_eth configuration
 */
typedef struct {
    const usb_eth_driver_t *const *drivers; /*!< Drivers this instance may bind, in priority order.
                                                 NULL: every compiled-in driver (Kconfig) */
    size_t n_drivers;               /*!< Number of entries in drivers */
    usb_eth_bind_filter_t bind_filter; /*!< Optional, see usb_eth_bind_filter_t */
    void *bind_filter_arg;          /*!< Passed to bind_filter */
    uint32_t attach_timeout_ms;     /*!< How long mac->init (inside esp_eth_driver_install) waits for the
                                         first adapter. On timeout the driver continues without one and uses
                                         the ESP's own Ethernet MAC address; an adapter plugged in later is
                                         programmed with that address. */
    uint32_t rx_xfer_size;          /*!< Bulk-IN transfer size in bytes. 0: the driver's default for the chip
                                         and USB speed (menuconfig). Otherwise rounded by the driver to what
                                         the endpoint and the chip need. */
    uint8_t rx_xfer_count;          /*!< Bulk-IN transfers kept in flight (1..USB_ETH_MAX_XFERS) */
    uint8_t tx_xfer_count;          /*!< Bulk-OUT transfers (one frame each) available for transmission */
    uint32_t tx_timeout_ms;         /*!< How long transmit waits for a free bulk-OUT transfer */
    uint32_t ctrl_timeout_ms;       /*!< Timeout of a control transfer (register access) */
    bool accept_all_multicast;      /*!< Accept every multicast frame (needed for IPv6/mDNS on IDF 5.4, whose
                                         esp_eth has no multicast filter API) */
    uint32_t worker_task_stack_size;/*!< Stack of the task doing attach/detach and chip init */
    uint32_t worker_task_prio;      /*!< Priority of that task */
    TaskHandle_t usb_lib_task;      /*!< Optional: the task running usb_host_lib_handle_events(). When set,
                                         the driver warns at init if its priority is not above both driver
                                         tasks, and before closing a still-connected device it waits until
                                         that task is blocked (proving IDF finished its control-transfer
                                         bookkeeping). NULL: timing-based wait only. Must stay valid
                                         (the task must not be deleted) until mac->del(). See README. */
} usb_eth_config_t;

/**
 * @brief Default configuration (values from menuconfig, "USB Ethernet (usb_eth)")
 */
#define USB_ETH_DEFAULT_CONFIG()                                        \
    {                                                                   \
        .drivers = NULL,                                                \
        .n_drivers = 0,                                                 \
        .bind_filter = NULL,                                            \
        .bind_filter_arg = NULL,                                        \
        .attach_timeout_ms = CONFIG_USB_ETH_ATTACH_TIMEOUT_MS,          \
        .rx_xfer_size = CONFIG_USB_ETH_RX_XFER_SIZE,                    \
        .rx_xfer_count = CONFIG_USB_ETH_RX_XFER_COUNT,                  \
        .tx_xfer_count = CONFIG_USB_ETH_TX_XFER_COUNT,                  \
        .tx_timeout_ms = CONFIG_USB_ETH_TX_TIMEOUT_MS,                  \
        .ctrl_timeout_ms = CONFIG_USB_ETH_CTRL_TIMEOUT_MS,              \
        .accept_all_multicast = CONFIG_USB_ETH_ACCEPT_ALL_MULTICAST,    \
        .worker_task_stack_size = CONFIG_USB_ETH_WORKER_TASK_STACK_SIZE,\
        .worker_task_prio = CONFIG_USB_ETH_WORKER_TASK_PRIO,            \
        .usb_lib_task = NULL,                                           \
    }

/**
 * @brief Snapshot of the driver state, returned by USB_ETH_CMD_G_INFO
 */
typedef struct {
    bool attached;              /*!< An adapter is attached and initialised */
    uint8_t usb_speed;          /*!< usb_speed_t of the adapter (USB_SPEED_FULL on S3, USB_SPEED_HIGH on P4) */
    uint16_t vid;               /*!< USB vendor ID of the attached (or last attached) adapter */
    uint16_t pid;               /*!< USB product ID */
    const char *driver;         /*!< Driver bound to it ("rtl815x", ...), NULL before the first attach */
    const char *chip;           /*!< Chip name reported by the driver ("RTL8152", "RTL8153B", ...) */
    uint32_t chip_id;           /*!< Driver-defined chip identifier (rtl815x: PLA_TCR1 version bits) */
    uint16_t mps_in;            /*!< Bulk-IN max packet size */
    uint16_t mps_out;           /*!< Bulk-OUT max packet size */
    uint32_t rx_xfer_size;      /*!< Bulk-IN transfer size actually used */
    uint32_t rx_frames;         /*!< Frames passed to the stack */
    uint32_t rx_dropped;        /*!< Frames dropped (runt, no memory, desync, truncated) */
    uint32_t rx_reassembled;    /*!< Frames that spanned more than one bulk transfer */
    uint32_t tx_frames;         /*!< Frames completed on the bulk-OUT endpoint */
    uint32_t tx_errors;         /*!< Failed or dropped transmissions */
    uint32_t attach_count;      /*!< Number of successful attaches since install */
    uint32_t rx_xfers;          /*!< Bulk-IN transfers completed successfully */
} usb_eth_info_t;

/**
 * @brief usb_eth commands for esp_eth_ioctl()
 */
typedef enum {
    USB_ETH_CMD_ADD_MCAST = ETH_CMD_CUSTOM_MAC_CMDS,   /*!< Accept a multicast MAC; data: const uint8_t[6] */
    USB_ETH_CMD_DEL_MCAST,                             /*!< Stop accepting it; data: const uint8_t[6] */
    USB_ETH_CMD_S_ALL_MULTICAST,                       /*!< Accept all multicast; data: bool * */
    USB_ETH_CMD_G_INFO,                                /*!< Driver state; data: usb_eth_info_t * */
} usb_eth_cmd_t;

/**
 * @brief Create the MAC object
 * @param config     usb_eth configuration
 * @param mac_config Generic MAC configuration; rx_task_stack_size and rx_task_prio size the task
 *                   that runs USB client events and RX processing.
 * @return MAC instance, or NULL on failure
 */
esp_eth_mac_t *usb_eth_mac_new(const usb_eth_config_t *config, const eth_mac_config_t *mac_config);

/**
 * @brief Create the PHY object
 *
 * This is the generic IEEE 802.3 PHY driver with the PHY address fixed to 0 and no
 * reset GPIO. Its MII registers are the adapter's PHY (through the MAC object), or a
 * register set the core derives from the link state for adapters without an MDIO
 * PHY. While no adapter is attached the MAC answers with a link-down register set,
 * so esp_eth reports link down on unplug and link up again after the next plug-in.
 */
esp_eth_phy_t *usb_eth_phy_new(const eth_phy_config_t *config);

/**
 * @brief Create the MAC + PHY pair (usb_eth_mac_new + usb_eth_phy_new)
 * On failure nothing is left allocated and both outputs are NULL.
 */
esp_err_t usb_eth_new(const usb_eth_config_t *config, const eth_mac_config_t *mac_config,
                      const eth_phy_config_t *phy_config, esp_eth_mac_t **mac, esp_eth_phy_t **phy);

/**
 * @brief usb_eth_new() with the default MAC and PHY configuration + esp_eth_driver_install()
 */
esp_err_t usb_eth_install(const usb_eth_config_t *config, esp_eth_handle_t *eth);

/**
 * @brief esp_eth_driver_uninstall() + delete the MAC and PHY created by usb_eth_install()
 * Call after esp_eth_stop() and after deleting the netif glue.
 */
esp_err_t usb_eth_uninstall(esp_eth_handle_t eth);

#ifdef __cplusplus
}
#endif
