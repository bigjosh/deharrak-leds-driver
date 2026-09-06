#!/bin/sh
# Only malformed arguments/configurations are passed to the driver commands.
# This test never submits a valid initialization or color request.
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: sh tests/test_cli.sh BUILD_DIRECTORY" >&2
    exit 2
fi

build_dir=$(CDPATH= cd "$1" && pwd)
init=$build_dir/dld-init
send=$build_dir/dld-send
if [ ! -x "$init" ] || [ ! -x "$send" ]; then
    echo "missing executable dld-init or dld-send in $build_dir" >&2
    exit 1
fi

# Keep scratch files beneath the build directory, in a new unique directory.
test_dir=$(mktemp -d "$build_dir/cli-test.XXXXXX")
trap 'rm -f "$test_dir/out" "$test_dir/err" "$test_dir/panel.json"; rmdir "$test_dir"' 0
trap 'exit 1' 1 2 15
checks=0

expect_rejected()
{
    label=$1
    expected=$2
    shift 2
    status=0
    "$@" >"$test_dir/out" 2>"$test_dir/err" || status=$?
    if [ "$status" -ne "$expected" ]; then
        echo "FAIL $label: expected exit $expected, got $status" >&2
        cat "$test_dir/err" >&2
        exit 1
    fi
    if [ ! -s "$test_dir/err" ]; then
        echo "FAIL $label: missing error diagnostic" >&2
        exit 1
    fi
    if [ -s "$test_dir/out" ]; then
        echo "FAIL $label: rejection wrote a success/output line" >&2
        cat "$test_dir/out" >&2
        exit 1
    fi
    checks=$((checks + 1))
}

expect_rejected "send missing argument" 2 "$send"
expect_rejected "send extra mask argument" 2 "$send" 123456 111111
expect_rejected "send short color" 2 "$send" 12345
expect_rejected "send long color" 2 "$send" 1234567
expect_rejected "send nonhex color" 2 "$send" 12345g
expect_rejected "send shell-style color" 2 "$send" '#123456'
expect_rejected "send whitespace" 2 "$send" '123456 '
expect_rejected "init missing argument" 2 "$init"
expect_rejected "init extra argument" 2 "$init" "$test_dir/panel.json" extra
expect_rejected "init missing file" 3 "$init" "$test_dir/does-not-exist.json"

printf '%s\n' '{"pixel_type":"ws2812b","string_lengths":[0,0,0,0,0]}' >"$test_dir/panel.json"
expect_rejected "init short length array" 2 "$init" "$test_dir/panel.json"
printf '%s\n' '{"pixel_type":"ws2812b","string_lengths":[301,0,0,0,0,0]}' >"$test_dir/panel.json"
expect_rejected "init oversized length" 2 "$init" "$test_dir/panel.json"
printf '%s\n' '{"pixel_type":"ws2812b","pixel_type":"ws2811-hs","string_lengths":[0,0,0,0,0,0]}' >"$test_dir/panel.json"
expect_rejected "init duplicate field" 2 "$init" "$test_dir/panel.json"
printf '%s\n' '{"pixel_type":"ws2812b","string_lengths":[0,0,0,0,0,0],"extra":1}' >"$test_dir/panel.json"
expect_rejected "init unknown field" 2 "$init" "$test_dir/panel.json"
printf '%s\n' '{"pixel_type":"not-a-profile","string_lengths":[0,0,0,0,0,0]}' >"$test_dir/panel.json"
expect_rejected "init unknown profile" 2 "$init" "$test_dir/panel.json"

echo "PASS CLI rejection: $checks checks"
