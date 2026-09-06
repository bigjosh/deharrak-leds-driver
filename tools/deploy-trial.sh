#!/bin/sh
# Copy an isolated RAM-only trial to a BBG and hand LEDscape's pins to DLD.
set -eu

usage() {
    printf '%s\n' 'usage: deploy-trial.sh TARGET PANEL_JSON [--bundle FILE] [--known-hosts FILE] [--no-startup-flash] [--no-idle-flash]' >&2
}

fail() { printf 'deploy-trial: %s\n' "$1" >&2; exit 2; }

if [ "$#" -eq 1 ] && [ "$1" = --help ]; then usage; exit 0; fi
[ "$#" -ge 2 ] || { usage; exit 2; }
target=$1
panel=$2
shift 2
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bundle=$script_dir/../build/dld-trial.tar.gz
bootstrap=$script_dir/trial-remote.py
known_hosts=
startup_flag=
idle_flag=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --bundle|--known-hosts)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case "$1" in --bundle) bundle=$2 ;; --known-hosts) known_hosts=$2 ;; esac
            shift 2 ;;
        --no-startup-flash) startup_flag=' --no-startup-flash'; shift ;;
        --no-idle-flash) idle_flag=' --no-idle-flash'; shift ;;
        *) usage; fail "unknown option: $1" ;;
    esac
done

# A target is an address/name, never an SSH option, username, or shell fragment.
case "$target" in
    *:*)
        case "$target" in *[!0123456789abcdefABCDEF:]*|*:::*) fail 'use an unbracketed numeric IPv6 address' ;; esac
        [ "${#target}" -le 39 ] || fail 'IPv6 address is too long'
        # Validate the compressed and full eight-group forms without needing
        # Python on the sending Linux machine. awk is part of POSIX.
        if ! awk -v address="$target" 'BEGIN {
            gap = index(address, "::"); count = 0;
            if (gap) {
                left = substr(address, 1, gap - 1); right = substr(address, gap + 2);
                if (index(right, "::")) exit 1;
                count += check(left); count += check(right);
                if (count >= 8) exit 1;
            } else if (check(address) != 8) exit 1;
        }
        function check(text, parts, n, i) {
            if (text == "") return 0;
            n = split(text, parts, ":");
            for (i = 1; i <= n; i++) if (length(parts[i]) < 1 || length(parts[i]) > 4) exit 1;
            return n;
        }' </dev/null; then fail 'invalid IPv6 address'; fi
        scp_host=[$target] ;;
    *)
        case "$target" in ''|[-.]*|*[-.]|*[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-]*|*..*)
            fail 'use a plain hostname, IPv4 address, or unbracketed numeric IPv6 address' ;;
        esac
        [ "${#target}" -le 253 ] || fail 'hostname is too long'
        scp_host=$target ;;
esac
for tool in ssh scp; do command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"; done
for file in "$bundle" "$panel" "$bootstrap"; do
    [ -f "$file" ] && [ -r "$file" ] && [ -s "$file" ] || fail "missing, empty, or unreadable local file: $file"
done
if [ -n "$known_hosts" ]; then
    [ -f "$known_hosts" ] && [ -r "$known_hosts" ] || fail "cannot read known-hosts file: $known_hosts"
    known_hosts=$(CDPATH= cd -- "$(dirname -- "$known_hosts")" && printf '%s/%s' "$(pwd)" "$(basename -- "$known_hosts")")
    case "$known_hosts" in *'"'*|*'\'*|*'
'*|*"$(printf '\r')"*) fail 'known-hosts path must not contain quotes, backslashes, or newlines' ;; esac
fi
# Absolute local paths prevent scp from interpreting a colon in a filename as
# the remote-host separator. No local shell evaluates any supplied path.
bundle=$(CDPATH= cd -- "$(dirname -- "$bundle")" && printf '%s/%s' "$(pwd)" "$(basename -- "$bundle")")
panel=$(CDPATH= cd -- "$(dirname -- "$panel")" && printf '%s/%s' "$(pwd)" "$(basename -- "$panel")")

# Accept keys for this deployment, including a different board reusing an IP.
# Normal SSH trust files are untouched; --known-hosts optionally records keys.
set -- -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
    -o CheckHostIP=no -o GlobalKnownHostsFile=/dev/null
if [ -n "$known_hosts" ]; then
    set -- "$@" -o "UserKnownHostsFile=\"$known_hosts\""
else
    set -- "$@" -o UserKnownHostsFile=/dev/null
fi

# Keep all writes beneath a fresh tmpfs directory. The bootstrap repeats these
# checks and verifies the complete bundle before any service/runtime changes.
prepare='set -eu
[ `id -u` -eq 0 ] || { printf "%s\n" "root SSH access is required" >&2; exit 3; }
command -v python3 >/dev/null
command -v mktemp >/dev/null
python3 -B -c '\''import sys
mounts = [line.split() for line in open("/proc/mounts")]
run = [m for m in mounts if m[1] == "/run"]
if len(run) != 1 or run[0][2] != "tmpfs" or "noexec" in run[0][3].split(","):
    sys.exit("/run must be executable tmpfs")
if len(open("/proc/swaps").read().splitlines()) != 1:
    sys.exit("active swap would prevent a RAM-only trial")'\''
umask 077
mktemp -d /run/dld-trial.XXXXXX'
if remote_dir=$(ssh "$@" "root@$target" "$prepare"); then :; else
    code=$?
    printf 'deploy-trial: remote RAM-directory preflight failed (exit %s)\n' "$code" >&2
    exit "$code"
fi
case "$remote_dir" in
    /run/dld-trial.??????)
        suffix=${remote_dir#/run/dld-trial.}
        case "$suffix" in *[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789]*) fail 'unexpected remote directory response' ;; esac ;;
    *) fail 'unexpected remote directory response' ;;
esac
printf 'Trial directory: %s:%s\n' "$target" "$remote_dir"
printf '%s\n' 'On failure, files/logs stay in RAM; reboot the BBG to recover.'

copy_file() {
    if scp -O "$@"; then :; else
        code=$?
        printf 'deploy-trial: transfer failed; retained %s (exit %s)\n' "$remote_dir" "$code" >&2
        exit "$code"
    fi
}
copy_file "$@" "$bundle" "root@$scp_host:$remote_dir/bundle.tar.gz"
copy_file "$@" "$panel" "root@$scp_host:$remote_dir/panel.json"
copy_file "$@" "$bootstrap" "root@$scp_host:$remote_dir/trial-bootstrap.py"
if ssh "$@" "root@$target" "cd $remote_dir && python3 -B trial-bootstrap.py --bundle bundle.tar.gz --panel panel.json$startup_flag$idle_flag"; then
    printf 'DLD trial is running from %s:%s\n' "$target" "$remote_dir"
else
    code=$?
    printf 'deploy-trial: handover failed; inspect %s (exit %s); reboot to recover\n' "$remote_dir" "$code" >&2
    exit "$code"
fi
