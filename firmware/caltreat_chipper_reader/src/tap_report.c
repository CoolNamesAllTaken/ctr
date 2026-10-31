#include "tap_report.h"

#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

#include "backend.h"
#include "net.h"

static const char *TAG = "tap_report";

/* ~24 B each, in PSRAM: 16384 reports (~400 KB) is over 30 taps for each of
 * the 500 cards, i.e. effectively unbounded for an event. */
#define QUEUE_LEN      16384
#define BACKOFF_MIN_MS 5000
#define BACKOFF_MAX_MS 60000

typedef struct {
    uint8_t uid[10];
    uint8_t uid_len;
    card_status_t result;
    uint8_t half_rides;
    uint32_t seq;
    int64_t unix_ms;            /* 0: clock not synced, sent as null */
} report_t;

static SemaphoreHandle_t s_lock;
static TaskHandle_t s_task;
static report_t *s_queue;         /* QUEUE_LEN entries, PSRAM */
static int s_head, s_count;     /* s_head = oldest */
static uint32_t s_seq;
static tap_report_stats_t s_stats;

void tap_report_add(const uint8_t *uid, uint8_t uid_len, card_status_t result, uint8_t half_rides_left)
{
    if (!s_lock) {
        return;
    }
    report_t r = {.uid_len = uid_len, .result = result, .half_rides = half_rides_left};
    memcpy(r.uid, uid, uid_len);
    if (net_time_synced()) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        r.unix_ms = (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    r.seq = ++s_seq;
    if (s_count == QUEUE_LEN) {
        s_head = (s_head + 1) % QUEUE_LEN;   /* drop the oldest */
        s_count--;
        s_stats.dropped_full++;
    }
    s_queue[(s_head + s_count) % QUEUE_LEN] = r;
    s_count++;
    xSemaphoreGive(s_lock);
    xTaskNotifyGive(s_task);
}

static bool peek(report_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool any = s_count > 0;
    if (any) {
        *out = s_queue[s_head];
    }
    xSemaphoreGive(s_lock);
    return any;
}

/* Remove the oldest report if it is still `seq` (it may have been dropped meanwhile). */
static void pop(uint32_t seq)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_count && s_queue[s_head].seq == seq) {
        s_head = (s_head + 1) % QUEUE_LEN;
        s_count--;
    }
    xSemaphoreGive(s_lock);
}

static void to_json(const report_t *r, char *buf, size_t len)
{
    char uid[2 * sizeof(r->uid) + 1] = "";
    for (int i = 0; i < r->uid_len; i++) {
        sprintf(uid + 2 * i, "%02x", r->uid[i]);
    }
    char at[32] = "null";
    if (r->unix_ms) {
        time_t sec = r->unix_ms / 1000;
        struct tm tm;
        gmtime_r(&sec, &tm);
        snprintf(at, sizeof(at), "\"%04d-%02d-%02dT%02d:%02d:%02d.%03dZ\"", tm.tm_year + 1900, tm.tm_mon + 1,
                 tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, (int)(r->unix_ms % 1000));
    }
    /* card_uid and kiosk_id are what the backend reads today; the rest is for
     * the card-held-rides version of the endpoint. */
    snprintf(buf, len,
             "{\"card_uid\":\"%s\",\"kiosk_id\":\"%s\",\"result\":\"%s\",\"rides_left\":%u,"
             "\"half_rides_left\":%u,\"tapped_at\":%s,\"seq\":%lu}",
             uid, net_hostname(), card_status_name(r->result), r->half_rides / 2, r->half_rides, at,
             (unsigned long)r->seq);
}

static void sender_task(void *arg)
{
    uint32_t backoff = BACKOFF_MIN_MS;
    for (;;) {
        report_t r;
        if (!peek(&r)) {
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
            continue;
        }
        if (!net_wait_for_ip(pdMS_TO_TICKS(1000))) {
            continue;
        }

        char json[256], body[160];
        int status;
        to_json(&r, json, sizeof(json));
        esp_err_t err = backend_request(HTTP_METHOD_POST, "/api/taps/", json, &status, body, sizeof(body));

        bool done = false;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_stats.last_status = err == ESP_OK ? status : 0;
        if (err == ESP_OK && status >= 200 && status < 300) {
            s_stats.sent++;
            done = true;
        } else if (err == ESP_OK && status >= 400 && status < 500 && status != 401) {
            /* Refused for good (e.g. unknown card): retrying won't help. */
            s_stats.dropped_rejected++;
            snprintf(s_stats.last_detail, sizeof(s_stats.last_detail), "%s", body);
            done = true;
        }
        xSemaphoreGive(s_lock);

        if (done) {
            if (status >= 300) {
                ESP_LOGW(TAG, "report %lu refused (%d): %s", (unsigned long)r.seq, status, body);
            }
            pop(r.seq);
            backoff = BACKOFF_MIN_MS;
            continue;
        }
        /* No network, 5xx, or 401 (the endpoint still needs the employee
         * passphrase until it is opened): keep it and retry later. */
        ESP_LOGD(TAG, "report %lu not delivered (%s, %d), retry in %lu ms", (unsigned long)r.seq,
                 err == ESP_OK ? "http" : esp_err_to_name(err), status, (unsigned long)backoff);
        vTaskDelay(pdMS_TO_TICKS(backoff));
        backoff = backoff * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : backoff * 2;
    }
}

esp_err_t tap_report_init(void)
{
    s_queue = heap_caps_calloc(QUEUE_LEN, sizeof(report_t), MALLOC_CAP_SPIRAM);
    ESP_RETURN_ON_FALSE(s_queue, ESP_ERR_NO_MEM, TAG, "queue");
    s_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_lock, ESP_ERR_NO_MEM, TAG, "lock");
    BaseType_t ok = xTaskCreate(sender_task, "tap_report", 6144, NULL, 3, &s_task);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}

void tap_report_stats(tap_report_stats_t *out)
{
    if (!s_lock) {
        *out = (tap_report_stats_t){0};
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_stats;
    out->queued = s_count;
    xSemaphoreGive(s_lock);
}
