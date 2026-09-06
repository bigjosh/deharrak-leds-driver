# Deharrak LED driver

First-pass uniform-color driver for the deployed BeagleBone Green/Linux
3.8.13-bone80 system. It supplies two commands:

```sh
build/dld-init config/panel.example.json
build/dld-send 00FF00
```

`dld-init` reads a pixel profile and six string lengths, makes all six GPIOs
outputs held low, and starts the embedded PRU0 firmware. It sends no pixel
data. `dld-send` reuses that initialization, sends exactly the configured
length on each enabled string, waits through final settling, and checks the
PRU completion. The ABI4 firmware waits for a kernel grant before each bank.
`dld-send` requires the matching `dld_quiet.ko` helper and never falls back to
unprotected output. After success, both commands exit while the PRU and
configuration remain. Neither initialization nor cleanup sends a black frame.

The first pass is diagnostic. Protected header captures provide physical timing
evidence; installed pixel family, color order, downstream timing, and chain
settling still require qualification. See the [current physical bench
report](docs/validation-20260906.md), [initial bring-up report](docs/validation.md),
and [spec.md](spec.md) for measured evidence and the full contract.

The September 6 protected endurance run completed fourteen full phases and a
shortened final black phase: 155,917 successful sends, no failed sends, and
no observed waveform violations in 4,452,206,525 complete captured pulses.
The recorded intervals total 18,257.095062016 seconds; acquisition gaps remain
unobserved. Verified handback on September 6 at 13:10 UTC left the tested helper and initialized
six-string session ready, with all six data outputs low. This completes the
bench endurance work; installed-panel qualification and production rollout
remain open. This is a historical handback record, not a check of the board's
present state; the retained `/run` files and initialization do not survive reboot.

## Build from Windows on the BBG

Run from the repository root. The Windows PC needs PowerShell plus `ssh`,
`scp` with legacy-protocol `-O` support, and `tar` on PATH. Root SSH access to
the chosen BBG must already work without a password prompt, and its host key
must already be verified in the SSH known-hosts file: the build uses
`BatchMode=yes`. The default hostname is `beaglebone`.

The validated BBG already had GCC 4.6.3, Make 3.81, binutils 2.22, Python 3.2,
glibc 2.13/armhf, and headers matching Linux `3.8.13-bone80`, including its
generated configuration and `Module.symvers`. On that environment no new
packages, paid tools, Windows C compiler, or cross-compilation sysroot are
needed. Another kernel or userspace is not a validated substitute.

```powershell
.\tools\build-bbg.ps1
```

Optional `-HostName HOST` and `-RemoteDirectory /root/dld-NAME` parameters
select an already configured SSH host and a new directory; the script refuses
to reuse an existing remote directory.

This creates a new `/root/dld-build-...` directory, copies the source, builds
the project-local PASM assembler, and runs the automated tests. It leaves
existing board files and services alone; compilation can still add CPU and
storage load, so do not run it during an exclusive waveform measurement.
It retains the source archive and outputs in that new remote directory and
does not install or load the module. Each build uses its own command
lock within that new directory. Use both commands from the same build and
run only one build's initialized session at a time: different build-local
locks do not coordinate each other.

The full instruction-model audit takes several minutes on the BBG's older
Python runtime; it is part of the normal test command.

To build manually inside a dedicated source directory on the BBG:

```sh
make -j2 LOCK_PATH="$PWD/dld.lock"
make LOCK_PATH="$PWD/dld.lock" test report
```

Without `LOCK_PATH`, the fixed default is `/var/lock/dld.lock`. This is a
compile-time deployment choice, not an environment override. Run `make clean`
before changing userspace compiler options or the lock location in an existing
build. The top-level `clean` target does not clean kernel objects; for a module
rebuild after changing kernel/build options, also use
`make -C /lib/modules/$(uname -r)/build M="$PWD/kernel" clean`. `KDIR=/path/to/matching/build`
overrides the top-level kernel build location.
Generated userspace/firmware files stay under `build/`; the module is
`kernel/dld_quiet.ko`. `make` rejects an oversized PRU image;
the loader checks the image size again before writing instruction RAM.

Optional Windows-only firmware build:

```powershell
.\tools\build-pru.ps1
```

This optional path needs an existing GCC-compatible Windows compiler, such as
MinGW GCC, on PATH; it does not accept MSVC's `cl` command-line syntax. Use
`-Compiler C:\path\to\gcc.exe` if needed. It writes PASM, firmware, listings,
and a printed firmware SHA256 under `build/windows-pru/`; compare that hash
with the native `build/pru.bin` before using the image. It does not build the
ARM executables or kernel module. The assembler's source and license are under
`vendor/pasm/`. Offline capture analysis has separate optional Python/NumPy
requirements in [the checker guide](tools/analyze_capture.md).

## Panel configuration

```json
{
  "pixel_type": "ws2812b",
  "string_lengths": [300, 180, 0, 0, 0, 0]
}
```

Lengths are exact integers from 0 to 300, in this order:

| Index | Pin | GPIO |
|---:|---|---|
| 0 | P8_8 | GPIO2[3] |
| 1 | P8_10 | GPIO2[4] |
| 2 | P8_12 | GPIO1[12] |
| 3 | P8_14 | GPIO0[26] |
| 4 | P8_16 | GPIO1[14] |
| 5 | P8_18 | GPIO2[1] |

Zero disables a string. Its pin remains low; no black frame is sent to it.
Changing this file takes effect on the next `dld-init` only. Config files
reject unknown/duplicate/missing fields, malformed JSON and invalid lengths.

The initial profiles are `ws2812b` (GRB wire order), `ws2811-hs` (RGB),
`ws2812b-bgr` and `ws2811-hs-bgr` (BGR module variants).
All use your 350 ns zero high, 700 ns one high and 1,200 ns bit period.
Reset is at least 300 us, with an initial 1 us per forwarding pixel and
100 us positive margin. These propagation/margin values are engineering
allowances awaiting measurement, not proven bounds for every LED revision.

The earlier reference board's LEDscape config says **BGR**; the replacement
waveform-bench board's config says **GBR** and 100 pixels per string. Neither
identifies the precise pixel family. The current catalog has no GBR profile.
Black/white diagnostics are byte-order independent; profile tests verify wire
encoding, while actual panel color order still requires a separate LED check.

## Hardware handover and use

The device tree must already pinmux the six pins as GPIOs. Run as root, with
`uio_pruss` loaded and `/dev/mem` available for one-time GPIO/clock setup.
Exclude all other PRU/GPIO users before initialization, including LEDscape.

The protected path requires only CPU0 online, fixed 1 GHz, an available Linux
PMU cycle counter, and a running `eth0` owned by the `cpsw` driver, even for
all-disabled sends. It also requires the checked AM335x peripheral
configuration and exclusive ownership of both PRUs and all six GPIOs. It masks
IRQ/FIQ and scheduling during each bank, idles CPSW DMA, and verifies MMC/EDMA
inactivity. Networking resumes between banks; incoming packets can be dropped
during each idle window. See [the quiet-window guide](docs/quiet-window.md)
for exact assumptions, restoration, and diagnostic limits.

The maximum-length bank timer is 9.24 ms at 300 pixels, plus admission and
restoration overhead. Transient DMA activity uses a 1 second admission
deadline and a 10,000-attempt cap, with CPU/CPSW state restored between attempts.
This never extends a granted bank's timer. Gate and final-completion waits
have separate 20 ms policies; total command latency also includes Linux
execution between banks. There is no PRU readiness or completion interrupt.
The normal bank timer is not a universal interrupt-off bound: a frozen-counter
fallback can take longer, and a wedged MMIO transaction is outside software
timeout guarantees.

For a new test session, first exclude independent/manual PRU or GPIO users.
On the supplied systemd board, the explicit test wrapper stops only
`ledscape.service`, verifies inactive/failed state with MainPID zero, and
initializes DLD. Run the following from the chosen build's source directory
only when that hardware handover is intended:

```sh
python3 tools/bench_prepare.py --apply "$PWD/build/preparation-state.json"
insmod kernel/dld_quiet.ko
sh tools/start-test.sh config/panel.example.json
build/dld-send 000000
build/dld-send FFFFFF
```

The preparation-state filename must be new, with an existing parent directory;
the script refuses to overwrite saved settings. It fixes CPU frequency and
disables user-LED triggers, but neither loads the UIO driver nor identifies
other DMA owners. A preexisting helper or PMU user must be resolved before
`insmod`; the commands do not replace another loaded module automatically.
The core `dld-init` does not require the helper, but protected sends and the
live suites do. `start-test.sh` does not stop a separately launched starfield
or another service merely because it uses the same PRUs.

These commands do not edit service files or boot configuration. For an explicitly
requested return to the original application, stop every send loop, unload
`dld_quiet` with `rmmod dld_quiet`, restore the saved settings using
`python3 tools/bench_prepare.py --restore "$PWD/build/preparation-state.json"`,
then run `systemctl start ledscape.service`. A later DLD session requires the handover
and initialization again. Restoration is not automatic: the September 6
handback retains the tested helper and CPU/LED preparation for continued DLD
use, as recorded in the bench report. The core CLI commands do not manage
services.

Accepted colors are exactly six hexadecimal digits, optionally prefixed by
`0x` or `0X`. Success is one `OK` line. Errors go to stderr:

| Exit | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid arguments/configuration |
| 3 | Missing privilege/prerequisite or unreadable configuration |
| 4 | Initialization/load/readiness failure |
| 5 | Published/uncertain send failure or post-publication cancellation; reinitialize |
| 6 | Command lock, kernel/network serialization busy, or previous PRU request outstanding |
| 7 | Invalid/uninitialized mailbox; initialize |

There are no automatic frame or granted-bank retries. A busy rejection or
prepublication prerequisite failure leaves the active session untouched.
After publication, the kernel owns completion or failure cleanup even if the
sender receives `SIGKILL`. Critical failure invalidates the session, attempts
to stop both PRUs and clear every LED bank, and requires `dld-init`.
Inaccessible hardware can prevent cleanup; diagnostics report that failure.
The helper's definitive ioctl `ENOTTY`/`EPERM` rejections return code 3 without
cleanup. Other syscall failures, including `EFAULT` on result copyout, leave
submission uncertain and take critical cleanup. Caught `SIGINT`, `SIGTERM`,
or `SIGHUP` after publication also cause code 5 and cleanup when the CLI regains
control; `SIGKILL` cannot run userspace cleanup, so the kernel finishes or fails
the accepted request independently. A successful accepted request can therefore
remain DONE after its caller was killed.
Reset after an interrupted frame can latch partial data; it does not restore
or black out the display.

## Tests and scope procedure

`make test` runs pure C and admission-policy checks, CLI rejection and
mocked ioctl-error tests, an instruction-level
audit of the assembled PRU binary, and checks of the kernel's emitted ARM loop
and its Thumb entry bridge for the deployed Thumb-2 kernel.
It does not touch PRU/GPIO state. See [tests/README.md](tests/README.md) for
separately invoked live tests and [pru/README.md](pru/README.md) for the timing
derivation and model limits.

The Python sender, storage-helper, and offline capture-checker tests are
separate from `make test`; their commands and dependencies are listed in the test guide. For a
logged, bounded traffic phase, use `tools/bench_sender.py` as documented there.

For each scope phase, initialize once with all six lengths 300. Arm the
positive pulse-width trigger at greater than 400 ns, then run:

```sh
sh tools/endurance.sh 000000 10800
```

Stop sending, change/rearm the trigger to greater than 800 ns, then run:

```sh
sh tools/endurance.sh FFFFFF 10800
```

This simpler shell wrapper sends sequentially and checks its three-hour limit
between commands. It stops on a command error, but has no child-process
watchdog: a hung command can exceed the requested duration. Use the logged
Python sender when a command watchdog or absolute UTC cutoff is required.
Record the actual scope setup, pin/bank, duration and any captures. A clean
run establishes only that no qualifying high pulse was observed on that pin.
These long-high trigger settings do not test short pulses, data lows, bit
periods, or all of the checker's ±50 ns windows; use captured-edge analysis
for those checks.
The protected kernel windows exclude ordinary ARM execution and quiesce the
checked DMA engines while pixel data is emitted. Instruction fetches still
exist; the small loop is warmed in cache before the grant. Physical captures
remain the acceptance test. The 2026-09-06 userspace-only implementation showed
repeated GPIO0 timing violations and is no longer the path under qualification.
