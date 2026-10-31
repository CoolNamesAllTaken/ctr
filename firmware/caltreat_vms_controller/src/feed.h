// Backend feed: what the sign shows, from the public timetable.
//
//   GET /api/timetable/?kiosk_id=<hostname>  trains stopping at the sign's station today (the sign is a
//                                            kiosk: an employee assigns it a station in the portal)
//   GET /api/timetable/                      the active service alerts
//
// The sign's clock follows the backend's (HTTP Date header), so it needs no SNTP.
#pragma once
#include <stdint.h>
#include <time.h>

#include "vms_core.h"

namespace feed {

struct Train {
  char number[8];
  int  minute;         // scheduled time at this station, minutes after local midnight
  int  delay_min;
};

struct Alert {
  char id[24];
  char text[vms::ALERT_LEN];
};

struct Snapshot {
  bool  registered;    // false: the backend doesn't know this sign's kiosk ID yet
  int   ntrains;
  Train trains[vms::MAX_TRAINS];   // the next trains at this station, in scheduled order
  int   nalerts;
  Alert alerts[vms::MAX_ALERTS];   // newest first
};

void start();                          // poll task; call after net_init()
// Copy the latest snapshot if it's newer than *seq (updated). Returns true if it copied.
bool get(Snapshot *out, uint32_t *seq);
// esp_timer time of the last good poll, 0 = never.
int64_t lastOkUs();
void printStatus();

}  // namespace feed
