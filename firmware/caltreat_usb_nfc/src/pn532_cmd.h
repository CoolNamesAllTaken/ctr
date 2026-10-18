/*
 * PN532 command dispatcher: maps the subset of PN532 host commands that libnfc,
 * nfcpy and adafruit-circuitpython-pn532 use onto the RC522 stack.
 */

#ifndef PN532_CMD_H
#define PN532_CMD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* pn532_cmd_handle return values other than a response length. */
#define PN532_NO_RESPONSE   (-1) /* command aborted by the host: send nothing */
#define PN532_SYNTAX_ERROR  (-2) /* unknown/malformed command: send the error frame */

/*
 * Handle one decoded command (cmd[0] is the command code; cmd[1..] its data).
 * Writes the response payload (data that follows TFI+cmd in the reply frame)
 * into resp and returns its length, plus the response command code (normally
 * cmd[0]+1) via *resp_cmd. resp must hold at least PN532_MAX_DATA bytes.
 *
 * Returns PN532_NO_RESPONSE or PN532_SYNTAX_ERROR instead of a length when
 * there is no normal response to send.
 */
int pn532_cmd_handle(const uint8_t *cmd, uint16_t cmd_len,
                     uint8_t *resp, uint8_t *resp_cmd);

/*
 * Supplied by the transport (main.c). Long-running commands (passive target
 * search, InAutoPoll) call it while they wait: it must keep USB serviced and
 * return true when the host has sent new data or closed the port, which aborts
 * the command like an ACK does on a real PN532.
 */
bool pn532_host_abort(void);

#endif /* PN532_CMD_H */
