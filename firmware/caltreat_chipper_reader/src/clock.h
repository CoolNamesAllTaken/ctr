#pragma once

#include <stddef.h>
#include <time.h>

/* Seed the wall clock from the firmware build time (there is no RTC/network yet). */
void clock_init(void);

/* Set the wall clock (Unix seconds), e.g. from the serial console. */
void clock_set(time_t now);

/* Reader-style timestamp: "27 SEP 2022 10:15:16". */
void clock_format(char *buf, size_t len);
