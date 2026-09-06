#!/bin/sh
# One scope phase per invocation. Arm/change the scope trigger before running.
set -eu
if [ "$#" -ne 2 ]; then
    printf 'usage: sh tools/endurance.sh 000000|FFFFFF SECONDS\n' >&2
    exit 2
fi
case "$1" in 000000|FFFFFF) ;; *) printf 'choose 000000 or FFFFFF\n' >&2; exit 2 ;; esac
case "$2" in ''|*[!0-9]*) printf 'SECONDS must be a positive integer\n' >&2; exit 2 ;; esac
if [ "$2" -le 0 ] || [ "$2" -gt 86400 ]; then
    printf 'SECONDS must be 1..86400\n' >&2; exit 2
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
driver=$(dirname -- "$script_dir")/build/dld-send
test -x "$driver"
uptime_seconds() {
    read -r uptime idle < /proc/uptime
    printf '%s\n' "${uptime%%.*}"
}
started=$(uptime_seconds)
deadline=$((started + $2))
count=0
while [ "$(uptime_seconds)" -lt "$deadline" ]; do
    "$driver" "$1" > /dev/null
    count=$((count + 1))
done
finished=$(uptime_seconds)
printf 'phase=%s sends=%s elapsed_seconds=%s\n' "$1" "$count" "$((finished - started))"
