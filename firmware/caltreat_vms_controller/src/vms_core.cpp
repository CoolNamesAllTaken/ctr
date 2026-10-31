// Page engine of the DIY Caltrain platform VMS. See include/vms_core.h.
#include "vms_core.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "fonts/CTSans10.h"
#include "fonts/CTBold13.h"
#include "fonts/CTBold12C.h"

namespace vms {

// ---- layout (LED columns/rows of one 128x32 face) --------------------------------------------
// Real 192-column sign (worker-2 section 5): number at col ~4, time ~43, AM/PM ~75, status ~109;
// lines on rows 1-10 and 21-30. Here the fields are packed so the widest row
// "101 12:58 PM 45 MIN LATE" fits: 3 blank columns between fields, time right-aligned.
static constexpr int LIST_BASE[2] = {11, 31};   // caps on rows 1-10 and 21-30
static constexpr int COL_TRAIN  = 0;
static constexpr int TIME_END   = 52;           // time is right-aligned to end at column 51
static constexpr int COL_AMPM   = 55;
static constexpr int COL_STATUS = 71;           // up to 57 columns: "10 MIN LATE", "CANCELLED"
static constexpr int CLOCK_BASE[2] = {15, 28};  // clock: caps rows 5-14 and 18-27, centred block
static constexpr int SCROLL_BASE = 22;          // bold caps rows 9-21: vertically centred
static constexpr int ATTN_BASE[2] = {14, 30};   // bold caps rows 2-13 and 18-29

// ---- frame / text ------------------------------------------------------------------------------
void Frame::clear() { memset(px, 0, sizeof px); }
void Frame::set(int x, int y) {
  if (x >= 0 && x < W && y >= 0 && y < H) px[y][x >> 3] |= 0x80 >> (x & 7);
}
bool Frame::get(int x, int y) const { return px[y][x >> 3] & (0x80 >> (x & 7)); }

static const VmsGlyph *glyphOf(const VmsFont &f, char c) {
  uint8_t u = (uint8_t)c;
  if (u < f.first || u > f.last) u = ' ';
  return &f.glyph[u - f.first];
}

int textWidth(const VmsFont &f, const char *s) {
  int x = 0, right = 0;
  for (; *s; s++) {
    const VmsGlyph *g = glyphOf(f, *s);
    if (g->w && x + g->xo + g->w > right) right = x + g->xo + g->w;
    x += g->adv;
  }
  return right;
}

void drawText(Frame &fr, const VmsFont &f, int x, int baseline, const char *s) {
  for (; *s; s++) {
    const VmsGlyph *g = glyphOf(f, *s);
    if (x + g->xo >= W) break;
    if (x + g->xo + g->w > 0) {
      const uint8_t *bm = f.bitmap + g->off;
      int bit = 0;
      for (int yy = 0; yy < g->h; yy++)
        for (int xx = 0; xx < g->w; xx++, bit++)
          if (bm[bit >> 3] & (0x80 >> (bit & 7))) fr.set(x + g->xo + xx, baseline + g->yo + yy);
    }
    x += g->adv;
  }
}

static void copyUpper(char *dst, size_t n, const char *src) {
  size_t i = 0;
  for (; src && src[i] && i + 1 < n; i++) dst[i] = (char)toupper((unsigned char)src[i]);
  dst[i] = 0;
}

static bool sameText(const char *a, const char *b) {
  for (; *a && *b; a++, b++)
    if (toupper((unsigned char)*a) != toupper((unsigned char)*b)) return false;
  return *a == *b;
}

static bool expired(uint32_t now, uint32_t when) { return (int32_t)(now - when) >= 0; }

// ---- Train -------------------------------------------------------------------------------------
bool Train::set(const char *num, const char *t, const char *st) {
  copyUpper(number, sizeof number, num);
  copyUpper(status, sizeof status, st);
  int h, m;
  char suffix[4] = "";
  if (!t || sscanf(t, "%d:%d %3s", &h, &m, suffix) < 2) return false;
  if (!suffix[0]) {                                  // "7:16PM" or 24-hour "19:16"
    const char *p = strpbrk(t, "AaPp");
    if (p) { suffix[0] = p[0]; suffix[1] = 'M'; suffix[2] = 0; }
  }
  if (m < 0 || m > 59 || h < 0 || h > 23) return false;
  if (suffix[0]) {
    copyUpper(ampm, sizeof ampm, suffix);
    if ((ampm[0] != 'A' && ampm[0] != 'P') || h < 1 || h > 12) return false;
    ampm[1] = 'M';
  } else {
    snprintf(ampm, sizeof ampm, "%s", h < 12 ? "AM" : "PM");
    h = (h + 11) % 12 + 1;
  }
  snprintf(time, sizeof time, "%d:%02d", h, m);
  return true;
}

// ---- input -------------------------------------------------------------------------------------
void Sign::setTrains(const Train *t, int n) {
  ntrains_ = n > MAX_TRAINS ? MAX_TRAINS : n;
  for (int i = 0; i < ntrains_; i++) trains_[i] = t[i];
}

bool Sign::upsertTrain(const Train &t) {
  for (int i = 0; i < ntrains_; i++)
    if (sameText(trains_[i].number, t.number)) { trains_[i] = t; return true; }
  if (ntrains_ >= MAX_TRAINS) return false;
  trains_[ntrains_++] = t;
  return true;
}

bool Sign::removeTrain(const char *number) {
  for (int i = 0; i < ntrains_; i++)
    if (sameText(trains_[i].number, number)) {
      for (int j = i; j + 1 < ntrains_; j++) trains_[j] = trains_[j + 1];
      ntrains_--;
      return true;
    }
  return false;
}

bool Sign::setStatus(const char *number, const char *status) {
  for (int i = 0; i < ntrains_; i++)
    if (sameText(trains_[i].number, number)) {
      copyUpper(trains_[i].status, sizeof trains_[i].status, status);
      return true;
    }
  return false;
}

void Sign::approach(const char *number, bool setArriving, uint32_t now) {
  copyUpper(approachNum_, sizeof approachNum_, number);
  if (setArriving) setStatus(approachNum_, "ARRIVING");
  attention_ = true;                  // pre-empts any page immediately (JLP: scroll cut, no blank)
  attentionStart_ = now;
}

bool Sign::setAlert(const char *id, const char *text, uint32_t ttl_s, uint32_t now) {
  Alert *slot = nullptr;
  for (Alert &a : alerts_)
    if (a.used && strcmp(a.id, id) == 0) { slot = &a; break; }
  if (!slot)
    for (Alert &a : alerts_)
      if (!a.used) { slot = &a; break; }
  if (!slot) return false;
  slot->used = true;
  slot->forever = ttl_s == 0;
  slot->expires = now + ttl_s * 1000;
  snprintf(slot->id, sizeof slot->id, "%s", id);
  snprintf(slot->text, sizeof slot->text, "%s", text);
  return true;
}

bool Sign::removeAlert(const char *id) {
  for (Alert &a : alerts_)
    if (a.used && strcmp(a.id, id) == 0) { a.used = false; return true; }
  return false;
}

void Sign::clearAlerts() {
  for (Alert &a : alerts_) a.used = false;
}

void Sign::expireAlerts(uint32_t now) {
  for (Alert &a : alerts_)
    if (a.used && !a.forever && expired(now, a.expires)) a.used = false;
}

int Sign::alertCount(uint32_t now) {
  expireAlerts(now);
  int n = 0;
  for (const Alert &a : alerts_) n += a.used;
  return n;
}

void Sign::restartCycle(uint32_t) { step_ = -1; }

// ---- scheduler ---------------------------------------------------------------------------------
void Sign::buildPlan(uint32_t now) {
  expireAlerts(now);
  nplan_ = 0;
  plan_[nplan_++] = {LIST, -1, timing.list1_ms};
  plan_[nplan_++] = {CLOCK, -1, timing.clock_ms};
  plan_[nplan_++] = {LIST, -1, timing.list2_ms};
  bool first = true;
  for (int i = 0; i < MAX_ALERTS; i++) {
    if (!alerts_[i].used) continue;
    if (!first) plan_[nplan_++] = {LIST, -1, timing.list_between_ms};
    plan_[nplan_++] = {SCROLL, (int8_t)i, timing.scroll_ms};
    first = false;
  }
}

void Sign::startStep(int i, uint32_t now) {
  step_ = i;
  stepStart_ = now;
  const Step &s = plan_[i];
  if (s.page == SCROLL) snprintf(scrollId_, sizeof scrollId_, "%s", alerts_[s.alert].id);
}

void Sign::nextStep(uint32_t now) {
  if (step_ < 0 || step_ + 1 >= nplan_) { buildPlan(now); startStep(0, now); }
  else startStep(step_ + 1, now);
}

const char *Sign::pageName() const {
  if (attention_) return "attention";
  if (step_ < 0) return "list";
  switch (plan_[step_].page) {
    case LIST: return "list";
    case CLOCK: return "clock";
    case SCROLL: return "scroll";
    default: return "attention";
  }
}

// ---- pages -------------------------------------------------------------------------------------
void Sign::drawList(Frame &f, uint32_t t) {
  const bool blinkOn = (t % timing.blink_period_ms) < timing.blink_on_ms;
  for (int r = 0; r < 2 && r < ntrains_; r++) {
    const Train &tr = trains_[r];
    const int base = LIST_BASE[r];
    drawText(f, CTSans10, COL_TRAIN, base, tr.number);
    drawText(f, CTSans10, TIME_END - textWidth(CTSans10, tr.time), base, tr.time);
    drawText(f, CTSans10, COL_AMPM, base, tr.ampm);
    if (strcmp(tr.status, "ARRIVING") == 0 && !blinkOn) continue;   // only the status word blinks
    int x = COL_STATUS;
    const int w = textWidth(CTSans10, tr.status);
    if (x + w > W) {                                  // unusually long status: pull it left
      const int minX = COL_AMPM + textWidth(CTSans10, tr.ampm) + 2;
      x = W - w < minX ? minX : W - w;
    }
    drawText(f, CTSans10, x, base, tr.status);
  }
}

void Sign::drawClock(Frame &f, const struct tm &tm) {
  static const char *const MONTHS[12] = {"January", "February", "March", "April", "May", "June", "July",
                                         "August", "September", "October", "November", "December"};
  char l1[32], l2[16];
  snprintf(l1, sizeof l1, "%s %d, %d", MONTHS[tm.tm_mon % 12], tm.tm_mday, tm.tm_year + 1900);
  snprintf(l2, sizeof l2, "%d:%02d %s", (tm.tm_hour + 11) % 12 + 1, tm.tm_min, tm.tm_hour < 12 ? "AM" : "PM");
  drawText(f, CTSans10, (W - textWidth(CTSans10, l1)) / 2, CLOCK_BASE[0], l1);
  drawText(f, CTSans10, (W - textWidth(CTSans10, l2)) / 2, CLOCK_BASE[1], l2);
}

bool Sign::drawScroll(Frame &f, const char *text, uint32_t t) {
  // Integer-column steps: 1 column every 1000/scroll_cps ms, starting just off the right edge.
  const int x = W - (int)((uint64_t)t * timing.scroll_cps / 1000);
  drawText(f, CTBold13, x, SCROLL_BASE, text);
  return x + textWidth(CTBold13, text) > 0;
}

void Sign::drawAttention(Frame &f) {
  char l2[24];
  snprintf(l2, sizeof l2, "#%s APPROACHING", approachNum_);
  drawText(f, CTBold12C, (W - textWidth(CTBold12C, "ATTENTION")) / 2, ATTN_BASE[0], "ATTENTION");
  drawText(f, CTBold12C, (W - textWidth(CTBold12C, l2)) / 2, ATTN_BASE[1], l2);
}

void Sign::render(uint32_t now, const struct tm *localTime, Frame &out) {
  out.clear();
  if (attention_) {
    if (now - attentionStart_ < timing.attention_ms) { drawAttention(out); clockShown_ = false; return; }
    attention_ = false;                          // back to the train list, ARRIVING blinking
    step_ = -1;
    nextStep(attentionStart_ + timing.attention_ms);
  }
  // Advance through finished or skipped steps (bounded: a plan never has more than nplan_ steps).
  if (step_ < 0) nextStep(now);
  for (int guard = 0; guard < 2 * (3 + 2 * MAX_ALERTS); guard++) {
    const Step &s = plan_[step_];
    const uint32_t t = now - stepStart_;
    bool skip = false;
    if (s.page == CLOCK && !localTime) skip = true;
    if (s.page == SCROLL) {
      const Alert &a = alerts_[s.alert];
      if (!a.used || strcmp(a.id, scrollId_) != 0 || (!a.forever && expired(now, a.expires))) skip = true;
    }
    if (skip || t >= timing.gap_ms + s.ms) {
      // Keep the cycle on the schedule: the next step starts when this one should have ended.
      const uint32_t end = skip ? now : stepStart_ + timing.gap_ms + s.ms;
      nextStep(end);
      continue;
    }
    // Hard cut with a short blank, except between two pages that both show the clock (no trains to
    // list): there the blank would just look like a flicker.
    const bool clockShown = s.page == CLOCK || (s.page == LIST && ntrains_ == 0 && localTime);
    if (t < timing.gap_ms && !(clockShown && clockShown_)) return;
    clockShown_ = clockShown;
    const uint32_t tp = t < timing.gap_ms ? 0 : t - timing.gap_ms;
    switch (s.page) {
      case LIST:
        if (ntrains_ == 0 && localTime) drawClock(out, *localTime);   // nothing to list: show the time
        else drawList(out, tp);
        return;
      case CLOCK:
        drawClock(out, *localTime);
        return;
      case SCROLL:
        if (drawScroll(out, alerts_[s.alert].text, tp) || !timing.scroll_end_early) return;
        nextStep(now);                           // short message has left the panel: end the slot
        out.clear();
        continue;
      default:
        return;
    }
  }
}

}  // namespace vms
