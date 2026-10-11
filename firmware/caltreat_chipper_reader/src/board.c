#include "board.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_touch_gt911.h"
#include "esp_lvgl_port.h"
#include "esp_heap_caps.h"
#include "freertos/semphr.h"
#include "driver/ppa.h"

static const char *TAG = "board";

#define I2C_PORT        0
#define I2C_GPIO_SDA    45
#define I2C_GPIO_SCL    46

#define TOUCH_GPIO_RST  36
#define TOUCH_GPIO_INT  42

/* STC8H1K co-processor: I2C register interface for backlight, amp, etc. */
#define STC8_ADDR             0x2F
#define STC8_REG_SET_GPIO     0x18
#define STC8_REG_SET_PWM      0x20
#define STC8_GPIO_AUDIO_SD    2  /* amp shutdown, active low */
#define STC8_GPIO_LCD_BL_PWR  3
#define STC8_PWM_LCD_BL       0

#define LCD_PCLK_HZ     (18 * 1000 * 1000)

static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_stc8;
static esp_lcd_panel_handle_t s_panel;
static esp_lcd_touch_handle_t s_touch;
static lv_display_t *s_disp;

static esp_err_t stc8_write(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(s_stc8, buf, sizeof(buf), 100);
}

static esp_err_t i2c_bus_init(void)
{
    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_PORT,
        .sda_io_num = I2C_GPIO_SDA,
        .scl_io_num = I2C_GPIO_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_i2c_bus), TAG, "i2c bus");

    const i2c_device_config_t stc8_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = STC8_ADDR,
        .scl_speed_hz = 400000,
    };
    return i2c_master_bus_add_device(s_i2c_bus, &stc8_cfg, &s_stc8);
}

static esp_err_t touch_init(void)
{
    esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_GT911_CONFIG();
    io_cfg.scl_speed_hz = 400000;

    esp_lcd_touch_io_gt911_config_t gt911_cfg = {
        .dev_addr = io_cfg.dev_addr,
    };
    const esp_lcd_touch_config_t tp_cfg = {
        .x_max = BOARD_LCD_H_RES,
        .y_max = BOARD_LCD_V_RES,
        .rst_gpio_num = TOUCH_GPIO_RST,
        .int_gpio_num = TOUCH_GPIO_INT,
        .levels = {.reset = 0, .interrupt = 0},
        .driver_data = &gt911_cfg,
    };

    esp_lcd_panel_io_handle_t io = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(s_i2c_bus, &io_cfg, &io), TAG, "touch io");
    if (esp_lcd_touch_new_i2c_gt911(io, &tp_cfg, &s_touch) == ESP_OK) {
        return ESP_OK;
    }

    /* GT911 latches one of two addresses at reset; retry with the backup. */
    ESP_LOGW(TAG, "GT911 not at 0x%02x, trying backup address", io_cfg.dev_addr);
    esp_lcd_panel_io_del(io);
    io_cfg.dev_addr = ESP_LCD_TOUCH_IO_I2C_GT911_ADDRESS_BACKUP;
    gt911_cfg.dev_addr = io_cfg.dev_addr;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(s_i2c_bus, &io_cfg, &io), TAG, "touch io");
    return esp_lcd_touch_new_i2c_gt911(io, &tp_cfg, &s_touch);
}

static esp_err_t lcd_init(void)
{
    const esp_lcd_rgb_panel_config_t panel_cfg = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .data_width = 16,
        .num_fbs = 2,
        .dma_burst_size = 64,
        .disp_gpio_num = -1,
        .pclk_gpio_num = 3,
        .vsync_gpio_num = 41,
        .hsync_gpio_num = 40,
        .de_gpio_num = 2,
        .data_gpio_nums = {8, 7, 6, 5, 4, 14, 13, 12, 11, 10, 9, 19, 18, 17, 16, 15},
        .timings = {
            .pclk_hz = LCD_PCLK_HZ,
            .h_res = BOARD_LCD_H_RES,
            .v_res = BOARD_LCD_V_RES,
            .hsync_pulse_width = 4,
            .hsync_back_porch = 8,
            .hsync_front_porch = 8,
            .vsync_pulse_width = 4,
            .vsync_back_porch = 16,
            .vsync_front_porch = 16,
            .flags = {
                .pclk_active_neg = true,
                .pclk_idle_high = true,
            },
        },
        .flags.fb_in_psram = true,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_rgb_panel(&panel_cfg, &s_panel), TAG, "rgb panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG, "panel reset");
    return esp_lcd_panel_init(s_panel);
}

/* ------------------------------------------------------------------ */
/* Display pipeline                                                    */
/*                                                                     */
/* LVGL renders a portrait 480x800 image (direct mode, one PSRAM       */
/* buffer). On each refresh the PPA (pixel-processing accelerator)     */
/* rotates just the dirty rectangles into the back RGB frame buffer,   */
/* then the two frame buffers are swapped at end-of-frame, so the CPU  */
/* never touches the frame buffers and nothing tears.                  */
/* ------------------------------------------------------------------ */

#define MAX_DIRTY 24

typedef struct {
    lv_area_t areas[MAX_DIRTY];
    int count;
    bool full;      /* too many areas: copy the whole screen */
} dirty_set_t;

static void *s_fb[2];
static int s_back;                      /* index of the frame buffer not being scanned out */
static uint16_t *s_lv_buf;              /* LVGL's portrait render target */
static ppa_client_handle_t s_ppa;
static SemaphoreHandle_t s_frame_done;
static dirty_set_t s_dirty[2];          /* [0] this refresh, [1] previous refresh */
static volatile bool s_flipped;

static IRAM_ATTR bool on_frame_done(esp_lcd_panel_handle_t panel, const esp_lcd_rgb_panel_event_data_t *e,
                                    void *ctx)
{
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_frame_done, &woken);
    return woken == pdTRUE;
}

static void dirty_add(dirty_set_t *d, const lv_area_t *a)
{
    if (d->full) {
        return;
    }
    if (d->count == MAX_DIRTY) {
        d->full = true;
        return;
    }
    d->areas[d->count++] = *a;
}

/* Rotate one portrait rectangle of the LVGL buffer into the back frame buffer. */
static void ppa_blit(const lv_area_t *a)
{
    const uint32_t w = lv_area_get_width(a), h = lv_area_get_height(a);
    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = s_lv_buf,
            .pic_w = BOARD_UI_H_RES,
            .pic_h = BOARD_UI_V_RES,
            .block_w = w,
            .block_h = h,
            .block_offset_x = a->x1,
            .block_offset_y = a->y1,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = s_fb[s_back],
            .buffer_size = BOARD_LCD_H_RES * BOARD_LCD_V_RES * 2,
            .pic_w = BOARD_LCD_H_RES,
            .pic_h = BOARD_LCD_V_RES,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .scale_x = 1.0f,
        .scale_y = 1.0f,
        .mode = PPA_TRANS_MODE_BLOCKING,
    };
    /* Rotation is counter-clockwise. Portrait (x, y) lands at:
     *    90: (y, 479 - x)        270: (799 - y, x)                        */
    if (!s_flipped) {
        op.rotation_angle = PPA_SRM_ROTATION_ANGLE_90;
        op.out.block_offset_x = a->y1;
        op.out.block_offset_y = BOARD_UI_H_RES - 1 - a->x2;
    } else {
        op.rotation_angle = PPA_SRM_ROTATION_ANGLE_270;
        op.out.block_offset_x = BOARD_UI_V_RES - 1 - a->y2;
        op.out.block_offset_y = a->x1;
    }
    ESP_ERROR_CHECK(ppa_do_scale_rotate_mirror(s_ppa, &op));
}

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    dirty_add(&s_dirty[0], area);
    if (!lv_display_flush_is_last(disp)) {
        lv_display_flush_ready(disp);
        return;
    }

    /* The back buffer last received the previous refresh's areas before that,
     * so it needs both this refresh's and the previous one's dirty areas. */
    static const lv_area_t full = {0, 0, BOARD_UI_H_RES - 1, BOARD_UI_V_RES - 1};
    if (s_dirty[0].full || s_dirty[1].full) {
        ppa_blit(&full);
    } else {
        for (int k = 1; k >= 0; k--) {
            for (int i = 0; i < s_dirty[k].count; i++) {
                ppa_blit(&s_dirty[k].areas[i]);
            }
        }
    }

    /* Present the back buffer; the driver switches to it at the next frame start. */
    xSemaphoreTake(s_frame_done, 0);
    esp_lcd_panel_draw_bitmap(s_panel, 0, 0, BOARD_LCD_H_RES, BOARD_LCD_V_RES, s_fb[s_back]);
    xSemaphoreTake(s_frame_done, pdMS_TO_TICKS(100));
    s_back ^= 1;

    s_dirty[1] = s_dirty[0];
    s_dirty[0].count = 0;
    s_dirty[0].full = false;
    lv_display_flush_ready(disp);
}

static void touch_read_cb(lv_indev_t *indev, lv_indev_data_t *data)
{
    esp_lcd_touch_point_data_t pt;
    uint8_t n = 0;

    esp_lcd_touch_read_data(s_touch);
    if (esp_lcd_touch_get_data(s_touch, &pt, &n, 1) != ESP_OK || n == 0) {
        data->state = LV_INDEV_STATE_RELEASED;
        return;
    }
    /* Inverse of the rotation in ppa_blit(): landscape panel -> portrait UI. */
    if (!s_flipped) {
        data->point.x = BOARD_UI_H_RES - 1 - pt.y;
        data->point.y = pt.x;
    } else {
        data->point.x = pt.y;
        data->point.y = BOARD_UI_V_RES - 1 - pt.x;
    }
    data->state = LV_INDEV_STATE_PRESSED;
}

static esp_err_t lvgl_init(void)
{
    const ppa_client_config_t ppa_cfg = {
        .oper_type = PPA_OPERATION_SRM,
        .max_pending_trans_num = 1,
    };
    ESP_RETURN_ON_ERROR(ppa_register_client(&ppa_cfg, &s_ppa), TAG, "ppa");

    s_frame_done = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(s_frame_done, ESP_ERR_NO_MEM, TAG, "sem");
    const esp_lcd_rgb_panel_event_callbacks_t cbs = {
        .on_frame_buf_complete = on_frame_done,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_rgb_panel_register_event_callbacks(s_panel, &cbs, NULL), TAG, "rgb cbs");
    ESP_RETURN_ON_ERROR(esp_lcd_rgb_panel_get_frame_buffer(s_panel, 2, &s_fb[0], &s_fb[1]), TAG, "fbs");
    s_back = 1; /* the driver starts out scanning fb 0 */

    const size_t buf_size = BOARD_UI_H_RES * BOARD_UI_V_RES * 2;
    s_lv_buf = heap_caps_aligned_calloc(128, 1, buf_size, MALLOC_CAP_SPIRAM);
    ESP_RETURN_ON_FALSE(s_lv_buf, ESP_ERR_NO_MEM, TAG, "lvgl buffer");

    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    port_cfg.task_priority = 4;
    port_cfg.task_stack = 16 * 1024;
    port_cfg.task_max_sleep_ms = 10;
    port_cfg.timer_period_ms = 2;
    ESP_RETURN_ON_ERROR(lvgl_port_init(&port_cfg), TAG, "lvgl port");

    lvgl_port_lock(0);
    s_disp = lv_display_create(BOARD_UI_H_RES, BOARD_UI_V_RES);
    lv_display_set_color_format(s_disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(s_disp, s_lv_buf, NULL, buf_size, LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(s_disp, flush_cb);

    lv_indev_t *indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, touch_read_cb);
    lv_indev_set_display(indev, s_disp);
    lvgl_port_unlock();
    return ESP_OK;
}

const uint16_t *board_front_framebuffer(void)
{
    return s_fb[s_back ^ 1];
}

void board_set_flipped(bool flipped)
{
    lvgl_port_lock(0);
    s_flipped = flipped;
    /* Every pixel lands somewhere new: redraw and re-blit the whole screen. */
    s_dirty[1].full = true;
    lv_obj_invalidate(lv_screen_active());
    lvgl_port_unlock();
}

esp_err_t board_init(void)
{
    ESP_RETURN_ON_ERROR(i2c_bus_init(), TAG, "i2c");
    vTaskDelay(pdMS_TO_TICKS(200)); /* let the STC8 come up */

    board_set_backlight(0);
    board_set_amp(false);

    ESP_RETURN_ON_ERROR(touch_init(), TAG, "touch");
    ESP_RETURN_ON_ERROR(lcd_init(), TAG, "lcd");
    ESP_RETURN_ON_ERROR(lvgl_init(), TAG, "lvgl");
    ESP_LOGI(TAG, "board up");
    return ESP_OK;
}

esp_err_t board_set_backlight(uint8_t percent)
{
    if (percent > 100) {
        percent = 100;
    }
    ESP_RETURN_ON_ERROR(stc8_write(STC8_REG_SET_GPIO + STC8_GPIO_LCD_BL_PWR, percent > 0), TAG, "bl power");
    return stc8_write(STC8_REG_SET_PWM + STC8_PWM_LCD_BL, percent);
}

esp_err_t board_set_amp(bool on)
{
    return stc8_write(STC8_REG_SET_GPIO + STC8_GPIO_AUDIO_SD, on ? 0 : 1);
}

i2c_master_bus_handle_t board_i2c_bus(void)
{
    return s_i2c_bus;
}
