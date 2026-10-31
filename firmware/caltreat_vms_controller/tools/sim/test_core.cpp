// Host tests of the page engine:  g++ -std=c++17 -Iinclude tools/sim/test_core.cpp src/vms_core.cpp && ./a.out
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "vms_core.h"
using namespace vms;

static int litRows(const Frame &f, int y0, int y1, int x0 = 0, int x1 = W) {
  int n = 0; for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++) n += f.get(x, y); return n;
}

int main() {
  Train t;
  assert(t.set("156", "7:16 PM", "7 min late") && !strcmp(t.time, "7:16") && !strcmp(t.ampm, "PM") && !strcmp(t.status, "7 MIN LATE"));
  assert(t.set("1", "19:05", "") && !strcmp(t.time, "7:05") && !strcmp(t.ampm, "PM"));
  assert(t.set("1", "0:30", "") && !strcmp(t.time, "12:30") && !strcmp(t.ampm, "AM"));
  assert(t.set("1", "12:58am", "") && !strcmp(t.time, "12:58") && !strcmp(t.ampm, "AM"));
  assert(!t.set("1", "13:00 PM", "") && !t.set("1", "7:61", "") && !t.set("1", "", ""));

  struct tm clk = {}; clk.tm_year = 124; clk.tm_mon = 8; clk.tm_mday = 23; clk.tm_hour = 19; clk.tm_min = 12;
  Sign s; Frame f;
  Train rows[3]; rows[0].set("156", "7:16 PM", "ON TIME"); rows[1].set("158", "7:46 PM", "ON TIME"); rows[2].set("160", "8:16 PM", "ON TIME");
  s.setTrains(rows, 3);
  uint32_t now = 0;
  auto run = [&](uint32_t to, const struct tm *c = nullptr) { for (; now <= to; now += 4) s.render(now, c ? c : &clk, f); now = to; s.render(now, c ? c : &clk, f); };
  run(1000);
  assert(!strcmp(s.pageName(), "list") && litRows(f, 0, 1) == 0 && litRows(f, 1, 11) > 0 && litRows(f, 11, 21) == 0 && litRows(f, 21, 31) > 0);

  // approach: ATTENTION for 10 s, then the list with ARRIVING blinking (row 1 status area only)
  s.approach("156", true, now);
  run(now + 100); assert(!strcmp(s.pageName(), "attention"));
  const uint32_t a0 = now - 100;
  run(a0 + 9990); assert(!strcmp(s.pageName(), "attention"));
  run(a0 + 10000 + 50 + 100); Frame on = f; assert(!strcmp(s.pageName(), "list"));
  run(a0 + 10000 + 50 + 600); Frame off = f;
  assert(litRows(on, 1, 11, 71) > 0 && litRows(off, 1, 11, 71) == 0);           // status blinks
  assert(litRows(on, 1, 11, 0, 71) == litRows(off, 1, 11, 0, 71));            // number/time steady
  assert(litRows(on, 21, 31) == litRows(off, 21, 31));                         // row 2 steady

  // departure: row 2 moves up
  Frame before = f;
  assert(s.removeTrain("156") && s.trainCount() == 2 && !strcmp(s.train(0).number, "158"));
  run(now + 4);
  assert(memcmp(before.px[21], f.px[1], 16 * 10) == 0);                        // old row 2 now on row 1

  // alerts: TTL expiry; scroll cut at 16.6 s for a long message
  Sign s2; s2.setTrains(rows, 2);
  char longtext[300]; memset(longtext, 'W', 299); longtext[299] = 0;
  assert(s2.setAlert("x", longtext, 0, 0) && s2.setAlert("y", "short", 1, 0));
  assert(s2.alertCount(500) == 2 && s2.alertCount(1000) == 1);
  uint32_t n2 = 0; const char *last = "";
  uint32_t scrollStart = 0, scrollEnd = 0;
  for (; n2 < 80000; n2 += 4) {
    s2.render(n2, &clk, f);
    const char *p = s2.pageName();
    if (strcmp(p, last)) { if (!strcmp(p, "scroll")) scrollStart = n2; if (!strcmp(last, "scroll")) scrollEnd = n2; last = p; }
  }
  printf("scroll slot %u -> %u ms (%u ms)\n", scrollStart, scrollEnd, scrollEnd - scrollStart);
  assert(scrollStart >= 53140 && scrollStart <= 53160);
  assert(scrollEnd - scrollStart >= 16640 && scrollEnd - scrollStart <= 16660);   // 50 ms gap + 16.6 s

  // no clock set: clock page skipped
  Sign s3; s3.setTrains(rows, 2);
  bool sawClock = false;
  for (uint32_t m = 0; m < 60000; m += 4) { s3.render(m, nullptr, f); sawClock |= !strcmp(s3.pageName(), "clock"); }
  assert(!sawClock);
  puts("all tests passed");
  return 0;
}
