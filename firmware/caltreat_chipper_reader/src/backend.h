/*
 * HTTPS client for the CalTreat backend (CONFIG_CHIPPER_BACKEND_URL). One
 * keep-alive connection shared by every caller, one request at a time.
 * Blocking: never call it from the tap path.
 */
#pragma once

#include <stddef.h>
#include "esp_err.h"
#include "esp_http_client.h"

esp_err_t backend_init(void);

/*
 * method + path (e.g. "/api/taps/"), JSON body or NULL. ESP_OK once an HTTP
 * response arrived, whatever its status (*status). body, if given, gets up
 * to body_len - 1 bytes of the response, NUL-terminated.
 */
esp_err_t backend_request(esp_http_client_method_t method, const char *path, const char *json,
                          int *status, char *body, size_t body_len);
