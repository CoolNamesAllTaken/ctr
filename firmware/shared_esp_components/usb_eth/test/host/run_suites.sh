#!/bin/sh
# run_suites.sh "<wrapper command>" binary...: run each suite binary (through the
# wrapper, if any) and print the sum of their "N checks, M failures" lines.
wrap=$1
shift
rc=0
total=0
failed=0
for bin in "$@"; do
    out=$($wrap "$bin" 2>&1) || rc=1
    printf '%s\n' "$out"
    line=$(printf '%s\n' "$out" | grep -E '^[0-9]+ checks, [0-9]+ failures$' | tail -1)
    [ -n "$line" ] || rc=1
    total=$((total + ${line%% *}))
    n=${line#*, }
    failed=$((failed + ${n%% *}))
done
echo "== total: $total checks, $failed failures"
exit $rc
