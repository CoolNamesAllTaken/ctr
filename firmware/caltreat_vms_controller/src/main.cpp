// CalTreat VMS - Caltrain-style platform sign on a Waveshare ESP32-S3-RGB-Matrix + 4 x P5 64x32.
//
// Plain ESP-IDF 5.5 (no Arduino). Trains, delays and alerts come from the backend (src/feed.cpp) and
// are shown by the page engine (src/vms_core.cpp): train list, clock, scrolling alerts and
// ATTENTION / #NNN APPROACHING.
//
// Chain order: ESP32 -> A1 -> A2 -> (crossover ribbon) -> B1 -> B2.  x 0..127 = face A, 128..255 = face B.
// Both faces show the same picture. At boot a 3 s test pattern numbers the panels 1-4 in chain order.
//
// Network: a USB Ethernet adapter on the USB-C port (src/net.c, as in caltreat_chipper_reader) takes
// firmware pushed over HTTP (net_ota), see docs/ota.md. The sign shows UPDATING while an image is written.
//
// Serial console on UART0 (TX GPIO43, RX GPIO44, 115200), single keys:
//   p  show the panel test pattern      i  network, feed and firmware info

#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "board_pins.h"
#include "feed.h"
#include "fonts/CTBold13.h"
#include "net.h"
#include "net_ota.h"
#include "vms_core.h"

static const char *TAG = "main";
static constexpr uart_port_t CONSOLE_UART = UART_NUM_0;

static uint32_t millis() { return (uint32_t)(esp_timer_get_time() / 1000); }

static constexpr int PANEL_W = 64, PANEL_H = 32, CHAIN = 4;
static constexpr uint32_t FRAME_MS = 4;        // engine tick; the panel is only rewritten when the picture changes
static constexpr uint32_t PATTERN_MS = 3000;
static constexpr time_t CLOCK_VALID = 1735689600;   // 2025-01-01: anything earlier means not set yet
static const char *const STALE_ID = "stale";
static const char *const KIOSK_ID = "kiosk";     // alert shown while the backend doesn't know this sign
static uint16_t AMBER;                         // color565(255,150,0)

MatrixPanel_I2S_DMA *dma = nullptr;
vms::Sign sign;
vms::Frame shown;
static uint32_t patternUntil = 0;
static volatile bool updating = false;         // an OTA image is being written

// ---- backend data -> sign ----------------------------------------------------------------------
static feed::Snapshot snap;
static uint32_t snapSeq = 0;
static bool stale = false;

// Trains already announced (ATTENTION page), so each arrival is announced once.
static struct { char number[8]; int minute; } announced[16];
static size_t announcedNext = 0;

static bool wasAnnounced(const feed::Train &t) {
  for (const auto &a : announced)
    if (a.minute == t.minute && !strcmp(a.number, t.number)) return true;
  return false;
}

// Rows from the backend's trains at this station: departed trains (scheduled time + delay passed) drop
// off, and a train is ARRIVING in its last minute before departure, as on the backend's live map.
static void applySnapshot(uint32_t now) {
  const time_t t = time(nullptr);
  struct tm lt;
  localtime_r(&t, &lt);
  const int nowSec = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec;

  vms::Train rows[vms::MAX_TRAINS];
  int n = 0;
  for (int i = 0; i < snap.ntrains && n < vms::MAX_TRAINS; i++) {
    const feed::Train &tr = snap.trains[i];
    const int departSec = (tr.minute + (tr.delay_min > 0 ? tr.delay_min : 0)) * 60;
    if (nowSec >= departSec) continue;
    const bool arriving = departSec - nowSec <= 60;
    char hhmm[8], status[24];
    snprintf(hhmm, sizeof hhmm, "%d:%02d", tr.minute / 60, tr.minute % 60);
    if (arriving) snprintf(status, sizeof status, "ARRIVING");
    else if (tr.delay_min > 0) snprintf(status, sizeof status, "%d MIN LATE", tr.delay_min);
    else snprintf(status, sizeof status, "ON TIME");
    if (!rows[n].set(tr.number, hhmm, status)) continue;
    n++;
    if (arriving && !wasAnnounced(tr)) {
      sign.approach(rows[n - 1].number, true, now);
      snprintf(announced[announcedNext].number, sizeof announced[0].number, "%s", tr.number);
      announced[announcedNext].minute = tr.minute;
      announcedNext = (announcedNext + 1) % (sizeof announced / sizeof announced[0]);
    }
  }
  sign.setTrains(rows, n);

  // Alerts by backend id: drop the ones that are gone, then add or update the rest.
  for (int i = 0; i < vms::MAX_ALERTS; i++) {
    const vms::Alert &a = sign.alertSlot(i);
    if (!a.used) continue;
    bool keep = !snap.registered && !strcmp(a.id, KIOSK_ID);
    for (int j = 0; j < snap.nalerts && !keep; j++) keep = !strcmp(a.id, snap.alerts[j].id);
    if (!keep) sign.removeAlert(a.id);
  }
  for (int j = 0; j < snap.nalerts; j++) sign.setAlert(snap.alerts[j].id, snap.alerts[j].text, 0, now);
  if (!snap.registered) {
    char text[96];
    snprintf(text, sizeof text, "Sign not registered: add kiosk %s and assign it a station.", net_hostname());
    sign.setAlert(KIOSK_ID, text, 0, now);
  }
}

static void pollFeed(uint32_t now) {
  if (feed::get(&snap, &snapSeq)) {
    stale = false;
    applySnapshot(now);
    return;
  }
  // No good poll for a while (or ever): don't leave old times up.
  const int64_t ok = feed::lastOkUs();
  const bool isStale = esp_timer_get_time() - ok > CONFIG_VMS_STALE_S * 1000000LL;
  if (isStale && !stale) {
    stale = true;
    sign.setTrains(nullptr, 0);
    sign.clearAlerts();
    sign.setAlert(STALE_ID, "Real-time information is unavailable.", 0, now);
  }
}

// ---- display -----------------------------------------------------------------------------------
// Each panel gets a border and its chain position; RGB squares in panel 1 check the colour order.
static void showPattern(uint32_t now) {
  dma->fillScreen(0);
  for (int p = 0; p < CHAIN; p++) {
    const int x0 = p * PANEL_W;
    dma->drawFastHLine(x0, 0, PANEL_W, AMBER);
    dma->drawFastHLine(x0, PANEL_H - 1, PANEL_W, AMBER);
    dma->drawFastVLine(x0, 0, PANEL_H, AMBER);
    dma->drawFastVLine(x0 + PANEL_W - 1, 0, PANEL_H, AMBER);
    const char digit[2] = {(char)('1' + p), 0};   // no GFX text without Arduino: draw with the VMS font
    vms::Frame f;
    f.clear();
    vms::drawText(f, CTBold13, (PANEL_W - vms::textWidth(CTBold13, digit)) / 2, 23, digit);
    for (int y = 0; y < PANEL_H; y++)
      for (int x = 0; x < PANEL_W; x++)
        if (f.get(x, y)) dma->drawPixel(x0 + x, y, AMBER);
  }
  dma->fillRect(3, 3, 6, 6, dma->color565(255, 0, 0));
  dma->fillRect(3, 13, 6, 6, dma->color565(0, 255, 0));
  dma->fillRect(3, 23, 6, 6, dma->color565(0, 0, 255));
  dma->flipDMABuffer();
  patternUntil = now + PATTERN_MS;
  memset(shown.px, 0xFF, sizeof shown.px);     // force a redraw when the pattern ends
}

static void renderFrame(uint32_t now) {
  if ((int32_t)(patternUntil - now) > 0) return;
  vms::Frame f;
  if (updating) {
    static const char *const MSG = "UPDATING";
    f.clear();
    vms::drawText(f, CTBold13, (vms::W - vms::textWidth(CTBold13, MSG)) / 2, 22, MSG);
  } else {
    struct tm lt;
    const time_t t = time(nullptr);
    localtime_r(&t, &lt);
    sign.render(now, t >= CLOCK_VALID ? &lt : nullptr, f);   // clock page only once the backend set the time
  }
  if (memcmp(f.px, shown.px, sizeof f.px) == 0) return;   // unchanged: nothing to send
  shown = f;
  dma->fillScreen(0);
  for (int y = 0; y < vms::H; y++)
    for (int x = 0; x < vms::W; x++)
      if (f.get(x, y)) {
        dma->drawPixel(x, y, AMBER);                       // face A
        dma->drawPixel(vms::W + x, y, AMBER);              // face B
      }
  dma->flipDMABuffer();
}

// ---- console -----------------------------------------------------------------------------------
static void printInfo() {
  net_status_t st;
  net_get_status(&st);
  printf("net: nic %s  ip " IPSTR "  reinits %lu  backend %s\n", net_nic_state_name(st.nic), IP2STR(&st.ip),
         (unsigned long)st.nic_reinits, net_backend_state_name(st.backend));
  printf("net: host %s.local  id %s  backend url %s\n", net_hostname(), net_device_id(), CONFIG_VMS_BACKEND_URL);
  feed::printStatus();
  for (int i = 0; i < sign.trainCount(); i++)
    printf("sign: %-4s %5s %s  %s\n", sign.train(i).number, sign.train(i).time, sign.train(i).ampm, sign.train(i).status);
  printf("sign: page %s, %d alerts\n", sign.pageName(), sign.alertCount(millis()));

  const esp_app_desc_t *d = esp_app_get_description();
  const esp_partition_t *part = esp_ota_get_running_partition();
  esp_ota_img_states_t ota;
  const char *state = "none";
  if (esp_ota_get_state_partition(part, &ota) == ESP_OK) {
    state = ota == ESP_OTA_IMG_VALID            ? "valid"
            : ota == ESP_OTA_IMG_PENDING_VERIFY ? "pending_verify"
            : ota == ESP_OTA_IMG_NEW            ? "new"
                                                : "other";
  }
  printf("ota: %s %s (IDF %s, built %s %s)\n", d->project_name, d->version, d->idf_ver, d->date, d->time);
  printf("ota: running %s @0x%lx, state %s%s\n", part->label, (unsigned long)part->address, state,
         net_ota_busy() ? ", update in progress" : "");
}

static void pollSerial(uint32_t now) {
  char c;
  while (uart_read_bytes(CONSOLE_UART, &c, 1, 0) == 1) {
    switch (c) {
      case 'p': showPattern(now); break;
      case 'i': printInfo(); break;
      default: break;
    }
  }
}

// ---- network / OTA -----------------------------------------------------------------------------
static void onOtaBegin() { updating = true; }
static void onOtaEnd(bool ok) {
  if (!ok) updating = false;                   // on success the restart follows
}

// Self-test of a freshly updated image: it must get back on the network, so the next update can reach
// it. Never depends on the backend.
static void otaConfirmTask(void *) {
  if (net_wait_for_ip(portMAX_DELAY)) net_ota_mark_valid();
  vTaskDelete(nullptr);
}

static void startNetwork() {
  if (net_init() != ESP_OK) {
    ESP_LOGE(TAG, "network init failed: no updates or backend this boot");
    return;
  }
  feed::start();
  net_ota_config_t ota = NET_OTA_DEFAULT_CONFIG();
  ota.on_begin = onOtaBegin;
  ota.on_end = onOtaEnd;
  if (net_ota_start(&ota) != ESP_OK) {
    ESP_LOGE(TAG, "update server failed to start");
    return;
  }
  xTaskCreate(otaConfirmTask, "ota_confirm", 2048, nullptr, 2, nullptr);
}

static void setup() {
  // Route stdout through the UART driver too, which also gives non-blocking key reads.
  ESP_ERROR_CHECK(uart_driver_install(CONSOLE_UART, 256, 0, 0, nullptr, 0));
  uart_vfs_dev_use_driver(CONSOLE_UART);

  HUB75_I2S_CFG cfg(PANEL_W, PANEL_H, CHAIN,
      {PIN_R1, PIN_G1, PIN_B1, PIN_R2, PIN_G2, PIN_B2,
       PIN_A, PIN_B, PIN_C, PIN_D, PIN_E, PIN_LAT, PIN_OE, PIN_CLK});
  cfg.double_buff = true;                       // 2 x 48 KiB internal SRAM at 6-bit depth
  cfg.i2sspeed = HUB75_I2S_CFG::HZ_16M;
  cfg.min_refresh_rate = 120;
  cfg.clkphase = false;                         // Waveshare examples; if the panels show garbage, build
#ifdef PANEL_DRIVER_SHIFTREG                    // with -DPANEL_DRIVER_SHIFTREG
  cfg.driver = HUB75_I2S_CFG::SHIFTREG;
#else
  cfg.driver = HUB75_I2S_CFG::FM6126A;
#endif

  dma = new MatrixPanel_I2S_DMA(cfg);
  if (!dma->begin()) { puts("HUB75 init failed (memory?)"); while (true) vTaskDelay(pdMS_TO_TICKS(1000)); }
  AMBER = dma->color565(255, 150, 0);
  dma->setBrightness8(255);

  // Times are shown on the line's clock (Pacific); the backend sets the time itself (src/feed.cpp).
  setenv("TZ", "PST8PDT,M3.2.0,M11.1.0", 1);
  tzset();

  // After the display, so its DMA buffers get internal RAM first. USB Ethernet comes up in the
  // background; the sign works without it.
  startNetwork();

  puts("CalTreat VMS. Keys: p i");
  showPattern(millis());
}

static void loop() {
  static uint32_t last = 0;
  const uint32_t now = millis();
  pollSerial(now);
  pollFeed(now);
  if (now - last >= FRAME_MS) { last = now; renderFrame(now); }
}

extern "C" void app_main() {
  setup();
  for (;;) {
    loop();
    vTaskDelay(1);                             // 1 ms tick: lets the idle task feed the watchdog
  }
}
