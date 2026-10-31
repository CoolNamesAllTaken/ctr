/*
 * net_ota: push updates over HTTP into the next OTA slot (esp_ota_ops), with a
 * rollback guard for the first boot of a new image. See include/net_ota.h.
 */
#include "net_ota.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/param.h>
#include "esp_app_desc.h"
#include "esp_app_format.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "mdns.h"
#include "sdkconfig.h"

#if !CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
#warning "net_ota: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is off, so a bad update cannot roll back"
#endif

static const char *TAG = "net_ota";

/* Enough of the image to identify it: image header, first segment header, app description. */
#define IMAGE_ID_LEN   (sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t) + sizeof(esp_app_desc_t))
#define RECV_RETRIES   3
#define RESTART_DELAY_US (500 * 1000)

static net_ota_config_t s_cfg;
static atomic_bool s_busy;
static esp_timer_handle_t s_guard;

static const char *state_name(esp_ota_img_states_t st)
{
    switch (st) {
    case ESP_OTA_IMG_NEW:            return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "pending_verify";
    case ESP_OTA_IMG_VALID:          return "valid";
    case ESP_OTA_IMG_INVALID:        return "invalid";
    case ESP_OTA_IMG_ABORTED:        return "aborted";
    default:                         return "undefined";
    }
}

static bool running_state(esp_ota_img_states_t *st)
{
    return esp_ota_get_state_partition(esp_ota_get_running_partition(), st) == ESP_OK;
}

static esp_err_t reply(httpd_req_t *req, const char *status, const char *msg)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, msg);
}

/* Read exactly len bytes of the body. Returns false if the client went away. */
static bool recv_full(httpd_req_t *req, char *buf, size_t len)
{
    size_t got = 0;
    int timeouts = 0;
    while (got < len) {
        int n = httpd_req_recv(req, buf + got, len - got);
        if (n == HTTPD_SOCK_ERR_TIMEOUT && ++timeouts <= RECV_RETRIES) {
            continue;
        }
        if (n <= 0) {
            return false;
        }
        got += n;
        timeouts = 0;
    }
    return true;
}

/* Reject images for another chip or another project before anything is erased. */
static bool check_image(const char *buf, char *why, size_t why_len)
{
    esp_image_header_t ih;
    esp_app_desc_t desc;
    memcpy(&ih, buf, sizeof(ih));
    memcpy(&desc, buf + sizeof(ih) + sizeof(esp_image_segment_header_t), sizeof(desc));
    const esp_app_desc_t *self = esp_app_get_description();

    if (ih.magic != ESP_IMAGE_HEADER_MAGIC) {
        snprintf(why, why_len, "not an ESP app image\n");
    } else if (ih.chip_id != CONFIG_IDF_FIRMWARE_CHIP_ID) {
        snprintf(why, why_len, "image is for chip id %d, this device is %d\n", ih.chip_id,
                 CONFIG_IDF_FIRMWARE_CHIP_ID);
    } else if (desc.magic_word != ESP_APP_DESC_MAGIC_WORD) {
        snprintf(why, why_len, "image has no app description\n");
    } else if (strncmp(desc.project_name, self->project_name, sizeof(desc.project_name)) != 0) {
        snprintf(why, why_len, "image is project '%.32s', this device runs '%s'\n", desc.project_name,
                 self->project_name);
    } else {
        ESP_LOGI(TAG, "incoming image: %.32s %.32s (running %s)", desc.project_name, desc.version,
                 self->version);
        return true;
    }
    return false;
}

static void restart_cb(void *arg)
{
    esp_restart();
}

static void restart_soon(void)
{
    const esp_timer_create_args_t args = {.callback = restart_cb, .name = "ota_restart"};
    esp_timer_handle_t t;
    if (esp_timer_create(&args, &t) != ESP_OK || esp_timer_start_once(t, RESTART_DELAY_US) != ESP_OK) {
        esp_restart();
    }
}

static esp_err_t post_ota(httpd_req_t *req)
{
    if (atomic_exchange(&s_busy, true)) {
        return reply(req, "503 Service Unavailable", "update already in progress\n");
    }
    if (s_cfg.can_update && !s_cfg.can_update()) {
        atomic_store(&s_busy, false);
        return reply(req, "503 Service Unavailable", "device busy, try again\n");
    }

    const char *status = NULL;
    char msg[128];
    char *buf = NULL;
    bool begun = false;
    esp_ota_handle_t h = 0;
    esp_err_t err;
    size_t chunk, done = 0, next_log = 1024 * 1024;
    const size_t total = req->content_len;
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);

    if (!part) {
        status = "500 Internal Server Error";
        snprintf(msg, sizeof(msg), "no OTA partition (check the partition table)\n");
        goto fail;
    }
    if (total == 0) {
        status = "411 Length Required";
        snprintf(msg, sizeof(msg), "Content-Length required\n");
        goto fail;
    }
    if (total < IMAGE_ID_LEN) {
        status = "400 Bad Request";
        snprintf(msg, sizeof(msg), "image too small (%u bytes)\n", (unsigned)total);
        goto fail;
    }
    if (total > part->size) {
        status = "413 Content Too Large";
        snprintf(msg, sizeof(msg), "image is %u bytes, partition %s holds %u\n", (unsigned)total,
                 part->label, (unsigned)part->size);
        goto fail;
    }
    buf = malloc(CONFIG_NET_OTA_BUF_SIZE);
    if (!buf) {
        status = "500 Internal Server Error";
        snprintf(msg, sizeof(msg), "out of memory\n");
        goto fail;
    }

    chunk = MIN(total, CONFIG_NET_OTA_BUF_SIZE);
    if (!recv_full(req, buf, chunk)) {
        status = "400 Bad Request";
        snprintf(msg, sizeof(msg), "receive failed\n");
        goto fail;
    }
    if (!check_image(buf, msg, sizeof(msg))) {
        status = "400 Bad Request";
        goto fail;
    }

    ESP_LOGI(TAG, "writing %u bytes to %s", (unsigned)total, part->label);
    if (s_cfg.on_begin) {
        s_cfg.on_begin();
    }
    begun = true;
    /* Erase sector by sector as data arrives, instead of the whole slot up front. */
    err = esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &h);
    if (err != ESP_OK) {
        h = 0;
        status = "500 Internal Server Error";
        snprintf(msg, sizeof(msg), "esp_ota_begin: %s\n", esp_err_to_name(err));
        goto fail;
    }

    for (;;) {
        err = esp_ota_write(h, buf, chunk);
        if (err != ESP_OK) {
            status = "500 Internal Server Error";
            snprintf(msg, sizeof(msg), "esp_ota_write: %s\n", esp_err_to_name(err));
            goto fail;
        }
        done += chunk;
        if (done >= next_log) {
            ESP_LOGI(TAG, "%u / %u bytes", (unsigned)done, (unsigned)total);
            next_log += 1024 * 1024;
        }
        if (done == total) {
            break;
        }
        chunk = MIN(total - done, CONFIG_NET_OTA_BUF_SIZE);
        if (!recv_full(req, buf, chunk)) {
            status = "400 Bad Request";
            snprintf(msg, sizeof(msg), "receive failed after %u bytes\n", (unsigned)done);
            goto fail;
        }
    }

    err = esp_ota_end(h);
    h = 0;   /* esp_ota_end frees the handle, also on error */
    if (err != ESP_OK) {
        status = err == ESP_ERR_OTA_VALIDATE_FAILED ? "400 Bad Request" : "500 Internal Server Error";
        snprintf(msg, sizeof(msg), "esp_ota_end: %s\n", esp_err_to_name(err));
        goto fail;
    }
    err = esp_ota_set_boot_partition(part);
    if (err != ESP_OK) {
        status = "500 Internal Server Error";
        snprintf(msg, sizeof(msg), "esp_ota_set_boot_partition: %s\n", esp_err_to_name(err));
        goto fail;
    }

    free(buf);
    ESP_LOGI(TAG, "update written to %s, restarting", part->label);
    snprintf(msg, sizeof(msg), "ok: %u bytes written to %s, restarting\n", (unsigned)total, part->label);
    reply(req, "200 OK", msg);
    if (s_cfg.on_end) {
        s_cfg.on_end(true);
    }
    restart_soon();   /* s_busy stays set until the restart */
    return ESP_OK;

fail:
    if (h) {
        esp_ota_abort(h);
    }
    free(buf);
    ESP_LOGW(TAG, "update rejected: %s", msg);
    reply(req, status, msg);
    if (begun && s_cfg.on_end) {
        s_cfg.on_end(false);
    }
    atomic_store(&s_busy, false);
    return ESP_OK;
}

static esp_err_t get_ota(httpd_req_t *req)
{
    const esp_app_desc_t *d = esp_app_get_description();
    esp_ota_img_states_t st;
    char host[MDNS_NAME_BUF_LEN] = "";
    char json[384];

    mdns_hostname_get(host);   /* stays empty without mDNS */
    snprintf(json, sizeof(json),
             "{\"project\":\"%s\",\"version\":\"%s\",\"idf\":\"%s\",\"partition\":\"%s\","
             "\"state\":\"%s\",\"hostname\":\"%s\",\"busy\":%s}\n",
             d->project_name, d->version, d->idf_ver, esp_ota_get_running_partition()->label,
             running_state(&st) ? state_name(st) : "none", host, net_ota_busy() ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, json);
}

static void guard_cb(void *arg)
{
    ESP_LOGE(TAG, "image not confirmed within %d s, restarting to roll back", CONFIG_NET_OTA_CONFIRM_TIMEOUT_S);
    esp_restart();
}

static esp_err_t arm_guard(void)
{
    esp_ota_img_states_t st;
    if (!running_state(&st) || st != ESP_OTA_IMG_PENDING_VERIFY) {
        return ESP_OK;
    }
    const esp_timer_create_args_t args = {.callback = guard_cb, .name = "ota_guard"};
    ESP_RETURN_ON_ERROR(esp_timer_create(&args, &s_guard), TAG, "guard timer");
    ESP_RETURN_ON_ERROR(esp_timer_start_once(s_guard, CONFIG_NET_OTA_CONFIRM_TIMEOUT_S * 1000000ULL), TAG,
                        "guard start");
    ESP_LOGW(TAG, "new image pending verification: rolls back unless confirmed within %d s",
             CONFIG_NET_OTA_CONFIRM_TIMEOUT_S);
    return ESP_OK;
}

static void advertise(uint16_t port)
{
    char host[MDNS_NAME_BUF_LEN];
    if (mdns_hostname_get(host) != ESP_OK) {
        ESP_LOGW(TAG, "mDNS not running, not advertising the update server");
        return;
    }
    const esp_app_desc_t *d = esp_app_get_description();
    mdns_txt_item_t txt[] = {
        {"project", d->project_name},
        {"version", d->version},
        {"path", "/ota"},
    };
    esp_err_t err = mdns_service_add(NULL, "_http", "_tcp", port, txt, sizeof(txt) / sizeof(txt[0]));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "mdns_service_add: %s", esp_err_to_name(err));
    }
}

esp_err_t net_ota_start(const net_ota_config_t *cfg)
{
    ESP_RETURN_ON_FALSE(cfg, ESP_ERR_INVALID_ARG, TAG, "cfg");
    s_cfg = *cfg;

    httpd_handle_t server = cfg->server;
    if (!server) {
        httpd_config_t hc = HTTPD_DEFAULT_CONFIG();
        hc.server_port = cfg->port;
        hc.max_open_sockets = CONFIG_NET_OTA_MAX_SOCKETS;
        hc.lru_purge_enable = true;
        hc.stack_size = 6144;
        ESP_RETURN_ON_ERROR(httpd_start(&server, &hc), TAG, "httpd_start");
    }
    const httpd_uri_t post = {.uri = "/ota", .method = HTTP_POST, .handler = post_ota};
    const httpd_uri_t get = {.uri = "/ota", .method = HTTP_GET, .handler = get_ota};
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(server, &post), TAG, "register POST");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(server, &get), TAG, "register GET");

    advertise(cfg->port);
    ESP_RETURN_ON_ERROR(arm_guard(), TAG, "rollback guard");

    const esp_app_desc_t *d = esp_app_get_description();
    ESP_LOGI(TAG, "update server on port %u, running %s %s from %s", cfg->port, d->project_name, d->version,
             esp_ota_get_running_partition()->label);
    return ESP_OK;
}

void net_ota_mark_valid(void)
{
    esp_ota_img_states_t st;
    if (s_guard) {
        esp_timer_stop(s_guard);   /* ESP_ERR_INVALID_STATE if already stopped: fine */
    }
    if (!running_state(&st) || st != ESP_OTA_IMG_PENDING_VERIFY) {
        return;
    }
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "running image confirmed");
    } else {
        ESP_LOGE(TAG, "esp_ota_mark_app_valid_cancel_rollback: %s", esp_err_to_name(err));
    }
}

bool net_ota_busy(void)
{
    return atomic_load(&s_busy);
}
