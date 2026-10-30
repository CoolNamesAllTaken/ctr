#pragma once

#include "esp_err.h"

/* Start the console-UART command reader (see serial_cmd.c for commands). */
esp_err_t serial_cmd_init(void);
