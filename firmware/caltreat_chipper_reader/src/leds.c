#include "leds.h"

#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "leds";

/* WS2812 values: "yellow" needs less green than (255, 255, 0) to read as yellow. */
static const uint8_t COLORS[][3] = {
    [LEDS_GREEN]  = {0, 255, 0},
    [LEDS_YELLOW] = {255, 150, 0},
    [LEDS_RED]    = {255, 0, 0},
};
#define FLASH_MS 1000

static led_strip_handle_t s_strip;
static QueueHandle_t s_queue;

esp_err_t leds_fill(uint8_t r, uint8_t g, uint8_t b)
{
    for (int i = 0; i < LEDS_COUNT; i++) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_strip, i, r, g, b), TAG, "set pixel");
    }
    return led_strip_refresh(s_strip);
}

static void fill_color(leds_color_t c)
{
    leds_fill(COLORS[c][0], COLORS[c][1], COLORS[c][2]);
}

static void leds_task(void *arg)
{
    leds_color_t color;
    bool pending = false;
    for (;;) {
        if (!pending && xQueueReceive(s_queue, &color, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        fill_color(color);
        /* a new tap during the flash restarts it in the new colour */
        pending = xQueueReceive(s_queue, &color, pdMS_TO_TICKS(FLASH_MS)) == pdTRUE;
        if (!pending) {
            leds_fill(0, 0, 0);
        }
    }
}

void leds_flash(leds_color_t color)
{
    if (s_queue && xQueueSend(s_queue, &color, 0) != pdTRUE) {
        ESP_LOGW(TAG, "flash dropped");
    }
}

esp_err_t leds_init(void)
{
    const led_strip_config_t strip_cfg = {
        .strip_gpio_num = LEDS_GPIO,
        .max_leds = LEDS_COUNT,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    const led_strip_rmt_config_t rmt_cfg = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
    };
    ESP_RETURN_ON_ERROR(led_strip_new_rmt_device(&strip_cfg, &rmt_cfg, &s_strip), TAG, "led strip");
    ESP_RETURN_ON_ERROR(leds_fill(0, 0, 0), TAG, "off");
    s_queue = xQueueCreate(4, sizeof(leds_color_t));
    ESP_RETURN_ON_FALSE(s_queue, ESP_ERR_NO_MEM, TAG, "queue");
    /* Above the reader task (5), so the flash starts with the beep. */
    BaseType_t ok = xTaskCreate(leds_task, "leds", 3072, NULL, 6, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}
