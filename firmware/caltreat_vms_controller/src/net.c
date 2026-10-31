#include "net.h"

#include <stdio.h>
#include <string.h>
#include <sys/param.h>
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_eth.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "mdns.h"
#include "usb/usb_host.h"

#include "net_ota.h"
#include "usb_eth.h"

static const char *TAG = "net";

#define TICK_MS          1000
#define BACKOFF_MIN_US   (5 * 1000000LL)
#define BACKOFF_MAX_US   (60 * 1000000LL)
#define PROBE_TIMEOUT_MS 10000   /* covers a Cloud Run cold start */
#define PROBE_FAILS_DOWN 2       /* consecutive failures before "unreachable" */
#define FAIL_LOG_US      (60 * 1000000LL)

#define NET_GOT_IP BIT0

typedef enum { ST_USB_INIT, ST_NIC_INIT, ST_NIC_UP } sup_state_t;

static EventGroupHandle_t s_events;
static SemaphoreHandle_t s_lock;
static net_status_t s_status;
static char s_device_id[13];
static char s_hostname[32];

/* Owned by the supervisor task. */
static TaskHandle_t s_usb_lib;
static esp_eth_handle_t s_eth;
static esp_eth_netif_glue_handle_t s_glue;
static esp_netif_t *s_netif;
static volatile bool s_link;
static volatile bool s_probe_now;
static volatile esp_ip4_addr_t s_ip;

/* ------------------------------------------------------------------ */
/* Bring-up and teardown                                               */
/* ------------------------------------------------------------------ */

/* Never calls usb_host_device_free_all(): after a driver uninstall that would
 * disable the port until the dongle is replugged (see the usb_eth example). */
static void usb_lib_task(void *arg)
{
    for (;;) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
    }
}

static esp_err_t usb_start(void)
{
    const usb_host_config_t cfg = {
        .skip_phy_setup = false,
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };
    ESP_RETURN_ON_ERROR(usb_host_install(&cfg), TAG, "usb_host_install");
    /* Above the usb_eth driver tasks (RX 15, worker 5), see the usb_eth README. */
    if (xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 20, &s_usb_lib) != pdPASS) {
        usb_host_uninstall();
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/* Reverse of nic_install(); also cleans up a partial install. */
static void nic_uninstall(void)
{
    if (s_eth) {
        esp_eth_stop(s_eth);   /* ESP_ERR_INVALID_STATE if never started: fine */
    }
    if (s_glue) {
        esp_eth_del_netif_glue(s_glue);
        s_glue = NULL;
    }
    if (s_netif) {
        /* mDNS caches the netif pointer and only drops it on a link-down event;
         * make it drop it before the netif is freed (e.g. reinit after a lost lease). */
        if (mdns_netif_action(s_netif, MDNS_EVENT_DISABLE_IP4 | MDNS_EVENT_DISABLE_IP6) == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(200));   /* let the mDNS task process it */
        }
        esp_netif_destroy(s_netif);
        s_netif = NULL;
    }
    if (s_eth) {
        esp_err_t err = usb_eth_uninstall(s_eth);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "usb_eth_uninstall: %s", esp_err_to_name(err));
        }
        s_eth = NULL;
    }
    s_link = false;
    xEventGroupClearBits(s_events, NET_GOT_IP);
}

/* usb_eth MAC/PHY + esp_eth, attached to a new DHCP netif. Waits up to
 * CONFIG_USB_ETH_ATTACH_TIMEOUT_MS for an adapter, then continues without one. */
static esp_err_t nic_install(void)
{
    usb_eth_config_t cfg = USB_ETH_DEFAULT_CONFIG();
    cfg.usb_lib_task = s_usb_lib;
    esp_err_t ret = usb_eth_install(&cfg, &s_eth);
    if (ret != ESP_OK) {
        s_eth = NULL;
        return ret;
    }
    const esp_netif_config_t netif_cfg = ESP_NETIF_DEFAULT_ETH();
    s_netif = esp_netif_new(&netif_cfg);
    s_glue = s_netif ? esp_eth_new_netif_glue(s_eth) : NULL;
    if (!s_glue) {
        ret = ESP_ERR_NO_MEM;
        goto fail;
    }
    esp_netif_set_hostname(s_netif, s_hostname);   /* shown in the router's DHCP table */
    ESP_GOTO_ON_ERROR(esp_netif_attach(s_netif, s_glue), fail, TAG, "netif attach");
    ESP_GOTO_ON_ERROR(esp_eth_start(s_eth), fail, TAG, "eth start");
    return ESP_OK;
fail:
    nic_uninstall();
    return ret;
}

/* ------------------------------------------------------------------ */
/* Events                                                              */
/* ------------------------------------------------------------------ */

static void eth_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    switch (id) {
    case ETHERNET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "link up");
        s_link = true;
        break;
    case ETHERNET_EVENT_DISCONNECTED:
        ESP_LOGI(TAG, "link down");
        s_link = false;
        xEventGroupClearBits(s_events, NET_GOT_IP);
        break;
    default:
        break;
    }
}

static void ip_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == IP_EVENT_ETH_GOT_IP) {
        const ip_event_got_ip_t *ev = data;
        ESP_LOGI(TAG, "got IP " IPSTR ", gateway " IPSTR, IP2STR(&ev->ip_info.ip), IP2STR(&ev->ip_info.gw));
        s_ip = ev->ip_info.ip;
        s_probe_now = true;
        xEventGroupSetBits(s_events, NET_GOT_IP);
    } else if (id == IP_EVENT_ETH_LOST_IP) {
        ESP_LOGI(TAG, "lost IP");
        xEventGroupClearBits(s_events, NET_GOT_IP);
    }
}

static bool have_ip(void)
{
    return xEventGroupGetBits(s_events) & NET_GOT_IP;
}

/* ------------------------------------------------------------------ */
/* Backend probe                                                       */
/* ------------------------------------------------------------------ */

typedef struct {
    char buf[64];
    int len;
} body_t;

static body_t s_body;
static esp_http_client_handle_t s_http;   /* kept between probes so TLS is reused */

static esp_err_t http_event(esp_http_client_event_t *e)
{
    if (e->event_id == HTTP_EVENT_ON_DATA) {
        int n = MIN(e->data_len, (int)sizeof(s_body.buf) - 1 - s_body.len);
        memcpy(s_body.buf + s_body.len, e->data, n);
        s_body.len += n;
        s_body.buf[s_body.len] = '\0';
    }
    return ESP_OK;
}

static esp_err_t probe_once(int *status)
{
    if (!s_http) {
        const esp_http_client_config_t cfg = {
            .url = CONFIG_VMS_BACKEND_URL "/api/health/",
            .timeout_ms = PROBE_TIMEOUT_MS,
            .crt_bundle_attach = esp_crt_bundle_attach,
            .keep_alive_enable = true,
            .event_handler = http_event,
        };
        s_http = esp_http_client_init(&cfg);
        if (!s_http) {
            return ESP_ERR_NO_MEM;
        }
    }
    s_body.len = 0;
    s_body.buf[0] = '\0';
    esp_err_t err = esp_http_client_perform(s_http);
    *status = err == ESP_OK ? esp_http_client_get_status_code(s_http) : 0;
    if (err != ESP_OK) {
        esp_http_client_cleanup(s_http);
        s_http = NULL;
    }
    return err;
}

static bool probe_backend(void)
{
    int status;
    bool reused = s_http != NULL;
    esp_err_t err = probe_once(&status);
    if (err != ESP_OK && reused) {
        err = probe_once(&status);   /* the kept connection may have been closed by the server */
    }
    return err == ESP_OK && status == 200 && strstr(s_body.buf, "\"ok\"");
}

/* ------------------------------------------------------------------ */
/* Supervisor                                                          */
/* ------------------------------------------------------------------ */

static void backoff_next(int64_t *backoff)
{
    *backoff = MIN(*backoff * 2, BACKOFF_MAX_US);
}

static net_nic_state_t nic_state(sup_state_t st)
{
    if (st == ST_USB_INIT) {
        return NET_NIC_NO_USB;
    }
    usb_eth_info_t info = {0};
    if (!s_eth || esp_eth_ioctl(s_eth, USB_ETH_CMD_G_INFO, &info) != ESP_OK || !info.attached) {
        return NET_NIC_NO_ADAPTER;
    }
    if (!s_link) {
        return NET_NIC_LINK_DOWN;
    }
    return have_ip() ? NET_NIC_UP : NET_NIC_NO_IP;
}

static void supervisor_task(void *arg)
{
    sup_state_t st = ST_USB_INIT;
    int64_t retry_at = 0, backoff = BACKOFF_MIN_US, last_fail_log = -FAIL_LOG_US;
    int64_t no_ip_since = 0;
    int64_t probe_at = 0, probe_backoff = BACKOFF_MIN_US;
    int probe_fails = 0;
    net_nic_state_t last_nic = NET_NIC_NO_USB;

    for (;;) {
        int64_t now = esp_timer_get_time();

        switch (st) {
        case ST_USB_INIT:
        case ST_NIC_INIT: {
            if (now < retry_at) {
                break;
            }
            esp_err_t err = st == ST_USB_INIT ? usb_start() : nic_install();
            if (err == ESP_OK) {
                ESP_LOGI(TAG, "%s ready", st == ST_USB_INIT ? "USB host" : "Ethernet driver");
                st = st == ST_USB_INIT ? ST_NIC_INIT : ST_NIC_UP;
                retry_at = 0;
                backoff = BACKOFF_MIN_US;
                no_ip_since = now;
            } else {
                if (now - last_fail_log >= FAIL_LOG_US) {
                    ESP_LOGW(TAG, "%s failed (%s), retrying", st == ST_USB_INIT ? "USB host install" : "Ethernet install",
                             esp_err_to_name(err));
                    last_fail_log = now;
                }
                retry_at = now + backoff;
                backoff_next(&backoff);
            }
            break;
        }
        case ST_NIC_UP:
            if (have_ip()) {
                no_ip_since = now;
            } else if (now - no_ip_since >= CONFIG_VMS_NET_REINIT_S * 1000000LL && !net_ota_busy()) {
                /* Covers the cases usb_eth can't recover from by itself: an adapter whose
                 * init failed, or a lost detach behind a hub. */
                ESP_LOGW(TAG, "no address for %d s, reinstalling the Ethernet driver", CONFIG_VMS_NET_REINIT_S);
                nic_uninstall();
                xSemaphoreTake(s_lock, portMAX_DELAY);
                s_status.nic_reinits++;
                xSemaphoreGive(s_lock);
                st = ST_NIC_INIT;
                retry_at = 0;
            }
            break;
        }

        net_nic_state_t nic = nic_state(st);
        if (nic != last_nic) {
            ESP_LOGI(TAG, "nic: %s -> %s", net_nic_state_name(last_nic), net_nic_state_name(nic));
            last_nic = nic;
        }
        net_backend_state_t backend;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_status.nic = nic;
        s_status.ip = s_ip;
        if (nic != NET_NIC_UP) {
            s_status.backend = NET_BACKEND_UNKNOWN;
        }
        backend = s_status.backend;
        xSemaphoreGive(s_lock);

        if (nic != NET_NIC_UP) {
            probe_fails = 0;
            probe_backoff = BACKOFF_MIN_US;
        } else if (s_probe_now || now >= probe_at) {
            s_probe_now = false;
            bool ok = probe_backend();
            now = esp_timer_get_time();
            net_backend_state_t next = backend;
            if (ok) {
                probe_fails = 0;
                probe_backoff = BACKOFF_MIN_US;
                probe_at = now + CONFIG_VMS_BACKEND_PROBE_S * 1000000LL;
                next = NET_BACKEND_OK;
            } else {
                probe_at = now + probe_backoff;
                backoff_next(&probe_backoff);
                if (++probe_fails >= PROBE_FAILS_DOWN) {
                    next = NET_BACKEND_UNREACHABLE;
                }
            }
            if (next != backend) {
                ESP_LOGI(TAG, "backend: %s -> %s", net_backend_state_name(backend), net_backend_state_name(next));
            }
            xSemaphoreTake(s_lock, portMAX_DELAY);
            s_status.backend = next;
            if (ok) {
                s_status.backend_last_ok_us = now;
            }
            xSemaphoreGive(s_lock);
        }

        vTaskDelay(pdMS_TO_TICKS(TICK_MS));
    }
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

esp_err_t net_init(void)
{
    uint8_t mac[6];
    ESP_RETURN_ON_ERROR(esp_read_mac(mac, ESP_MAC_EFUSE_FACTORY), TAG, "factory MAC");
    snprintf(s_device_id, sizeof(s_device_id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3],
             mac[4], mac[5]);
    snprintf(s_hostname, sizeof(s_hostname), "caltreat-vms-%s", s_device_id);

    s_events = xEventGroupCreate();
    s_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_events && s_lock, ESP_ERR_NO_MEM, TAG, "sync objects");

    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "esp_netif_init");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(ETH_EVENT, ESP_EVENT_ANY_ID, eth_event, NULL), TAG, "eth events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, ip_event, NULL), TAG, "ip events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_LOST_IP, ip_event, NULL), TAG, "ip events");

    ESP_RETURN_ON_ERROR(mdns_init(), TAG, "mdns_init");
    ESP_RETURN_ON_ERROR(mdns_hostname_set(s_hostname), TAG, "mdns hostname");
    mdns_instance_name_set("CalTreat VMS");

    /* Above the sign loop (1), below the USB/Ethernet driver tasks; probes and bring-up are never urgent. */
    BaseType_t ok = xTaskCreate(supervisor_task, "net_sup", 6144, NULL, 4, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "supervisor task");
    ESP_LOGI(TAG, "hostname %s.local, backend %s", s_hostname, CONFIG_VMS_BACKEND_URL);
    return ESP_OK;
}

void net_get_status(net_status_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_status;
    xSemaphoreGive(s_lock);
}

bool net_wait_for_ip(TickType_t timeout)
{
    return xEventGroupWaitBits(s_events, NET_GOT_IP, pdFALSE, pdTRUE, timeout) & NET_GOT_IP;
}

const char *net_device_id(void)
{
    return s_device_id;
}

const char *net_hostname(void)
{
    return s_hostname;
}

const char *net_nic_state_name(net_nic_state_t s)
{
    static const char *const names[] = {"no_usb", "no_adapter", "link_down", "no_ip", "up"};
    return s < sizeof(names) / sizeof(names[0]) ? names[s] : "?";
}

const char *net_backend_state_name(net_backend_state_t s)
{
    static const char *const names[] = {"unknown", "ok", "unreachable"};
    return s < sizeof(names) / sizeof(names[0]) ? names[s] : "?";
}
