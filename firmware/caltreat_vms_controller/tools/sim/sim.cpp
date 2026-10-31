// Host-side simulator: runs the firmware's page engine (src/vms_core.cpp) on a PC and writes
// frames for tools/mockups.py. Build and run with tools/mockups.sh.
//   OUTDIR/<name>.pbm    still frames (P4 bitmap, 128x32)
//   OUTDIR/<name>.anim   animation: "W H N" header line, then N lines "<hold ms> <hex frame>"
// Like the firmware, the sign is rendered continuously (every 25 ms) between the snapshots.
#include <stdio.h>
#include <string.h>
#include <string>
#include "vms_core.h"

using namespace vms;
static std::string outdir;
static struct tm clockTime;   // 2024-09-23 19:12, the X6 clock page

struct Sim {
  Sign s;
  double now = 0;
  Frame f;
  void runTo(double t, double step = 25) {      // firmware FRAME_MS = 25
    for (; now < t; now += step) s.render((uint32_t)now, &clockTime, f);
    now = t;
    s.render((uint32_t)now, &clockTime, f);
  }
};

static void still(Sim &m, const char *name, double t) {
  m.runTo(t);
  FILE *fp = fopen((outdir + "/" + name + ".pbm").c_str(), "wb");
  fprintf(fp, "P4\n%d %d\n", W, H);
  fwrite(m.f.px, 1, sizeof m.f.px, fp);
  fclose(fp);
  printf("still %-28s t=%7.2f s  page=%s\n", name, t / 1000, m.s.pageName());
}

// Render every `stepMs` until t1; consecutive identical frames are merged.
static void anim(Sim &m, const char *name, double t1, double stepMs) {
  const double t0 = m.now;
  std::string body;
  Frame prev;
  int n = 0;
  double held = 0;
  bool have = false;
  auto flush = [&]() {
    char buf[16];
    snprintf(buf, sizeof buf, "%d ", (int)(held + 0.5));
    body += buf;
    for (size_t i = 0; i < sizeof prev.px; i++) {
      snprintf(buf, sizeof buf, "%02x", ((const uint8_t *)prev.px)[i]);
      body += buf;
    }
    body += "\n";
    n++;
  };
  for (double t = t0; t < t1; t += stepMs) {
    m.runTo(t, stepMs);
    if (have && memcmp(m.f.px, prev.px, sizeof prev.px) == 0) { held += stepMs; continue; }
    if (have) flush();
    prev = m.f; held = stepMs; have = true;
  }
  flush();
  FILE *fp = fopen((outdir + "/" + name + ".anim").c_str(), "w");
  fprintf(fp, "%d %d %d\n%s", W, H, n, body.c_str());
  fclose(fp);
  printf("anim  %-28s %.2f-%.2f s, %d distinct frames\n", name, t0 / 1000, t1 / 1000, n);
}

static Train T(const char *n, const char *t, const char *s) { Train x; x.set(n, t, s); return x; }

static const char *QUIET = "Quiet Electric Trains In Service. Stand behind yellow line at all times.";
static const char *DELAYS = "Please Expect up to 30 to 60 Minute Delays Due to Police Activity.";
static const char *CANCEL = "Train 428 Has Been Cancelled.";
static const char *CLIPPER = "For all Clipper card users: tap on before boarding.";

int main(int argc, char **argv) {
  if (argc != 2) { fprintf(stderr, "usage: sim OUTDIR\n"); return 2; }
  outdir = argv[1];
  memset(&clockTime, 0, sizeof clockTime);
  clockTime.tm_year = 124; clockTime.tm_mon = 8; clockTime.tm_mday = 23;
  clockTime.tm_hour = 19; clockTime.tm_min = 12;

  // Cycle start times with the default timing: list 0.05-30.05, clock 30.10-33.10,
  // list 33.15-53.15, scroll 53.20-69.80 (each page starts with a 50 ms blank).
  const Train x6[2] = {T("156", "7:16 PM", "7 MIN LATE"), T("158", "7:46 PM", "ON TIME")};  // X6, Sep 23 2024
  {
    Sim m; m.s.setTrains(x6, 2);
    still(m, "01_train_list", 1000);
    still(m, "03_clock", 31000);
  }
  {
    Sim m; const Train w[2] = {T("101", "12:58 PM", "45 MIN LATE"), T("145", "11:05 PM", "CANCELLED")};
    m.s.setTrains(w, 2);
    still(m, "02_train_list_widest", 1000);
  }
  {
    Sim m; const Train d[2] = {T("110", "8:21 AM", "ON TIME"), T("408", "8:36 AM", "ON TIME")};  // San Antonio photo
    m.s.setTrains(d, 2);
    m.runTo(1000);
    m.s.approach("110", true, 1000);
    still(m, "04_attention", 2000);
    still(m, "05_arriving_on", 11000 + 50 + 200);    // ATTENTION 1-11 s, 50 ms gap, blink on 0-470 ms
    still(m, "06_arriving_off", 11000 + 50 + 700);
  }
  {
    Sim m; const Train d[2] = {T("110", "8:21 AM", "ARRIVING"), T("408", "8:36 AM", "ON TIME")};
    m.s.setTrains(d, 2);
    m.runTo(1000);
    anim(m, "anim_arriving_blink", 1000 + 50 + 4000, 1000.0 / 30);
  }
  {
    Sim m; m.s.setTrains(x6, 2);
    m.s.setAlert("quiet", QUIET, 0, 0);
    still(m, "07_scroll_t2s", 53200 + 2000);
    still(m, "08_scroll_t6s", 53200 + 6000);
    still(m, "09_scroll_t16s", 53200 + 16000);
  }
  {
    Sim m; m.s.setTrains(x6, 2);
    m.s.setAlert("quiet", QUIET, 0, 0);
    m.runTo(53150);
    anim(m, "anim_scroll", 53150 + 16650 + 1500, 1000.0 / 30);    // whole slot, cut at 16.6 s, back to list
  }
  {
    // JLP: alert scrolling, approach pre-empts it (no blank), 10 s ATTENTION, list with ARRIVING blinking
    Sim m; const Train j[2] = {T("428", "4:27 PM", "ON TIME"), T("430", "4:57 PM", "ON TIME")};
    m.s.setTrains(j, 2);
    m.s.setAlert("clipper", CLIPPER, 0, 0);
    m.runTo(53200 + 1000);
    anim(m, "anim_approach_a", 53200 + 4000, 1000.0 / 30);
    m.s.approach("428", true, (uint32_t)m.now);
    anim(m, "anim_approach_b", 53200 + 4000 + 10000 + 4000, 1000.0 / 30);
  }
  {
    // Whole cycle with two alerts at 10 frames/s, for checking the schedule
    Sim m; m.s.setTrains(x6, 2);
    m.s.setAlert("cancel", CANCEL, 0, 0);
    m.s.setAlert("delays", DELAYS, 0, 0);
    anim(m, "anim_cycle_10fps", 125000, 100);
  }
  return 0;
}
