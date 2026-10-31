#!/bin/sh
# Run the ASan/UBSan test binary. GCC 12's sanitizer runtime can crash in a loop
# at startup ("AddressSanitizer:DEADLYSIGNAL" before main runs) on Linux >= 6.5
# kernels with vm.mmap_rnd_bits = 32, depending on the random address layout
# (fixed in newer compiler-rt). Only that case, recognisable because no test
# has printed anything yet, is retried; any report from a test run fails.
bin=${1:-./test_runner_asan}
out=$(mktemp)
trap 'rm -f "$out"' EXIT
export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
for attempt in 1 2 3 4 5 6 7 8; do
    timeout 60 "$bin" >"$out" 2>&1
    rc=$?
    if grep -q "AddressSanitizer:DEADLYSIGNAL" "$out" && ! grep -q "^PASS\|^FAIL" "$out"; then
        echo "(sanitizer runtime startup crash, ASLR layout; retry $attempt)"
        continue
    fi
    cat "$out"
    exit $rc
done
echo "sanitizer runtime never started" >&2
exit 1
