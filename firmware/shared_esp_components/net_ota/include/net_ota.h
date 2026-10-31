/*
 * net_ota: push firmware updates over HTTP.
 *
 *   POST /ota   body = the app .bin (Content-Length required). Checked against the
 *               running app (chip, project name) before anything is erased; on
 *               success the device switches partitions and restarts.
 *   GET  /ota   JSON with the running project, version, partition and OTA state.
 *
 * The application brings up the network and mDNS (mdns_init + hostname); net_ota
 * only needs a TCP/IP stack and adds an "_http._tcp" service if mDNS is running.
 *
 *     net_ota_config_t cfg = NET_OTA_DEFAULT_CONFIG();
 *     ESP_ERROR_CHECK(net_ota_start(&cfg));
 *     ...  once the app knows it works (e.g. got an IP):
 *     net_ota_mark_valid();
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    httpd_handle_t server;      /*!< Register on this server; NULL: start our own on `port` */
    uint16_t port;              /*!< Port of the server (ours, or `server`'s); advertised over mDNS */
    bool (*can_update)(void);   /*!< Optional: return false to refuse an update (503) */
    void (*on_begin)(void);     /*!< Optional: an accepted image starts writing */
    void (*on_end)(bool ok);    /*!< Optional: writing ended; on success the restart follows */
} net_ota_config_t;

#define NET_OTA_DEFAULT_CONFIG()            \
    {                                       \
        .server = NULL,                     \
        .port = CONFIG_NET_OTA_PORT,        \
        .can_update = NULL,                 \
        .on_begin = NULL,                   \
        .on_end = NULL,                     \
    }

/**
 * @brief Register the /ota handlers (starting a server if cfg->server is NULL),
 *        advertise them over mDNS and, if the running image is pending verification,
 *        arm the rollback guard (CONFIG_NET_OTA_CONFIRM_TIMEOUT_S).
 */
esp_err_t net_ota_start(const net_ota_config_t *cfg);

/**
 * @brief Confirm the running image: cancels the rollback guard. No-op unless the
 *        image is pending verification. Safe to call from any task, more than once.
 */
void net_ota_mark_valid(void);

/** @brief An update is being received or the restart after one is pending. */
bool net_ota_busy(void);

#ifdef __cplusplus
}
#endif
