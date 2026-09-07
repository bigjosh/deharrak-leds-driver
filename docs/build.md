# Building DLD

Build the ARM commands, embedded PRU firmware, and matching kernel helper on
the BeagleBone Green. The Windows wrapper copies this checkout into a fresh
directory and runs the native build there; it needs no ARM cross compiler.
For an existing compatible board, the [prebuilt gateway release](gateway.md)
avoids building altogether. See [manual operation](operations.md) for hardware
handover after a source build, or the [trial guide](trial.md) to deploy a bundle
over SSH.

## Supported environment

The validated target is the deployed armhf BeagleBone Green running Linux
`3.8.13-bone80`, with GCC 4.6.3, Make 3.81, binutils 2.22, Python 3.2, and
glibc 2.13. Its kernel build tree must contain the exact running kernel's
prepared headers, generated configuration, and `Module.symvers`. The default
location is `/lib/modules/$(uname -r)/build`. Headers for another kernel are
not a substitute, and another kernel or userspace needs separate qualification.

The build uses the project's vendored PASM assembler, so no TI toolchain
installation or paid tools are needed. The native report also uses the board's
existing `readelf`, `size`, `ldd`, `sha256sum`, `modinfo`, `getconf`, and `dpkg`.
No packages or system tools need changing on the validated target.

Use a dedicated source directory outside the existing LEDscape tree. Building
and the standard tests do not load the module, change services, initialize
GPIOs, or send pixel data. Compilation does create CPU and storage load, so
keep it outside an exclusive waveform-measurement interval.

## Build from Windows

The Windows PC needs PowerShell, `ssh`, `scp` with legacy-protocol `-O`
support, and `tar` on PATH. Root SSH access must already work without a
password prompt. Verify the target's host key and establish its known-hosts
entry before running the wrapper: both transfer and remote execution use
`BatchMode=yes`.

Run from the repository root:

```powershell
.\tools\build-bbg.ps1
```

If Windows blocks PowerShell scripts, invoke this wrapper with a process-only
execution-policy override:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build-bbg.ps1
```

This does not change the machine's saved execution policy.

The default host is `beaglebone`. To select another configured host and an
explicit new directory:

```powershell
.\tools\build-bbg.ps1 -HostName beaglebone -RemoteDirectory /root/dld-release-001
```

`HostName` must be a plain hostname or IP address, without a username or port.
The remote path must be a fresh
`/root/dld-NAME` directory without spaces or nested paths; the script refuses
to reuse an existing directory. Omitting it generates a timestamped, unique
`/root/dld-build-...` name.

The wrapper writes `build/source.tar` locally, copies it into the new remote
directory, and runs:

```sh
make -j2 LOCK_PATH=/root/dld-NAME/dld.lock all test report
```

It substitutes the actual directory for `/root/dld-NAME`. Sources, build
outputs, and `source.tar` remain on the BBG in that directory; the wrapper
does not download the resulting executables. Its final output identifies
the directory. On failure, inspect that same directory rather than rerunning
against it. The normal instruction-model audit takes several minutes on
the BBG's Python 3.2 runtime.

The wrapper's private lock is useful for an isolated build, but it is not the
RAM lock used by deployed releases. For deployment, use
[`tools/package-trial.sh`](#package-a-temporary-ssh-deployment), which rebuilds
in a fresh workspace with `/run/dld.lock`. Copying an executable to `/run` does
not change its compiled lock path.

## Build directly on the BBG

From a fresh, dedicated source directory, build with the shared runtime lock
used by the deployment tools:

```sh
make -j2 LOCK_PATH=/run/dld.lock
make LOCK_PATH=/run/dld.lock test report
```

`make` builds all three commands and the helper. `make test` runs pure C and
admission-policy checks, CLI rejection, shared-sender and UDP loopback tests, the PRU
instruction model, and the emitted kernel instruction audit. It does not
access GPIO or PRU hardware. The separately invoked live and host Python
suites are documented in the [test guide](../tests/README.md).

If the matching kernel build tree is elsewhere, pass the same override on
each build/test/report invocation:

```sh
make -j2 KDIR=/path/to/matching/build LOCK_PATH=/run/dld.lock
make KDIR=/path/to/matching/build LOCK_PATH=/run/dld.lock test report
```

`LOCK_PATH` is compiled into the commands. Omitting it selects
`/var/lock/dld.lock`; an environment variable at runtime cannot change that
choice. Use all three commands from the same build. Build-local locks do not
coordinate different builds, so run only one build's initialized session
at a time. A shared deployment should compile every cooperating command
with the same absolute lock path. Keep runtime binaries, logs, and the lock
in `/run` for the protected operating setup; [manual operation](operations.md)
shows how to stage a native build there. Do not delete or replace the lock file
while any sender still has it open.

Make does not track changed compiler options or lock locations. Before
changing those in an existing build, run `make clean` and rebuild. That target
only removes the listed userspace and firmware outputs. Clean kernel objects
separately when changing kernel or module build options:

```sh
make clean
make -C /lib/modules/$(uname -r)/build M="$PWD/kernel" clean
make -j2 LOCK_PATH=/run/dld.lock
make LOCK_PATH=/run/dld.lock test report
```

Use the matching custom kernel path in the clean command if `KDIR` was
overridden, and keep that override on the subsequent top-level commands.

## Package a temporary SSH deployment

For the [RAM-only trial launchers](trial.md), run this from a dedicated native
source directory on the BBG:

```sh
sh tools/package-trial.sh
```

The helper creates `build/dld-trial.tar.gz`, containing the ARM commands,
matching kernel module, runtime handover tools, configuration checker,
manifest, and build report. It uses a fresh build workspace under `/run` and
builds the commands with the shared lock path `/run/dld.lock`, keeping the
lock in RAM alongside the deployed package. Use this helper instead of
archiving arbitrary previous build outputs whose compiled lock path may
point at persistent storage. An optional single output-path argument selects
an archive destination other than `build/dld-trial.tar.gz`; existing outputs
are refused. The temporary source/build directory remains in `/run` until
reboot. The helper runs `test-native` plus the mocked remote-handover and
launcher suites before packaging, without initializing hardware.

Packaging uses the installed native compiler and matching kernel build tree;
it does not install packages or take hardware ownership. Copy the resulting
archive back to the local checkout before running either deployment launcher.
Keep the local launchers and bundle from the same source version; the remote
bootstrap rejects a bundle containing a different copy of its helper.
The [trial guide](trial.md) gives Windows and Linux commands, prerequisites,
and the reboot recovery procedure.

For a versioned GitHub gateway release, use Python 3.8+ on Windows, Linux, or
the Pi to wrap this tested native bundle with the matching deployment scripts,
configuration example, and documentation. The
[gateway release instructions](gateway.md#release-contents-and-verification)
describe `tools/package-gateway.py` and its SHA-256 sidecar. This outer
packaging step does not compile or qualify new BBG binaries. A gateway archive
contains deployment files, not the full native source checkout.

## Outputs and build record

Paths below are relative to the source directory where the native build ran.

| Path | Purpose |
|---|---|
| `build/dld-init` | Initializer containing the embedded PRU image |
| `build/dld-send` | Uniform-color sender |
| `build/dld-udp` | Resident first-pixel OPC/UDP receiver, using the same sender |
| `build/dld-config-check` | JSON/configuration validator, built explicitly or by the trial packager |
| `kernel/dld_quiet.ko` | Matching protected-transmission helper |
| `build/pasm` | Project-local PRU assembler |
| `build/pru.bin` | PRU instruction image |
| `build/pru.lst`, `build/pru.txt` | Assembler listings |
| `build/pru_blob.c` | Generated image embedded in `dld-init` |
| `build/dld-init.dis`, `build/dld-send.dis`, `build/dld-udp.dis` | ARM disassembly produced by `make audit` |
| `kernel/dld_quiet.dis` | Linked module disassembly audited by `make audit` |
| `build/build-report.txt` | Toolchain, sizes, ARM attributes, dependencies, module metadata, and SHA256 hashes from `make report` |
| `build/dld-trial.tar.gz` | Temporary SSH deployment bundle produced by `tools/package-trial.sh` |

The build rejects an empty, misaligned, or larger-than-8,192-byte PRU image;
the loader checks the image size again before writing instruction RAM.
Retain the report with the binaries when moving a build into service. Keep
the commands, module, and initialization together when the mailbox ABI or
shared timing constants change. The [kernel guide](../kernel/README.md)
describes its kernel and instruction-audit requirements.

## Optional Windows-only PRU build

This path checks firmware assembly locally; the full utility still uses
the native BBG build. It needs an existing GCC-compatible Windows compiler
such as MinGW GCC on PATH. MSVC's `cl` command-line syntax is unsupported.

```powershell
.\tools\build-pru.ps1
```

To select a compiler explicitly:

```powershell
.\tools\build-pru.ps1 -Compiler C:\path\to\gcc.exe
```

The script writes `pasm.exe`, `pru.bin`, `pru.lst`, and `pru.txt` under
`build/windows-pru/`, validates instruction-memory size, and prints the
firmware's SHA256. Compare it with the native `build/pru.bin` hash in
`build/build-report.txt` for the same source revision. This script does not
build the ARM executables, kernel helper, or embedded-image C source, and
does not deploy firmware. PASM's source and license are in
[vendor/pasm](../vendor/pasm/README.dld.md).

Capture automation and offline analysis have separate optional host Python
dependencies; see the [capture-checker setup](../tools/analyze_capture.md).
