/*
 * Network: USB Ethernet adapter (usb_eth) + DHCP + mDNS, kept alive by a
 * supervisor task that retries the USB host / adapter bring-up and probes the
 * backend. Nothing here blocks the sign: net_init() returns immediately and
 * all bring-up happens in the background.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "esp_err.h"
#include "esp_netif_ip_addr.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    NET_NIC_NO_USB,       /* USB host library not installed (retrying) */
    NET_NIC_NO_ADAPTER,   /* driver not installed, or no adapter plugged in */
    NET_NIC_LINK_DOWN,    /* adapter present, no Ethernet link */
    NET_NIC_NO_IP,        /* link up, waiting for DHCP */
    NET_NIC_UP,
} net_nic_state_t;

typedef enum {
    NET_BACKEND_UNKNOWN,
    NET_BACKEND_OK,
    NET_BACKEND_UNREACHABLE,
} net_backend_state_t;

typedef struct {
    net_nic_state_t nic;
    net_backend_state_t backend;
    esp_ip4_addr_t ip;
    int64_t backend_last_ok_us;   /* esp_timer time of the last good probe, 0 = never */
    uint32_t nic_reinits;         /* driver reinstalls after a long time without an address */
} net_status_t;

/* Start mDNS and the supervisor task. Never blocks on the network. */
esp_err_t net_init(void);

void net_get_status(net_status_t *out);

/* Wait until the interface has an IPv4 address. */
bool net_wait_for_ip(TickType_t timeout);

/* Factory MAC as 12 lowercase hex digits; the sign's identity on the network. */
const char *net_device_id(void);

/* mDNS / DHCP hostname, "caltreat-vms-<device id>". */
const char *net_hostname(void);

const char *net_nic_state_name(net_nic_state_t s);
const char *net_backend_state_name(net_backend_state_t s);

#ifdef __cplusplus
}
#endif
