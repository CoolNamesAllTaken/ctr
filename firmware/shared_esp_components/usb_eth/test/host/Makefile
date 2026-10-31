# Host-side unit tests for the IDF-independent usb_eth logic (plain gcc), one
# binary per suite: core (usb_eth_util.c) and rtl815x (rtl815x_proto.c).
#
#   make            build and run every suite, print the total
#   make asan       build with AddressSanitizer + LeakSanitizer + UBSan and run;
#                   any leak, memory error or UB fails the run
#   make valgrind   run under valgrind memcheck; any leak or error fails the run
#                   VALGRIND=/path/to/valgrind      use a non-default valgrind
#                   VALGRIND_LDFLAGS=...            extra link flags for that build, e.g.
#                     -Wl,--dynamic-linker=<ld.so copy with debug info> when glibc's
#                     debug symbols (libc6-dbg) cannot be installed system-wide
#   make check      test + asan + valgrind (valgrind is skipped with a message when
#                   it is not installed; `make valgrind` fails instead)
CC       ?= gcc
VALGRIND ?= valgrind
VALGRIND_LDFLAGS ?=
TOP      := ../..
CFLAGS   := -std=c11 -O1 -g -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion \
            -I. -I$(TOP)/private_include -I$(TOP)/drivers/rtl815x
SAN      := -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer
UTIL     := $(TOP)/core/usb_eth_util.c

SUITES        := core rtl815x
core_SRCS     := core/test_usb_eth_util.c $(UTIL)
rtl815x_SRCS  := rtl815x/test_rtl815x_proto.c $(TOP)/drivers/rtl815x/rtl815x_proto.c $(UTIL)
HDRS     := test_harness.h $(wildcard $(TOP)/private_include/*.h $(TOP)/drivers/rtl815x/*.h)

BIN      := $(SUITES:%=%/test_runner)
BIN_ASAN := $(SUITES:%=%/test_runner_asan)
BIN_VG   := $(SUITES:%=%/test_runner_vg)

.SECONDEXPANSION:
%/test_runner: $$($$*_SRCS) $(HDRS)
	$(CC) $(CFLAGS) -o $@ $($*_SRCS)

%/test_runner_asan: $$($$*_SRCS) $(HDRS)
	$(CC) $(CFLAGS) $(SAN) -o $@ $($*_SRCS)

# Always rebuilt: its link flags (VALGRIND_LDFLAGS) are not tracked by make.
%/test_runner_vg: $$($$*_SRCS) $(HDRS) FORCE
	$(CC) $(CFLAGS) $(VALGRIND_LDFLAGS) -o $@ $($*_SRCS)

.PHONY: test asan valgrind valgrind-if-available check clean FORCE
FORCE:
test: $(BIN)
	@./run_suites.sh "" $(BIN)

asan: $(BIN_ASAN)
	@./run_suites.sh ./run_asan.sh $(BIN_ASAN)

VG_CMD := $(VALGRIND) --error-exitcode=99 --leak-check=full --show-leak-kinds=all --errors-for-leak-kinds=all
valgrind: $(BIN_VG)
	@./run_suites.sh "$(VG_CMD)" $(BIN_VG)

# Like `valgrind`, but skipped with a message when valgrind is not installed.
valgrind-if-available: $(BIN_VG)
	@if command -v $(VALGRIND) >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory valgrind; \
	else \
		echo "SKIP valgrind: '$(VALGRIND)' not found (install valgrind or pass VALGRIND=/path/to/valgrind)"; \
	fi

check: test asan valgrind-if-available

clean:
	rm -f $(BIN) $(BIN_ASAN) $(BIN_VG)

.DEFAULT_GOAL := test
