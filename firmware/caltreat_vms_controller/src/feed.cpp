// Backend feed, see src/feed.h: polls the public timetable and builds what the sign shows.
#include "feed.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/time.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "net.h"

namespace feed {

static const char *TAG = "feed";
static constexpr size_t BODY_MAX = 128 * 1024;   // the full timetable is ~0.5 KB per train per day
static constexpr int64_t FAIL_LOG_US = 60 * 1000000LL;

// ---- parsing -----------------------------------------------------------------------------------
// Days since 1970-01-01 of a proleptic Gregorian date (H. Hinnant's days_from_civil).
static long daysFromCivil(int y, int m, int d) {
  y -= m <= 2;
  const long era = (y >= 0 ? y : y - 399) / 400;
  const long yoe = y - era * 400;
  const long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + doe - 719468;
}

// HTTP Date header, e.g. "Sat, 31 Oct 2026 21:07:32 GMT".
static bool parseHttpDate(const char *s, time_t *out) {
  static const char *const MONTHS = "JanFebMarAprMayJunJulAugSepOctNovDec";
  char mon[4];
  int d, y, h, mi, se;
  if (!s || sscanf(s, "%*3s, %d %3s %d %d:%d:%d", &d, mon, &y, &h, &mi, &se) != 6) return false;
  const char *p = strstr(MONTHS, mon);
  if (!p || (p - MONTHS) % 3) return false;
  *out = (time_t)(daysFromCivil(y, (int)(p - MONTHS) / 3 + 1, d) * 86400L + h * 3600L + mi * 60L + se);
  return true;
}

static const char *str(const cJSON *o, const char *k) {
  const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
  return cJSON_IsString(v) ? v->valuestring : nullptr;
}
static int num(const cJSON *o, const char *k, int dflt = 0) {
  const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
  return cJSON_IsNumber(v) ? v->valueint : dflt;
}

// [{"train_name": "101", "time": "08:21:00", "delay_minutes": 7}, ...], the whole day in scheduled
// order. Trains that have already left (scheduled time + delay passed) are skipped, so the slots go to
// upcoming ones.
static bool parseTrains(const char *body, Snapshot *out) {
  cJSON *list = cJSON_Parse(body);
  if (!cJSON_IsArray(list)) { cJSON_Delete(list); return false; }
  const time_t now = time(nullptr);
  struct tm lt;
  localtime_r(&now, &lt);
  const int nowMin = lt.tm_hour * 60 + lt.tm_min;
  out->ntrains = 0;
  const cJSON *t;
  cJSON_ArrayForEach(t, list) {
    int h, m;
    if (out->ntrains >= (int)(sizeof out->trains / sizeof out->trains[0])) break;
    if (!str(t, "train_name") || !str(t, "time") || sscanf(str(t, "time"), "%d:%d", &h, &m) != 2) continue;
    const int delay = num(t, "delay_minutes");
    if (h * 60 + m + (delay > 0 ? delay : 0) < nowMin) continue;
    Train &tr = out->trains[out->ntrains++];
    snprintf(tr.number, sizeof tr.number, "%s", str(t, "train_name"));
    tr.minute = h * 60 + m;
    tr.delay_min = delay;
  }
  cJSON_Delete(list);
  return true;
}

// The full timetable's "alerts": active service alerts, newest first.
static bool parseAlerts(const char *body, Snapshot *out) {
  cJSON *tt = cJSON_Parse(body);
  if (!cJSON_IsObject(tt)) { cJSON_Delete(tt); return false; }
  out->nalerts = 0;
  const cJSON *a;
  cJSON_ArrayForEach(a, cJSON_GetObjectItemCaseSensitive(tt, "alerts")) {
    const char *message = str(a, "message"), *train = str(a, "train_name");
    if (!message || !*message || out->nalerts >= vms::MAX_ALERTS) continue;
    Alert &al = out->alerts[out->nalerts++];
    snprintf(al.id, sizeof al.id, "%d", num(a, "id"));
    if (train && *train) snprintf(al.text, sizeof al.text, "Train %s: %s", train, message);
    else snprintf(al.text, sizeof al.text, "%s", message);
  }
  cJSON_Delete(tt);
  return true;
}

// ---- poll task ---------------------------------------------------------------------------------
struct Body {
  char *buf;
  size_t len;
  bool overflow;
};

static Body *s_into;                    // where the running request's body goes
static time_t s_date;                   // its Date header, 0 if none
static SemaphoreHandle_t s_lock;
static Snapshot s_snap;                 // guarded by s_lock
static uint32_t s_seq;
static int64_t s_lastOkUs;
static char s_lastError[64] = "not polled yet";

static esp_err_t httpEvent(esp_http_client_event_t *e) {
  Body *b = s_into;
  if (e->event_id == HTTP_EVENT_ON_HEADER && !strcasecmp(e->header_key, "Date")) {
    parseHttpDate(e->header_value, &s_date);
  } else if (e->event_id == HTTP_EVENT_ON_DATA && b) {
    if (b->len + e->data_len >= BODY_MAX) { b->overflow = true; return ESP_OK; }
    memcpy(b->buf + b->len, e->data, e->data_len);
    b->len += e->data_len;
    b->buf[b->len] = 0;
  }
  return ESP_OK;
}

// GET url into body; returns the HTTP status, 0 on a network error. One client for both requests
// (same host), kept between polls so the TLS session is reused; on an error it's recreated.
static int fetch(esp_http_client_handle_t *http, const char *url, Body *body) {
  for (int attempt = 0; attempt < 2; attempt++) {   // a kept connection may have been closed by the server
    if (!*http) {
      esp_http_client_config_t cfg = {};
      cfg.url = url;
      cfg.timeout_ms = 10000;                       // covers a Cloud Run cold start
      cfg.crt_bundle_attach = esp_crt_bundle_attach;
      cfg.keep_alive_enable = true;
      cfg.event_handler = httpEvent;
      *http = esp_http_client_init(&cfg);
      if (!*http) { snprintf(s_lastError, sizeof s_lastError, "no memory"); return 0; }
    } else {
      esp_http_client_set_url(*http, url);
    }
    body->len = 0;
    body->buf[0] = 0;
    body->overflow = false;
    s_into = body;
    const esp_err_t err = esp_http_client_perform(*http);
    if (err == ESP_OK) {
      if (body->overflow) { snprintf(s_lastError, sizeof s_lastError, "response too big"); return 0; }
      return esp_http_client_get_status_code(*http);
    }
    snprintf(s_lastError, sizeof s_lastError, "%s", esp_err_to_name(err));
    esp_http_client_cleanup(*http);
    *http = nullptr;
  }
  return 0;
}

// The backend's clock is the line's clock: follow it.
static void syncClock(time_t date) {
  const time_t now = time(nullptr);
  if (date && (now - date > 2 || date - now > 2)) {
    const struct timeval tv = {date, 0};
    settimeofday(&tv, nullptr);
  }
}

static bool poll(esp_http_client_handle_t *http, Body *body, Snapshot *snap) {
  char url[160];
  snprintf(url, sizeof url, "%s/api/timetable/?kiosk_id=%s", CONFIG_VMS_BACKEND_URL, net_hostname());
  s_date = 0;
  int status = fetch(http, url, body);
  if (status == 0) return false;
  syncClock(s_date);
  snap->registered = status != 404;
  if (status == 404) {
    snap->ntrains = 0;
  } else if (status != 200 || !parseTrains(body->buf, snap)) {
    snprintf(s_lastError, sizeof s_lastError, "kiosk timetable: HTTP %d", status);
    return false;
  }

  snprintf(url, sizeof url, "%s/api/timetable/", CONFIG_VMS_BACKEND_URL);
  status = fetch(http, url, body);
  if (status != 200 || !parseAlerts(body->buf, snap)) {
    if (status) snprintf(s_lastError, sizeof s_lastError, "timetable: HTTP %d", status);
    return false;
  }
  return true;
}

static void pollTask(void *) {
  Body body = {(char *)heap_caps_malloc(BODY_MAX, MALLOC_CAP_SPIRAM), 0, false};
  Snapshot *snap = (Snapshot *)heap_caps_malloc(sizeof(Snapshot), MALLOC_CAP_SPIRAM);
  if (!body.buf || !snap) {
    ESP_LOGE(TAG, "no memory for the feed");
    vTaskDelete(nullptr);
  }
  esp_http_client_handle_t http = nullptr;
  int64_t lastFailLog = -FAIL_LOG_US;

  for (;;) {
    net_wait_for_ip(portMAX_DELAY);
    const bool ok = poll(&http, &body, snap);
    const int64_t now = esp_timer_get_time();
    if (ok) {
      xSemaphoreTake(s_lock, portMAX_DELAY);
      s_snap = *snap;
      s_seq++;
      s_lastOkUs = now;
      snprintf(s_lastError, sizeof s_lastError, "none");
      xSemaphoreGive(s_lock);
    } else if (now - lastFailLog >= FAIL_LOG_US) {
      ESP_LOGW(TAG, "poll failed: %s", s_lastError);
      lastFailLog = now;
    }
    vTaskDelay(pdMS_TO_TICKS(CONFIG_VMS_POLL_S * 1000));
  }
}

static void *psramMalloc(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM); }

void start() {
  // A full timetable is a thousand-odd small JSON nodes: keep them out of the scarce internal RAM.
  cJSON_Hooks hooks = {psramMalloc, heap_caps_free};
  cJSON_InitHooks(&hooks);
  s_lock = xSemaphoreCreateMutex();
  // Same level as the net supervisor: below the USB/Ethernet driver tasks, above the sign loop.
  if (!s_lock || xTaskCreate(pollTask, "feed", 8192, nullptr, 3, nullptr) != pdPASS)
    ESP_LOGE(TAG, "feed task failed to start");
}

bool get(Snapshot *out, uint32_t *seq) {
  xSemaphoreTake(s_lock, portMAX_DELAY);
  const bool fresh = s_seq != *seq;
  if (fresh) { *out = s_snap; *seq = s_seq; }
  xSemaphoreGive(s_lock);
  return fresh;
}

int64_t lastOkUs() {
  xSemaphoreTake(s_lock, portMAX_DELAY);
  const int64_t t = s_lastOkUs;
  xSemaphoreGive(s_lock);
  return t;
}

void printStatus() {
  xSemaphoreTake(s_lock, portMAX_DELAY);
  const int64_t ok = s_lastOkUs;
  const bool registered = s_snap.registered;
  const int ntrains = s_snap.ntrains, nalerts = s_snap.nalerts;
  char err[sizeof s_lastError];
  snprintf(err, sizeof err, "%s", s_lastError);
  xSemaphoreGive(s_lock);
  printf("feed: kiosk id %s%s\n", net_hostname(), ok && !registered ? " (not registered with the backend)" : "");
  if (ok) printf("feed: last ok %lld s ago, %d trains today, %d alerts, last error %s\n",
                 (esp_timer_get_time() - ok) / 1000000, ntrains, nalerts, err);
  else printf("feed: never reached the backend, last error %s\n", err);
}

}  // namespace feed
