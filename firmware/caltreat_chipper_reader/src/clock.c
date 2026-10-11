#include "clock.h"

#include <stdio.h>
#include <string.h>
#include <sys/time.h>

void clock_set(time_t now)
{
    struct timeval tv = {.tv_sec = now};
    settimeofday(&tv, NULL);
}

void clock_init(void)
{
    static const char months[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
    char mon[4] = {0};
    struct tm tm = {0};

    /* __DATE__ = "Oct  8 2026", __TIME__ = "15:14:19" */
    sscanf(__DATE__, "%3s %d %d", mon, &tm.tm_mday, &tm.tm_year);
    sscanf(__TIME__, "%d:%d:%d", &tm.tm_hour, &tm.tm_min, &tm.tm_sec);
    tm.tm_mon = (strstr(months, mon) - months) / 3;
    tm.tm_year -= 1900;
    clock_set(mktime(&tm));
}

void clock_format(char *buf, size_t len)
{
    static const char *months[] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    snprintf(buf, len, "%02d %s %04d %02d:%02d:%02d", tm.tm_mday, months[tm.tm_mon],
             tm.tm_year + 1900, tm.tm_hour, tm.tm_min, tm.tm_sec);
}
