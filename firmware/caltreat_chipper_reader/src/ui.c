/*
 * Chipper reader screen (portrait 480x800, modelled on the next-generation
 * Clipper validator as used on Caltrain) plus a slide-up debug sheet.
 *
 *   idle    navy (Caltrain): logo + wordmark, hand-with-card icon, "TAG HERE",
 *           kiosk ID and clock bottom left
 *   result  logo-less full-screen card: bold two-line message at the top,
 *           outlined icon in the middle, lighter text below: "FARE $x.xx" and
 *           "BAL $x.xx" when accepted (navy), the reason when rejected (orange).
 */
#include "ui.h"

#include <stdio.h>
#include "esp_lvgl_port.h"

#include "assets.h"
#include "board.h"
#include "clock.h"
#include "net.h"
#include "settings.h"

/* Background colours (docs/screens.md). Caltrain theme: navy idle and
 * accepted (bus validators use black / blue), orange rejected. */
#define CLR_SCREEN   lv_color_hex(0x121A30)   /* idle */
#define CLR_ACCEPT   CLR_SCREEN               /* "TRAVEL OK": Caltrain navy, as idle */
#define CLR_REJECT   lv_color_hex(0xE57200)   /* "TRAVEL NOT OK": Clipper brand orange PMS 152C */
#define CLR_WHITE    lv_color_hex(0xFFFFFF)
#define CLR_INK      lv_color_hex(0x111111)
#define CLR_GREEN    lv_color_hex(0x00A04B)
#define CLR_AMBER    lv_color_hex(0xFFB81C)
#define CLR_RED      lv_color_hex(0xD50032)
#define CLR_SHEET    lv_color_hex(0x111827)
#define CLR_BTN      lv_color_hex(0x374151)

extern const uint8_t montserrat_bold_ttf_start[] asm("_binary_Montserrat_Bold_ttf_start");
extern const uint8_t montserrat_bold_ttf_end[] asm("_binary_Montserrat_Bold_ttf_end");
extern const uint8_t montserrat_regular_ttf_start[] asm("_binary_Montserrat_Regular_ttf_start");
extern const uint8_t montserrat_regular_ttf_end[] asm("_binary_Montserrat_Regular_ttf_end");

static lv_font_t *s_font_tag;     /* "TAG HERE" */
static lv_font_t *s_font_head;    /* result message, e.g. "TRAVEL / NOT OK" */
static lv_font_t *s_font_detail;  /* under the icon: "FARE $x.xx" or a reason, e.g. "EXPIRED / CARD" */

/* idle layer */
static lv_obj_t *s_idle;
static lv_obj_t *s_clock;

/* result layer */
static lv_obj_t *s_result_layer;
static lv_obj_t *s_headline;
static lv_obj_t *s_glyph;
static lv_obj_t *s_detail;
static lv_obj_t *s_balance;

/* firmware update in progress (covers both layers above) */
static lv_obj_t *s_updating;

/* debug sheet */
static lv_obj_t *s_dbg_btn;
static lv_obj_t *s_dbg_sheet;
static lv_obj_t *s_last_card;

static bool s_showing_result;
static reader_result_t s_result;

/* ------------------------------------------------------------------ */
/* Result screens                                                      */
/* ------------------------------------------------------------------ */

static void fmt_money(char *buf, size_t n, int32_t cents)
{
    snprintf(buf, n, "$%ld.%02ld", (long)(cents / 100), (long)(cents % 100));
}

typedef struct {
    lv_color_t bg;
    const char *headline;
    const lv_image_dsc_t *icon;
    char detail[40];          /* "FARE $x.xx" when accepted, reason when rejected */
    char balance[24];         /* "BAL $x.xx", accepted only */
} result_style_t;

/*
 * Accepted screens follow the real "TRAVEL OK / FARE $x.xx / BAL $x.xx" layout
 * (Clipper Google Pay video 0:35, NBD 878 s); tag off shows the "FARE $0.00"
 * form (NBD 1119 s). Rejected screens follow "TRAVEL NOT OK / <reason>" (U4).
 */
static result_style_t result_style(const reader_result_t *r)
{
    result_style_t st = {.headline = "TRAVEL\nOK", .icon = &img_icon_check, .bg = CLR_ACCEPT};
    char money[16];
    if (r->rides_left >= 0) {   /* Chipper card: rides, not money */
        switch (r->kind) {
        case READER_RESULT_OK:
            snprintf(st.detail, sizeof(st.detail), "TAGGED ON");
            return st;
        case READER_RESULT_TAG_OFF:
            snprintf(st.detail, sizeof(st.detail), "TAGGED OFF");
            snprintf(st.balance, sizeof(st.balance), "RIDES LEFT %d", r->rides_left);
            return st;
        case READER_RESULT_ALREADY_TAPPED:
            snprintf(st.detail, sizeof(st.detail), "ALREADY\nTAPPED ON");
            return st;
        case READER_RESULT_INSUFFICIENT:
            return (result_style_t){.bg = CLR_REJECT, .headline = "TRAVEL\nNOT OK", .icon = &img_icon_prohibited,
                                    .detail = "NO RIDES\nLEFT"};
        case READER_RESULT_NOT_CHIPPER:
            return (result_style_t){.bg = CLR_REJECT, .headline = "TRAVEL\nNOT OK", .icon = &img_icon_prohibited,
                                    .detail = "NOT A CHIPPER\nCARD"};
        default:
            break;   /* card error: as below */
        }
    }
    switch (r->kind) {
    case READER_RESULT_OK:
    case READER_RESULT_LOW_BALANCE:
    case READER_RESULT_TAG_OFF:
        fmt_money(money, sizeof(money), r->kind == READER_RESULT_TAG_OFF ? 0 : r->fare_cents);
        snprintf(st.detail, sizeof(st.detail), "FARE %s", money);
        fmt_money(money, sizeof(money), r->balance_cents);
        snprintf(st.balance, sizeof(st.balance), "BAL %s", money);
        break;
    case READER_RESULT_INSUFFICIENT:
        st = (result_style_t){.bg = CLR_REJECT, .headline = "TRAVEL\nNOT OK", .icon = &img_icon_prohibited,
                              .detail = "INSUFFICIENT\nFUNDS"};
        break;
    case READER_RESULT_CARD_ERROR:
    default:
        st = (result_style_t){.bg = CLR_REJECT, .headline = "TRAVEL\nNOT OK", .icon = &img_icon_prohibited,
                              .detail = "PLEASE\nTAP AGAIN"};
        break;
    }
    return st;
}

/* ------------------------------------------------------------------ */
/* Reader screen                                                       */
/* ------------------------------------------------------------------ */

static lv_obj_t *bare_obj(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    return o;
}

static lv_obj_t *full_screen_layer(lv_obj_t *parent, lv_color_t bg)
{
    lv_obj_t *o = bare_obj(parent);
    lv_obj_set_size(o, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(o, bg, 0);
    return o;
}

static lv_obj_t *mask_image(lv_obj_t *parent, const lv_image_dsc_t *src)
{
    lv_obj_t *img = lv_image_create(parent);
    lv_image_set_src(img, src);
    lv_obj_set_style_image_recolor_opa(img, LV_OPA_COVER, 0);
    lv_obj_set_style_image_recolor(img, CLR_WHITE, 0);
    return img;
}

static lv_obj_t *centered_label(lv_obj_t *parent, const lv_font_t *font)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_width(l, 460);
    lv_label_set_long_mode(l, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, CLR_WHITE, 0);
    return l;
}

static void clock_tick_cb(lv_timer_t *t)
{
    char buf[32];
    clock_format(buf, sizeof(buf));
    lv_label_set_text(s_clock, buf);
}

static void create_idle_layer(lv_obj_t *scr)
{
    s_idle = full_screen_layer(scr, CLR_SCREEN);

    /* logo lockup: mark with the wordmark beside its middle */
    lv_obj_t *logo = bare_obj(s_idle);
    lv_obj_set_size(logo, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(logo, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(logo, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(logo, 18, 0);
    lv_obj_align(logo, LV_ALIGN_TOP_MID, 0, 56);
    mask_image(logo, &img_chipper_mark);
    mask_image(logo, &img_chipper_wordmark);

    lv_obj_t *tap = mask_image(s_idle, &img_icon_tap);
    lv_obj_align(tap, LV_ALIGN_TOP_MID, 0, 270);

    lv_obj_t *tag = centered_label(s_idle, s_font_tag);
    lv_label_set_text(tag, "TAG HERE");
    lv_obj_align(tag, LV_ALIGN_CENTER, 0, 200);

    /* The reader's kiosk ID, as the backend lists it ("chipper-reader-<factory MAC>"),
     * left-aligned above the clock. */
    lv_obj_t *id = lv_label_create(s_idle);
    lv_obj_set_style_text_font(id, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(id, CLR_WHITE, 0);
    lv_label_set_text(id, net_hostname());
    lv_obj_align(id, LV_ALIGN_BOTTOM_LEFT, 22, -52);

    s_clock = lv_label_create(s_idle);
    lv_obj_set_style_text_font(s_clock, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_clock, CLR_WHITE, 0);
    lv_obj_align(s_clock, LV_ALIGN_BOTTOM_LEFT, 22, -22);
    lv_timer_create(clock_tick_cb, 1000, NULL);
    clock_tick_cb(NULL);
}

static void create_result_layer(lv_obj_t *scr)
{
    /* Proportions from photos of the validator: message in the top third,
     * icon centred just below the middle, reason text in the lower quarter. */
    s_result_layer = full_screen_layer(scr, CLR_ACCEPT);

    s_headline = centered_label(s_result_layer, s_font_head);
    lv_obj_set_style_text_line_space(s_headline, -6, 0);
    lv_obj_align(s_headline, LV_ALIGN_TOP_MID, 0, 70);

    s_glyph = mask_image(s_result_layer, &img_icon_check);
    lv_obj_align(s_glyph, LV_ALIGN_TOP_MID, 0, 330);

    s_detail = centered_label(s_result_layer, s_font_detail);
    lv_obj_set_style_text_line_space(s_detail, 2, 0);

    s_balance = centered_label(s_result_layer, s_font_detail);
    lv_obj_align(s_balance, LV_ALIGN_TOP_MID, 0, 650);
}

static void create_reader_screen(void)
{
    const size_t bold_len = montserrat_bold_ttf_end - montserrat_bold_ttf_start;
    const size_t regular_len = montserrat_regular_ttf_end - montserrat_regular_ttf_start;
    s_font_tag = lv_tiny_ttf_create_data(montserrat_bold_ttf_start, bold_len, 76);
    s_font_head = lv_tiny_ttf_create_data(montserrat_bold_ttf_start, bold_len, 84);
    s_font_detail = lv_tiny_ttf_create_data(montserrat_regular_ttf_start, regular_len, 46);

    lv_obj_t *scr = lv_screen_active();
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(scr, CLR_SCREEN, 0);

    create_idle_layer(scr);
    create_result_layer(scr);

    s_updating = full_screen_layer(scr, CLR_SCREEN);
    lv_obj_t *msg = centered_label(s_updating, s_font_detail);
    lv_label_set_text(msg, "UPDATING\nFIRMWARE\n\nPLEASE WAIT");
    lv_obj_center(msg);
    lv_obj_add_flag(s_updating, LV_OBJ_FLAG_HIDDEN);
}

/* Must hold the LVGL lock. */
static void render(void)
{
    if (s_showing_result) {
        result_style_t st = result_style(&s_result);
        lv_obj_set_style_bg_color(s_result_layer, st.bg, 0);
        lv_label_set_text(s_headline, st.headline);
        lv_image_set_src(s_glyph, st.icon);
        lv_label_set_text(s_detail, st.detail);
        lv_label_set_text(s_balance, st.balance);
        /* fare sits right under the icon; a two-line reason a little lower */
        lv_obj_align(s_detail, LV_ALIGN_TOP_MID, 0, st.balance[0] ? 515 : 560);
    }
    lv_obj_set_flag(s_idle, LV_OBJ_FLAG_HIDDEN, s_showing_result);
    lv_obj_set_flag(s_result_layer, LV_OBJ_FLAG_HIDDEN, !s_showing_result);
}

/* ------------------------------------------------------------------ */
/* Debug sheet                                                         */
/* ------------------------------------------------------------------ */

typedef enum { FMT_MONEY, FMT_PCT, FMT_SEC } fmt_t;

typedef enum {
    SL_BALANCE,
    SL_FARE,
    SL_LOW,
    SL_VOLUME,
    SL_BRIGHTNESS,
    SL_RESULT_TIME,
    SL_COUNT,
} slider_id_t;

typedef struct {
    const char *name;
    int32_t min, max;   /* actual-value range */
    int32_t step;       /* actual units per slider tick */
    fmt_t fmt;
} slider_def_t;

static const slider_def_t SLIDERS[SL_COUNT] = {
    [SL_BALANCE]     = {"Virtual card balance",        0, 5000,  25, FMT_MONEY},
    [SL_FARE]        = {"Adult fare",          0, 1500,   5, FMT_MONEY},
    [SL_LOW]         = {"Low-balance warning", 0, 1000,  25, FMT_MONEY},
    [SL_VOLUME]      = {"Volume",              0, 100,    1, FMT_PCT},
    [SL_BRIGHTNESS]  = {"Brightness",          5, 100,    1, FMT_PCT},
    [SL_RESULT_TIME] = {"Result hold time",  500, 8000, 250, FMT_SEC},
};

static lv_obj_t *s_slider[SL_COUNT];
static lv_obj_t *s_slider_label[SL_COUNT];

static int32_t slider_get_actual(slider_id_t id)
{
    switch (id) {
    case SL_BALANCE:     return reader_virtual_balance();
    case SL_FARE:        return g_settings.fare_cents;
    case SL_LOW:         return g_settings.low_balance_cents;
    case SL_VOLUME:      return g_settings.volume;
    case SL_BRIGHTNESS:  return g_settings.brightness;
    case SL_RESULT_TIME: return g_settings.result_ms;
    default:             return 0;
    }
}

static void slider_set_actual(slider_id_t id, int32_t v)
{
    switch (id) {
    case SL_BALANCE:     reader_set_virtual_balance(v); break;
    case SL_FARE:        g_settings.fare_cents = v; break;
    case SL_LOW:         g_settings.low_balance_cents = v; break;
    case SL_VOLUME:      g_settings.volume = v; break;
    case SL_BRIGHTNESS:
        g_settings.brightness = v;
        board_set_backlight(v);
        break;
    case SL_RESULT_TIME: g_settings.result_ms = v; break;
    default: break;
    }
}

static void slider_update_label(slider_id_t id)
{
    const slider_def_t *d = &SLIDERS[id];
    int32_t v = slider_get_actual(id);
    char val[16];
    switch (d->fmt) {
    case FMT_MONEY: fmt_money(val, sizeof(val), v); break;
    case FMT_PCT:   snprintf(val, sizeof(val), "%ld%%", (long)v); break;
    case FMT_SEC:   snprintf(val, sizeof(val), "%ld.%02ld s", (long)(v / 1000), (long)(v % 1000 / 10)); break;
    }
    lv_label_set_text_fmt(s_slider_label[id], "%s:  %s", d->name, val);
}

static void slider_cb(lv_event_t *e)
{
    slider_id_t id = (slider_id_t)(intptr_t)lv_event_get_user_data(e);
    int32_t ticks = lv_slider_get_value(lv_event_get_target_obj(e));
    slider_set_actual(id, ticks * SLIDERS[id].step);
    slider_update_label(id);
}

static void slider_sync(slider_id_t id)
{
    lv_slider_set_value(s_slider[id], slider_get_actual(id) / SLIDERS[id].step, LV_ANIM_OFF);
    slider_update_label(id);
}

static void add_slider(lv_obj_t *parent, slider_id_t id)
{
    const slider_def_t *d = &SLIDERS[id];
    lv_obj_t *lbl = lv_label_create(parent);
    lv_obj_set_style_text_color(lbl, CLR_WHITE, 0);

    lv_obj_t *sl = lv_slider_create(parent);
    lv_obj_set_width(sl, lv_pct(92));
    lv_obj_set_style_margin_left(sl, 12, 0);
    lv_obj_set_style_margin_bottom(sl, 8, 0);
    lv_slider_set_range(sl, d->min / d->step, d->max / d->step);
    lv_obj_add_event_cb(sl, slider_cb, LV_EVENT_VALUE_CHANGED, (void *)(intptr_t)id);

    s_slider[id] = sl;
    s_slider_label[id] = lbl;
    slider_sync(id);
}

typedef enum { SW_DISCOUNT, SW_TAG_ON_OFF, SW_LOCATOR, SW_FLIP } switch_id_t;

static void switch_cb(lv_event_t *e)
{
    switch_id_t id = (switch_id_t)(intptr_t)lv_event_get_user_data(e);
    bool on = lv_obj_has_state(lv_event_get_target_obj(e), LV_STATE_CHECKED);
    switch (id) {
    case SW_DISCOUNT:
        g_settings.discount = on;
        break;
    case SW_TAG_ON_OFF:
        g_settings.tag_on_off = on;
        break;
    case SW_LOCATOR:
        g_settings.locator_tone = on;
        break;
    case SW_FLIP:
        g_settings.flip = on;
        board_set_flipped(on);
        break;
    }
}

static void add_switch(lv_obj_t *parent, const char *name, switch_id_t id, bool on)
{
    lv_obj_t *row = bare_obj(parent);
    lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_ver(row, 4, 0);

    lv_obj_t *lbl = lv_label_create(row);
    lv_label_set_text(lbl, name);
    lv_obj_set_style_text_color(lbl, CLR_WHITE, 0);

    lv_obj_t *sw = lv_switch_create(row);
    if (on) {
        lv_obj_add_state(sw, LV_STATE_CHECKED);
    }
    lv_obj_add_event_cb(sw, switch_cb, LV_EVENT_VALUE_CHANGED, (void *)(intptr_t)id);
}

static void add_section(lv_obj_t *parent, const char *title)
{
    lv_obj_t *lbl = lv_label_create(parent);
    lv_label_set_text(lbl, title);
    lv_obj_set_style_text_font(lbl, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(lbl, lv_color_hex(0x93C5FD), 0);
    lv_obj_set_style_pad_top(lbl, 8, 0);
}

typedef enum {
    ACT_TAP,
    ACT_FORCE_OK,
    ACT_FORCE_LOW,
    ACT_FORCE_TAG_OFF,
    ACT_FORCE_INSUFFICIENT,
    ACT_FORCE_ERROR,
    ACT_ADD_VALUE,
} action_t;

static void action_cb(lv_event_t *e)
{
    switch ((action_t)(intptr_t)lv_event_get_user_data(e)) {
    case ACT_TAP:                reader_tap_virtual_card(); break;
    case ACT_FORCE_OK:           reader_force_result(READER_RESULT_OK); break;
    case ACT_FORCE_LOW:          reader_force_result(READER_RESULT_LOW_BALANCE); break;
    case ACT_FORCE_TAG_OFF:      reader_force_result(READER_RESULT_TAG_OFF); break;
    case ACT_FORCE_INSUFFICIENT: reader_force_result(READER_RESULT_INSUFFICIENT); break;
    case ACT_FORCE_ERROR:        reader_force_result(READER_RESULT_CARD_ERROR); break;
    case ACT_ADD_VALUE: {
        int32_t b = reader_virtual_balance() + 2000;
        reader_set_virtual_balance(b > SLIDERS[SL_BALANCE].max ? SLIDERS[SL_BALANCE].max : b);
        slider_sync(SL_BALANCE);
        break;
    }
    }
}

static lv_obj_t *add_button(lv_obj_t *parent, const char *text, action_t act, lv_color_t color,
                            int32_t width_pct)
{
    lv_obj_t *btn = lv_button_create(parent);
    lv_obj_set_size(btn, lv_pct(width_pct), 48);
    lv_obj_set_style_bg_color(btn, color, 0);
    lv_obj_add_event_cb(btn, action_cb, LV_EVENT_CLICKED, (void *)(intptr_t)act);
    lv_obj_t *lbl = lv_label_create(btn);
    lv_label_set_text(lbl, text);
    lv_obj_center(lbl);
    return btn;
}

static void toggle_debug_cb(lv_event_t *e)
{
    bool open = lv_obj_has_flag(s_dbg_sheet, LV_OBJ_FLAG_HIDDEN);
    if (open) {
        slider_sync(SL_BALANCE);
        lv_obj_scroll_to_y(s_dbg_sheet, 0, LV_ANIM_OFF);
    }
    lv_obj_set_flag(s_dbg_sheet, LV_OBJ_FLAG_HIDDEN, !open);
    lv_obj_set_flag(s_dbg_btn, LV_OBJ_FLAG_HIDDEN, open);
}

static void create_debug_sheet(void)
{
    lv_obj_t *top = lv_layer_top();

    s_dbg_btn = lv_button_create(top);
    lv_obj_set_size(s_dbg_btn, 56, 56);
    lv_obj_align(s_dbg_btn, LV_ALIGN_BOTTOM_RIGHT, -10, -10);
    lv_obj_set_style_radius(s_dbg_btn, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_dbg_btn, lv_color_hex(0x808080), 0);
    lv_obj_set_style_bg_opa(s_dbg_btn, LV_OPA_30, 0);
    lv_obj_set_style_shadow_width(s_dbg_btn, 0, 0);
    lv_obj_add_event_cb(s_dbg_btn, toggle_debug_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *icon = lv_label_create(s_dbg_btn);
    lv_label_set_text(icon, LV_SYMBOL_SETTINGS);
    lv_obj_set_style_text_font(icon, &lv_font_montserrat_20, 0);
    lv_obj_center(icon);

    /* Bottom sheet; the top of the reader screen stays visible above it. */
    s_dbg_sheet = lv_obj_create(top);
    lv_obj_set_size(s_dbg_sheet, lv_pct(100), 560);
    lv_obj_align(s_dbg_sheet, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_radius(s_dbg_sheet, 16, 0);
    lv_obj_set_style_border_width(s_dbg_sheet, 0, 0);
    lv_obj_set_style_bg_color(s_dbg_sheet, CLR_SHEET, 0);
    lv_obj_set_style_bg_opa(s_dbg_sheet, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(s_dbg_sheet, 14, 0);
    lv_obj_set_style_pad_row(s_dbg_sheet, 8, 0);
    lv_obj_set_flex_flow(s_dbg_sheet, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_flex_align(s_dbg_sheet, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START);
    lv_obj_set_scroll_dir(s_dbg_sheet, LV_DIR_VER);
    lv_obj_add_flag(s_dbg_sheet, LV_OBJ_FLAG_HIDDEN);

    /* header */
    lv_obj_t *title = lv_label_create(s_dbg_sheet);
    lv_label_set_text(title, "Reader debug");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(title, CLR_WHITE, 0);
    lv_obj_t *close = lv_button_create(s_dbg_sheet);
    lv_obj_set_size(close, 48, 40);
    lv_obj_set_style_bg_color(close, CLR_BTN, 0);
    lv_obj_add_event_cb(close, toggle_debug_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *x = lv_label_create(close);
    lv_label_set_text(x, LV_SYMBOL_DOWN);
    lv_obj_center(x);

    /* events */
    lv_obj_t *tap = add_button(s_dbg_sheet, LV_SYMBOL_WIFI "  Tap virtual card", ACT_TAP,
                               lv_color_hex(0x0B5CAB), 100);
    lv_obj_add_flag(tap, LV_OBJ_FLAG_FLEX_IN_NEW_TRACK);
    add_button(s_dbg_sheet, "Force OK", ACT_FORCE_OK, CLR_GREEN, 48);
    lv_obj_t *low = add_button(s_dbg_sheet, "Force low balance", ACT_FORCE_LOW, CLR_AMBER, 48);
    lv_obj_set_style_text_color(low, CLR_INK, 0);
    add_button(s_dbg_sheet, "Insufficient", ACT_FORCE_INSUFFICIENT, CLR_RED, 48);
    add_button(s_dbg_sheet, "Card error", ACT_FORCE_ERROR, lv_color_hex(0x8A0020), 48);
    add_button(s_dbg_sheet, "Force tag off", ACT_FORCE_TAG_OFF, lv_color_hex(0x0E7490), 100);

    /* Everything below is one item per row. */
    lv_obj_t *col = bare_obj(s_dbg_sheet);
    lv_obj_set_size(col, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 6, 0);
    lv_obj_add_flag(col, LV_OBJ_FLAG_FLEX_IN_NEW_TRACK);

    add_section(col, "Card & fare");
    s_last_card = lv_label_create(col);
    lv_label_set_text(s_last_card, "Last card: none tapped yet");
    lv_obj_set_style_text_color(s_last_card, CLR_WHITE, 0);
    add_slider(col, SL_BALANCE);
    add_button(col, "+ $20.00 value", ACT_ADD_VALUE, CLR_BTN, 100);
    add_slider(col, SL_FARE);
    add_slider(col, SL_LOW);
    add_switch(col, "Discount card (half fare)", SW_DISCOUNT, g_settings.discount);
    add_switch(col, "Caltrain tag on / tag off", SW_TAG_ON_OFF, g_settings.tag_on_off);

    add_section(col, "Audio");
    add_slider(col, SL_VOLUME);
    add_switch(col, "Locator tone every 15 s", SW_LOCATOR, g_settings.locator_tone);

    add_section(col, "Display");
    add_slider(col, SL_BRIGHTNESS);
    add_slider(col, SL_RESULT_TIME);
    add_switch(col, "Flip screen 180 deg", SW_FLIP, g_settings.flip);
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

void ui_init(void)
{
    lvgl_port_lock(0);
    create_reader_screen();
    create_debug_sheet();
    render();
    lvgl_port_unlock();
}

void ui_show_idle(void)
{
    lvgl_port_lock(0);
    s_showing_result = false;
    render();
    lvgl_port_unlock();
}

void ui_show_result(const reader_result_t *result)
{
    lvgl_port_lock(0);
    s_result = *result;
    s_showing_result = true;
    render();
    slider_sync(SL_BALANCE);
    if (result->uid_len) {
        char uid[31] = "";
        for (int i = 0; i < result->uid_len; i++) {
            int at = i ? 3 * i - 1 : 0;   /* "AA:BB:..." */
            snprintf(uid + at, sizeof(uid) - at, i ? ":%02X" : "%02X", result->uid[i]);
        }
        lv_label_set_text_fmt(s_last_card, "Last card: %s\n%d ride(s) left", uid, result->rides_left);
    }
    lvgl_port_unlock();
}

void ui_show_updating(bool active)
{
    lvgl_port_lock(0);
    lv_obj_set_flag(s_updating, LV_OBJ_FLAG_HIDDEN, !active);
    lvgl_port_unlock();
}

void ui_toggle_debug(void)
{
    lvgl_port_lock(0);
    toggle_debug_cb(NULL);
    lvgl_port_unlock();
}
