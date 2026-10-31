#include "reader.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_log.h"

#include "audio.h"
#include "leds.h"
#include "settings.h"
#include "tap_report.h"
#include "ui.h"

static const char *TAG = "reader";

#define LOCATOR_PERIOD_MS 15000

typedef enum {
    EVT_CARD_RESULT,
    EVT_PRESENT_CARD,
    EVT_TAP_VIRTUAL,
    EVT_FORCE_RESULT,
} reader_evt_type_t;

typedef struct {
    reader_evt_type_t type;
    union {
        struct {
            uint8_t uid[RC522_UID_MAX];
            uint8_t uid_len;
            card_status_t status;
            uint8_t half_rides;
        } chipper;
        reader_card_t card;
        reader_result_kind_t forced;
    };
} reader_evt_t;

static QueueHandle_t s_queue;
static volatile bool s_showing_result;
static volatile int32_t s_virtual_balance = 2000;
static bool s_virtual_tagged_on;

static int32_t fare_for(bool discount)
{
    return discount ? g_settings.fare_cents / 2 : g_settings.fare_cents;
}

static reader_result_t charge(int32_t balance, bool discount, bool tagged_on)
{
    reader_result_t r = {
        .fare_cents = fare_for(discount),
        .balance_cents = balance,
        .discount = discount,
        .tagged_on = tagged_on,
    };
    if (g_settings.tag_on_off && tagged_on) {
        /* Second tap closes the trip; the fare was already taken at tag on. */
        r.kind = READER_RESULT_TAG_OFF;
        r.fare_cents = 0;
        r.tagged_on = false;
        return r;
    }
    if (balance < r.fare_cents) {
        r.kind = READER_RESULT_INSUFFICIENT;
        return r;
    }
    r.balance_cents -= r.fare_cents;
    r.tagged_on = g_settings.tag_on_off;
    r.kind = r.balance_cents < g_settings.low_balance_cents ? READER_RESULT_LOW_BALANCE
                                                             : READER_RESULT_OK;
    return r;
}

static audio_cue_t cue_for(reader_result_kind_t kind)
{
    switch (kind) {
    case READER_RESULT_OK:
        return AUDIO_CUE_OK;
    case READER_RESULT_LOW_BALANCE:
        return AUDIO_CUE_LOW_BALANCE;
    case READER_RESULT_TAG_OFF:
        return AUDIO_CUE_TAG_OFF;
    case READER_RESULT_ALREADY_TAPPED:
        return AUDIO_CUE_OK;
    case READER_RESULT_INSUFFICIENT:
        return AUDIO_CUE_REJECT;
    default:
        return AUDIO_CUE_WARNING;
    }
}

/* Clipper light code: green = paid, yellow = paid but low, red = not accepted
 * (one ~1 s flash per tap, see leds.h). */
static void lights_for(reader_result_kind_t kind)
{
    switch (kind) {
    case READER_RESULT_OK:
    case READER_RESULT_ALREADY_TAPPED:
    case READER_RESULT_TAG_OFF:
        leds_flash(LEDS_GREEN);
        break;
    case READER_RESULT_LOW_BALANCE:
        leds_flash(LEDS_YELLOW);
        break;
    default:   /* no rides / insufficient, not a Chipper card, card error */
        leds_flash(LEDS_RED);
        break;
    }
}

static reader_result_kind_t kind_for(card_status_t st)
{
    switch (st) {
    case CARD_TAP_ON:         return READER_RESULT_OK;
    case CARD_TAP_OFF:        return READER_RESULT_TAG_OFF;
    case CARD_NO_RIDES:       return READER_RESULT_INSUFFICIENT;
    case CARD_ALREADY_TAPPED: return READER_RESULT_ALREADY_TAPPED;
    case CARD_NOT_CHIPPER:    return READER_RESULT_NOT_CHIPPER;
    default:                  return READER_RESULT_CARD_ERROR;
    }
}

static reader_result_t handle_event(const reader_evt_t *evt)
{
    reader_result_t r;
    switch (evt->type) {
    case EVT_CARD_RESULT: {
        const card_status_t st = evt->chipper.status;
        r = (reader_result_t){.kind = kind_for(st), .rides_left = evt->chipper.half_rides / 2,
                              .uid_len = evt->chipper.uid_len};
        memcpy(r.uid, evt->chipper.uid, evt->chipper.uid_len);
        if (st == CARD_DAMAGED) {
            ESP_LOGW(TAG, "card damaged (CRC mismatch): re-issue it");
        }
        /* Taps on, off and refused are what the backend hears about. */
        if (st == CARD_TAP_ON || st == CARD_TAP_OFF || st == CARD_NO_RIDES) {
            tap_report_add(r.uid, r.uid_len, st, evt->chipper.half_rides);
        }
        return r;
    }
    case EVT_PRESENT_CARD:
        r = charge(evt->card.balance_cents, evt->card.discount, evt->card.tagged_on);
        break;
    case EVT_TAP_VIRTUAL:
        r = charge(s_virtual_balance, g_settings.discount, s_virtual_tagged_on);
        s_virtual_balance = r.balance_cents;
        s_virtual_tagged_on = r.tagged_on;
        break;
    case EVT_FORCE_RESULT:
    default:
        r = (reader_result_t){
            .kind = evt->forced,
            .fare_cents = fare_for(g_settings.discount),
            .balance_cents = s_virtual_balance,
            .discount = g_settings.discount,
            .tagged_on = s_virtual_tagged_on,
        };
        break;
    }
    r.rides_left = -1;   /* money screens */
    return r;
}

static void reader_task(void *arg)
{
    reader_evt_t evt;

    while (true) {
        TickType_t wait = pdMS_TO_TICKS(s_showing_result ? g_settings.result_ms : LOCATOR_PERIOD_MS);

        if (xQueueReceive(s_queue, &evt, wait) == pdTRUE) {
            s_showing_result = true;
            reader_result_t r = handle_event(&evt);
            if (r.rides_left >= 0) {
                ESP_LOGI(TAG, "result=%d rides_left=%d", r.kind, r.rides_left);
            } else {
                ESP_LOGI(TAG, "result=%d fare=%ld balance=%ld", r.kind, (long)r.fare_cents,
                         (long)r.balance_cents);
            }
            /* Sound first: the beep is the rider's primary feedback. */
            audio_play(cue_for(r.kind));
            lights_for(r.kind);
            ui_show_result(&r);
        } else if (s_showing_result) {
            ui_show_idle();
            s_showing_result = false;
        } else if (g_settings.locator_tone) {
            audio_play(AUDIO_CUE_LOCATOR);
        }
    }
}

esp_err_t reader_init(void)
{
    s_queue = xQueueCreate(8, sizeof(reader_evt_t));
    ESP_RETURN_ON_FALSE(s_queue, ESP_ERR_NO_MEM, TAG, "queue");
    BaseType_t ok = xTaskCreate(reader_task, "reader", 4096, NULL, 5, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}

static void post(const reader_evt_t *evt)
{
    if (xQueueSend(s_queue, evt, 0) != pdTRUE) {
        ESP_LOGW(TAG, "event dropped (queue full)");
    }
}

void reader_card_result(const rc522_card_t *card, card_status_t st, uint8_t half_rides_left)
{
    reader_evt_t evt = {.type = EVT_CARD_RESULT,
                        .chipper = {.uid_len = card->uid_len, .status = st, .half_rides = half_rides_left}};
    memcpy(evt.chipper.uid, card->uid, card->uid_len);
    post(&evt);
}

void reader_present_card(const reader_card_t *card)
{
    reader_evt_t evt = {.type = EVT_PRESENT_CARD, .card = *card};
    post(&evt);
}

void reader_tap_virtual_card(void)
{
    reader_evt_t evt = {.type = EVT_TAP_VIRTUAL};
    post(&evt);
}

void reader_force_result(reader_result_kind_t kind)
{
    reader_evt_t evt = {.type = EVT_FORCE_RESULT, .forced = kind};
    post(&evt);
}

bool reader_is_idle(void)
{
    return !s_showing_result && uxQueueMessagesWaiting(s_queue) == 0;
}

int32_t reader_virtual_balance(void)
{
    return s_virtual_balance;
}

void reader_set_virtual_balance(int32_t cents)
{
    s_virtual_balance = cents;
}
