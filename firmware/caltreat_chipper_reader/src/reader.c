#include "reader.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_log.h"

#include "audio.h"
#include "settings.h"
#include "ui.h"

static const char *TAG = "reader";

#define LOCATOR_PERIOD_MS 15000

typedef enum {
    EVT_PRESENT_CARD,
    EVT_TAP_VIRTUAL,
    EVT_FORCE_RESULT,
} reader_evt_type_t;

typedef struct {
    reader_evt_type_t type;
    union {
        reader_card_t card;
        reader_result_kind_t forced;
    };
} reader_evt_t;

static QueueHandle_t s_queue;
static volatile int32_t s_virtual_balance = 2000;
static bool s_virtual_tagged_on;

/* Card accounts, keyed by UID (the "back office"); not persisted across reboots. */
static reader_account_t s_accounts[MAX_ACCOUNTS];
static int s_account_count;
static SemaphoreHandle_t s_accounts_lock;

/* Find the account for a UID, enrolling it if new. Caller holds the lock. */
static reader_account_t *account_for(const uint8_t *uid, uint8_t len)
{
    for (int i = 0; i < s_account_count; i++) {
        if (s_accounts[i].uid_len == len && !memcmp(s_accounts[i].uid, uid, len)) {
            return &s_accounts[i];
        }
    }
    /* table full: recycle the oldest enrolment */
    int i = s_account_count < MAX_ACCOUNTS ? s_account_count++ : 0;
    s_accounts[i] = (reader_account_t){.uid_len = len, .balance_cents = ACCOUNT_START_CENTS};
    memcpy(s_accounts[i].uid, uid, len);
    ESP_LOGI(TAG, "enrolled new card with %d.%02d", ACCOUNT_START_CENTS / 100, ACCOUNT_START_CENTS % 100);
    return &s_accounts[i];
}

int reader_accounts(reader_account_t *out, int max)
{
    xSemaphoreTake(s_accounts_lock, portMAX_DELAY);
    int n = s_account_count < max ? s_account_count : max;
    memcpy(out, s_accounts, n * sizeof(*out));
    xSemaphoreGive(s_accounts_lock);
    return n;
}

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
    case READER_RESULT_INSUFFICIENT:
        return AUDIO_CUE_REJECT;
    default:
        return AUDIO_CUE_WARNING;
    }
}

static reader_result_t handle_event(const reader_evt_t *evt)
{
    switch (evt->type) {
    case EVT_PRESENT_CARD: {
        const reader_card_t *c = &evt->card;
        if (c->uid_len == 0) {
            return charge(c->balance_cents, c->discount, c->tagged_on);
        }
        xSemaphoreTake(s_accounts_lock, portMAX_DELAY);
        reader_account_t *acc = account_for(c->uid, c->uid_len);
        reader_result_t r = charge(acc->balance_cents, false, acc->tagged_on);
        acc->balance_cents = r.balance_cents;
        acc->tagged_on = r.tagged_on;
        xSemaphoreGive(s_accounts_lock);
        memcpy(r.uid, c->uid, c->uid_len);
        r.uid_len = c->uid_len;
        return r;
    }
    case EVT_TAP_VIRTUAL: {
        reader_result_t r = charge(s_virtual_balance, g_settings.discount, s_virtual_tagged_on);
        s_virtual_balance = r.balance_cents;
        s_virtual_tagged_on = r.tagged_on;
        return r;
    }
    case EVT_FORCE_RESULT:
    default:
        return (reader_result_t){
            .kind = evt->forced,
            .fare_cents = fare_for(g_settings.discount),
            .balance_cents = s_virtual_balance,
            .discount = g_settings.discount,
            .tagged_on = s_virtual_tagged_on,
        };
    }
}

static void reader_task(void *arg)
{
    reader_evt_t evt;
    bool showing_result = false;

    while (true) {
        TickType_t wait = pdMS_TO_TICKS(showing_result ? g_settings.result_ms : LOCATOR_PERIOD_MS);

        if (xQueueReceive(s_queue, &evt, wait) == pdTRUE) {
            reader_result_t r = handle_event(&evt);
            ESP_LOGI(TAG, "result=%d fare=%ld balance=%ld", r.kind, (long)r.fare_cents,
                     (long)r.balance_cents);
            /* Sound first: the beep is the rider's primary feedback. */
            audio_play(cue_for(r.kind));
            ui_show_result(&r);
            showing_result = true;
        } else if (showing_result) {
            ui_show_idle();
            showing_result = false;
        } else if (g_settings.locator_tone) {
            audio_play(AUDIO_CUE_LOCATOR);
        }
    }
}

esp_err_t reader_init(void)
{
    s_accounts_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_accounts_lock, ESP_ERR_NO_MEM, TAG, "lock");
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

int32_t reader_virtual_balance(void)
{
    return s_virtual_balance;
}

void reader_set_virtual_balance(int32_t cents)
{
    s_virtual_balance = cents;
}
