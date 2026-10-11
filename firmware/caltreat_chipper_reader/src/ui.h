#pragma once

#include "reader.h"

/* Build all screens and the debug overlay. Call once after board_init(). */
void ui_init(void);

/* Safe to call from any task (takes the LVGL lock). */
void ui_show_idle(void);
void ui_show_result(const reader_result_t *result);

/* Open/close the debug sheet (also reachable via the on-screen gear button). */
void ui_toggle_debug(void);
