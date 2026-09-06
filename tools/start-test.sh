#!/bin/sh
# Explicit hardware handover for the supplied systemd BBG. No service files
# or startup links are changed; the CLI tools themselves never manage services.
set -eu
if [ "$#" -ne 1 ]; then
    printf 'usage: sh tools/start-test.sh CONFIG_FILE\n' >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    printf 'start-test requires root\n' >&2
    exit 3
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
test -x "$project_dir/build/dld-init"
systemctl stop ledscape.service
state=$(systemctl show ledscape.service -p ActiveState)
pid=$(systemctl show ledscape.service -p MainPID)
case "$state" in
    ActiveState=inactive|ActiveState=failed) ;;
    *) printf 'LEDscape has not stopped: %s\n' "$state" >&2; exit 3 ;;
esac
if [ "$pid" != 'MainPID=0' ]; then
    printf 'LEDscape still has a main process: %s\n' "$pid" >&2
    exit 3
fi
# A manual systemd stop cancels that service's automatic restart behavior.
# The operator must also exclude independent/manual PRU users during testing.
exec "$project_dir/build/dld-init" "$1"
