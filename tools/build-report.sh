#!/bin/sh
set -eu
printf 'DLD native build report\n'
printf 'CFLAGS=%s\nLOCK_PATH=%s\n' "${DLD_REPORT_CFLAGS:-see Makefile}" "${DLD_REPORT_LOCK:-see build invocation}"
uname -a
gcc --version | head -n 1
make --version | head -n 1
ld --version | head -n 1
getconf GNU_LIBC_VERSION
dpkg --print-architecture
printf '\nArtifact sizes\n'
wc -c build/pru.bin build/dld-init build/dld-send kernel/dld_quiet.ko
size build/dld-init build/dld-send
printf '\nARM binary attributes\n'
readelf -A build/dld-init
readelf -A build/dld-send
printf '\nRuntime dependencies\n'
ldd build/dld-init
ldd build/dld-send
printf '\nArtifact hashes\n'
sha256sum build/pasm build/pru.bin build/dld-init build/dld-send kernel/dld_quiet.ko
modinfo kernel/dld_quiet.ko
printf '\nSource hashes\n'
sha256sum Makefile .gitignore config/panel.example.json
find include src pru vendor/pasm tools tests -type f ! -name '*.pyc' | LC_ALL=C sort | xargs sha256sum
sha256sum kernel/Makefile kernel/dld_quiet_main.c kernel/dld_quiet_spin.S kernel/dld_admission.h kernel/audit_quiet.py
