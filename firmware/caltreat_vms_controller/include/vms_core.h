// Page engine of the DIY Caltrain platform VMS: state (train rows, alerts, approach event),
// scheduler and rendering of one 128x32 face. Plain C++ with no Arduino dependency, so the same
// code runs on the ESP32 (src/main.cpp) and on a PC (tools/sim/sim.cpp renders the mockups).
//
// Behaviour follows worker-2's video analysis (/workspace/agents/worker-2/t0069/report.md):
//   - pages: train list (default), clock, scrolling alert, ATTENTION / #NNN APPROACHING
//   - hard cuts with a short blank between pages; no wipes or fades
//   - cycle: list 30 s -> clock 3 s -> list 20 s -> [per active alert: scroll 16.6 s] -> repeat
//     (consecutive alerts are separated by a 7.6 s train list, as seen in X6)
//   - scroll: bold mixed case, enters right from an empty panel, 1 column per 33 ms (30 col/s),
//     cut at 16.6 s even mid-message
//   - approach event pre-empts everything: ATTENTION page 10 s, then the train list with that
//     train's status ARRIVING blinking (1.0 s period, 0.47 s on)
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <time.h>
#include "vms_font.h"

namespace vms {

constexpr int W = 128, H = 32;   // one face

struct Frame {
  uint8_t px[H][W / 8];
  void clear();
  void set(int x, int y);
  bool get(int x, int y) const;
};

int  textWidth(const VmsFont &f, const char *s);                       // ink width from pen x = 0
void drawText(Frame &fr, const VmsFont &f, int x, int baseline, const char *s);  // baseline = first row below the caps

struct Timing {                    // all adjustable at run time (POST /config)
  uint32_t list1_ms        = 30000;  // train list at the start of the cycle
  uint32_t clock_ms        = 3000;   // clock filler (X6: 1.17 s, SV: >= 3.8 s)
  uint32_t list2_ms        = 20000;  // train list before the alerts (X6: 5.8 s)
  uint32_t list_between_ms = 7600;   // train list between two consecutive alerts (X6: 7.6 s)
  uint32_t scroll_ms       = 16600;  // scroll slot; the page is cut at this time
  uint32_t scroll_cps      = 30;     // scroll speed, LED columns per second
  bool     scroll_end_early = true;  // end the slot as soon as a short message has left the panel
  uint32_t attention_ms    = 10000;  // ATTENTION / #NNN APPROACHING page
  uint32_t blink_period_ms = 1000;   // ARRIVING blink
  uint32_t blink_on_ms     = 470;
  uint32_t gap_ms          = 50;     // blank between pages (observed 17-220 ms)
};

struct Train {
  char number[8];
  char time[8];      // "7:16"
  char ampm[3];      // "PM"
  char status[24];   // "ON TIME", "7 MIN LATE", "ARRIVING", ...
  // time: "7:16 PM", "7:16PM" or 24-hour "19:16". Returns false if the time can't be parsed.
  bool set(const char *number, const char *time, const char *status);
};

constexpr int MAX_TRAINS = 8;      // only the first two are shown
constexpr int MAX_ALERTS = 6;
constexpr int ALERT_LEN  = 400;

struct Alert {
  bool     used;
  bool     forever;
  uint32_t expires;                // millis() when it stops being scheduled (unless forever)
  char     id[24];
  char     text[ALERT_LEN];
};

class Sign {
 public:
  Timing timing;

  // ---- input ----
  void setTrains(const Train *t, int n);
  bool upsertTrain(const Train &t);          // match by number; appended if new (false if full)
  bool removeTrain(const char *number);      // train departed: row 2 moves up
  bool setStatus(const char *number, const char *status);
  void approach(const char *number, bool setArriving, uint32_t now);
  bool setAlert(const char *id, const char *text, uint32_t ttl_s, uint32_t now);  // ttl 0 = until removed
  bool removeAlert(const char *id);
  void clearAlerts();
  void restartCycle(uint32_t now);           // next frame starts the cycle at the train list

  // ---- state for GET /state ----
  int          trainCount() const { return ntrains_; }
  const Train &train(int i) const { return trains_[i]; }
  int          alertCount(uint32_t now);
  const Alert &alertSlot(int i) const { return alerts_[i]; }
  const char  *pageName() const;
  bool         approachActive() const { return attention_; }
  const char  *approachNumber() const { return approachNum_; }

  // ---- output ----
  // localTime: current local time, or nullptr if the clock has not been set (clock page skipped).
  void render(uint32_t now, const struct tm *localTime, Frame &out);

 private:
  enum Page : uint8_t { LIST, CLOCK, SCROLL, ATTENTION };
  struct Step { Page page; int8_t alert; uint32_t ms; };

  void buildPlan(uint32_t now);
  void startStep(int i, uint32_t now);
  void nextStep(uint32_t now);
  void expireAlerts(uint32_t now);
  void drawList(Frame &f, uint32_t t);
  void drawClock(Frame &f, const struct tm &tm);
  bool drawScroll(Frame &f, const char *text, uint32_t t);   // false when the text has left the panel
  void drawAttention(Frame &f);

  Train trains_[MAX_TRAINS];
  int   ntrains_ = 0;
  Alert alerts_[MAX_ALERTS] = {};

  Step     plan_[3 + 2 * MAX_ALERTS];
  int      nplan_ = 0, step_ = -1;
  uint32_t stepStart_ = 0;
  char     scrollId_[24] = "";               // alert being scrolled (by id: slots may change)
  bool     clockShown_ = false;              // the last page drawn showed the clock

  bool     attention_ = false;
  uint32_t attentionStart_ = 0;
  char     approachNum_[8] = "";
};

}  // namespace vms
