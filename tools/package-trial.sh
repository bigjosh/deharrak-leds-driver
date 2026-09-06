#!/bin/sh
# Build a portable reference-BBG trial pack without changing installed files.
# Run on the native build board, from any directory. Temporary build stays in
# /run until reboot; only the requested archive is copied to the source tree.
set -eu
if [ "$#" -gt 1 ]; then
    echo 'usage: sh tools/package-trial.sh [OUTPUT.tar.gz]' >&2
    exit 2
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=${1:-$project_dir/build/dld-trial.tar.gz}
case "$output" in /*) ;; *) output=$PWD/$output ;; esac
if [ -e "$output" ] || [ -L "$output" ]; then
    echo "refusing to overwrite existing package: $output" >&2
    exit 2
fi
if [ "$(uname -r)" != '3.8.13-bone80' ] || [ "$(uname -m)" != 'armv7l' ]; then
    echo 'build on the reference ARMv7 Linux 3.8.13-bone80 BBG' >&2
    exit 3
fi
python3 -B - <<'PY'
with open('/proc/mounts') as source:
    mounts = [line.split() for line in source]
run = [entry for entry in mounts if entry[1] == '/run']
if len(run) != 1 or run[0][2] != 'tmpfs' or 'noexec' in run[0][3].split(','):
    raise SystemExit('/run must be executable tmpfs')
with open('/proc/swaps') as source:
    if len(source.read().splitlines()) != 1:
        raise SystemExit('swap must be absent')
PY
umask 077
workspace=$(mktemp -d /run/dld-package.XXXXXX)
printf 'Building trial package in %s\n' "$workspace"
mkdir "$workspace/source" "$workspace/package"
cd "$project_dir"
tar --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='kernel/*.o' --exclude='kernel/*.ko' --exclude='kernel/*.dis' \
    --exclude='kernel/*.mod.c' --exclude='kernel/.*.cmd' --exclude='kernel/.*.d' \
    --exclude='kernel/.tmp_versions' --exclude='kernel/Module.symvers' \
    --exclude='kernel/modules.order' \
    -cf "$workspace/source.tar" Makefile .gitignore include src pru kernel vendor config tools tests
cd "$workspace/source"
tar -xmf "$workspace/source.tar"
# Fresh objects are essential: Make does not track changes to LOCK_PATH.
make -j2 LOCK_PATH=/run/dld.lock all build/dld-config-check test-native report
python3 -B tests/test_trial_remote.py
python3 -B tests/test_deploy_trial.py
build/dld-config-check config/panel.example.json
package=$workspace/package
mkdir "$package/build" "$package/kernel" "$package/tools"
cp build/dld-init build/dld-send build/dld-udp build/dld-config-check build/build-report.txt "$package/build/"
cp kernel/dld_quiet.ko "$package/kernel/"
cp tools/bench_prepare.py tools/trial-remote.py "$package/tools/"
cd "$package"
python3 -B - <<'PY'
import hashlib, json, platform
files = ('build/dld-init', 'build/dld-send', 'build/dld-udp',
         'build/dld-config-check', 'build/build-report.txt',
         'kernel/dld_quiet.ko', 'tools/bench_prepare.py', 'tools/trial-remote.py')
manifest = {'schema': 1, 'kernel_release': platform.release(),
            'lock_path': '/run/dld.lock', 'files': {}}
for name in files:
    with open(name, 'rb') as source:
        manifest['files'][name] = hashlib.sha256(source.read()).hexdigest()
with open('manifest.json', 'w') as destination:
    json.dump(manifest, destination, indent=2, sort_keys=True)
    destination.write('\n')
PY
tar -czf "$workspace/dld-trial.tar.gz" manifest.json \
    build/dld-init build/dld-send build/dld-udp build/dld-config-check build/build-report.txt \
    kernel/dld_quiet.ko tools/bench_prepare.py tools/trial-remote.py
mkdir -p "$(dirname -- "$output")"
# Noclobber prevents two builders from replacing the same published archive.
(set -C; cat "$workspace/dld-trial.tar.gz" > "$output")
printf '\nTrial package: %s\nRetained build: %s\n' "$output" "$workspace"
sha256sum "$output"
echo 'No services, modules, PRUs or GPIOs were changed.'
