/*
 * PN532 command dispatcher: maps the subset of PN532 host commands that libnfc,
 * nfcpy and adafruit-circuitpython-pn532 use onto the RC522 stack.
 */

#ifndef PN532_CMD_H
#define PN532_CMD_H

#include <stddef.h>
#include <stdint.h>

/*
 * Handle one decoded command (cmd[0] is the command code; cmd[1..] its data).
 * Writes the response payload (data that follows TFI+cmd in the reply frame)
 * into resp and returns its length, plus the response command code (normally
 * cmd[0]+1) via *resp_cmd. resp must hold at least PN532_MAX_DATA bytes.
 *
 * Returns a negative value when no response should be sent.
 */
int pn532_cmd_handle(const uint8_t *cmd, uint16_t cmd_len,
                     uint8_t *resp, uint8_t *resp_cmd);

#endif /* PN532_CMD_H */
