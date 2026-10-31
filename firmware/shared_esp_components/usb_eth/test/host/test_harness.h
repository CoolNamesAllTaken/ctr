/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Minimal test runner shared by the host test suites (one suite per binary).
 */
#pragma once

#include <stdio.h>

/* ---------------------------------------------------------------- runner -- */

static int g_failures;
static int g_checks;
static const char *g_test;

#define CHECK(cond) do { \
        g_checks++; \
        if (!(cond)) { \
            g_failures++; \
            printf("  FAIL %s:%d [%s]: %s\n", __FILE__, __LINE__, g_test, #cond); \
        } \
    } while (0)

#define CHECK_EQ(a, b) do { \
        unsigned long long _a = (unsigned long long)(a), _b = (unsigned long long)(b); \
        g_checks++; \
        if (_a != _b) { \
            g_failures++; \
            printf("  FAIL %s:%d [%s]: %s == %s (0x%llx != 0x%llx)\n", \
                   __FILE__, __LINE__, g_test, #a, #b, _a, _b); \
        } \
    } while (0)

#define RUN(fn) do { g_test = #fn; int _f = g_failures; fn(); \
        printf("%s %s\n", g_failures == _f ? "PASS" : "FAIL", #fn); } while (0)

#define REPORT() (printf("\n%d checks, %d failures\n", g_checks, g_failures), g_failures ? 1 : 0)
