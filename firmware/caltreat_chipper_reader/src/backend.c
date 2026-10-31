#include "backend.h"

#include <stdio.h>
#include <string.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"

#include "net.h"

static const char *TAG = "backend";

#define TIMEOUT_MS 10000   /* covers a Cloud Run cold start */

static SemaphoreHandle_t s_lock;
static esp_http_client_handle_t s_http;   /* kept between requests so TLS is reused */
static char s_user_agent[64];

/* Response body of the request in progress (s_lock held). */
static char *s_body;
static size_t s_body_len, s_body_used;

static esp_err_t http_event(esp_http_client_event_t *e)
{
    if (e->event_id == HTTP_EVENT_ON_DATA && s_body && s_body_used + 1 < s_body_len) {
        size_t n = MIN((size_t)e->data_len, s_body_len - 1 - s_body_used);
        memcpy(s_body + s_body_used, e->data, n);
        s_body_used += n;
        s_body[s_body_used] = '\0';
    }
    return ESP_OK;
}

static esp_err_t perform(esp_http_client_method_t method, const char *url, const char *json, int *status)
{
    if (!s_http) {
        const esp_http_client_config_t cfg = {
            .url = url,
            .timeout_ms = TIMEOUT_MS,
            .crt_bundle_attach = esp_crt_bundle_attach,
            .keep_alive_enable = true,
            .user_agent = s_user_agent,
            .event_handler = http_event,
        };
        s_http = esp_http_client_init(&cfg);
        if (!s_http) {
            return ESP_ERR_NO_MEM;
        }
    }
    esp_http_client_set_url(s_http, url);
    esp_http_client_set_method(s_http, method);
    if (json) {
        esp_http_client_set_header(s_http, "Content-Type", "application/json");
        esp_http_client_set_post_field(s_http, json, strlen(json));
    } else {
        esp_http_client_delete_header(s_http, "Content-Type");
        esp_http_client_set_post_field(s_http, NULL, 0);
    }
    s_body_used = 0;
    if (s_body) {
        s_body[0] = '\0';
    }
    esp_err_t err = esp_http_client_perform(s_http);
    if (err == ESP_OK) {
        *status = esp_http_client_get_status_code(s_http);
    } else {
        esp_http_client_cleanup(s_http);
        s_http = NULL;
    }
    return err;
}

esp_err_t backend_request(esp_http_client_method_t method, const char *path, const char *json,
                          int *status, char *body, size_t body_len)
{
    char url[192];
    snprintf(url, sizeof(url), "%s%s", CONFIG_CHIPPER_BACKEND_URL, path);
    *status = 0;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_body = body;
    s_body_len = body ? body_len : 0;
    bool reused = s_http != NULL;
    esp_err_t err = perform(method, url, json, status);
    if (err != ESP_OK && reused) {
        err = perform(method, url, json, status);   /* the server may have closed the kept connection */
    }
    s_body = NULL;
    xSemaphoreGive(s_lock);

    if (err != ESP_OK) {
        ESP_LOGD(TAG, "%s: %s", path, esp_err_to_name(err));
    }
    return err;
}

esp_err_t backend_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_lock, ESP_ERR_NO_MEM, TAG, "lock");
    /* Shows up in the Cloud Run request logs: which reader, which firmware. */
    snprintf(s_user_agent, sizeof(s_user_agent), "chipper-reader/%.24s %s", esp_app_get_description()->version,
             net_device_id());
    return ESP_OK;
}
