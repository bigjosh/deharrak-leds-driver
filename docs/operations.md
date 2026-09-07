# Operate DLD on a BBG

Use the [SSH trial launcher](trial.md) for a temporary replacement of LEDscape;
it stages the bundle, prepares the board, and starts the UDP receiver. A
[Raspberry Pi gateway](gateway.md) can run that launcher on a remote network.
This guide covers manual operation from a matching [native build](build.md),
configuration changes, command results, and returning hardware ownership.

## Runtime prerequisites

The supported target is the AM335x BeagleBone Green running Linux
`3.8.13-bone80`. Run the hardware commands as root. The board must have:

- All six [data pins](configuration.md) already muxed as GPIOs, `uio_pruss`
  available, and `/dev/mem` access. DLD does not configure pinmux.
- Exclusive use of both PRUs and the six data pins. Stop LEDscape and exclude
  any independently launched renderer before initialization.
- Only CPU0 online, fixed at 1 GHz, no swap, and an available PMU cycle
  counter. The preparation tool sets the CPU policy and disables the four
  user-LED triggers; it does not take a PMU counter away from another user.
- A running interface named `eth0`, driven by `cpsw`, even if all six strings
  are disabled. Other peripherals must meet the
  [DMA ownership assumptions](quiet-window.md#dma-checks-and-ownership).
- Executable `/run` tmpfs for runtime binaries, logs, and command locks. Keep
  the normal root filesystem mounted; do not freeze it.

Use commands and `dld_quiet.ko` from the same build. The helper is required:
there is no userspace-only transmission fallback. A different kernel or
peripheral setup needs separate qualification; matching a kernel version alone
does not establish all hardware ownership assumptions.

## Stage a native build in RAM

Skip this section when using the trial launcher. For manual operation, first
complete the [direct native build](build.md#build-directly-on-the-bbg) with
`LOCK_PATH=/run/dld.lock`. The Windows build wrapper uses a private build lock;
create a fresh build with the runtime lock before following these manual steps,
or use the [trial packaging and deployment procedure](trial.md) instead. Moving
a binary does not change its compiled lock path.

From that build's source directory, with the reviewed `config/panel.json`:

```sh
set -e
umask 077
DLD_RUNTIME_DIR=$(mktemp -d /run/dld-manual.XXXXXX)
mkdir "$DLD_RUNTIME_DIR/build" "$DLD_RUNTIME_DIR/kernel" \
      "$DLD_RUNTIME_DIR/tools" "$DLD_RUNTIME_DIR/config"
cp build/dld-init build/dld-send build/dld-udp build/build-report.txt \
   "$DLD_RUNTIME_DIR/build/"
cp kernel/dld_quiet.ko "$DLD_RUNTIME_DIR/kernel/"
cp tools/bench_prepare.py tools/start-test.sh "$DLD_RUNTIME_DIR/tools/"
cp config/panel.json "$DLD_RUNTIME_DIR/config/"
printf 'Runtime directory: %s\n' "$DLD_RUNTIME_DIR"
cd "$DLD_RUNTIME_DIR"
```

Record the printed path; subsequent commands run from this directory. The
files remain until reboot. Keep the build report with the running files, and
copy any logs or preparation records off the board before reboot if needed.
Staging files does not stop an existing session or take hardware ownership.

## Start the driver

Stop existing DLD callers and exclude independent PRU/GPIO users first.
From the runtime directory, prepare the board, load the matching helper, and
hand over the named LEDscape service:

```sh
set -e
modprobe uio_pruss
python3 tools/bench_prepare.py --apply "$PWD/preparation-state.json"
insmod kernel/dld_quiet.ko
sh tools/start-test.sh config/panel.json
```

Despite their bench-oriented names, these are the supplied preparation and
handover tools. `start-test.sh` stops `ledscape.service`, verifies it has
stopped, and calls `build/dld-init`. It does not find or stop a separately
launched starfield sender or another renderer. The core commands themselves
do not manage services.

Use a new preparation-state filename and retain it for restoration. The tool
saves the prior CPU and user-LED settings before changing them and refuses to
overwrite that record. If this session is already prepared and its matching
helper is loaded, skip preparation and `insmod`, but still confirm ownership
before initialization. Run `start-test.sh` if LEDscape's service state is
uncertain. Resolve an existing module from a different build or a PMU user
before loading the helper. Stop on any setup error and read its diagnostic
before continuing; already completed preparation may still need restoration.

Initialization configures all six pins as outputs held low, stores the panel
configuration in PRU memory, and starts the embedded firmware. The firmware
and configuration remain after `dld-init` exits. Initialization sends no pixel
data, so a healthy idle panel keeps its latched color. After an interrupted
frame, forcing the pins low can latch partial data instead; initialization
cannot guarantee preservation of the prior display.

After reboot, repeat startup, including RAM staging, preparation with a new
state file, module loading, and initialization. Reestablish ownership and
initialize again after PRU/GPIO state loss or use by another application.
These procedures change runtime state only; they do not install a boot service
or disable LEDscape at boot.

## Send colors from an application

Invoke `build/dld-send RRGGBB` synchronously for each update:

```sh
build/dld-send FF0000  # Red
build/dld-send 00FF00  # Green
build/dld-send 000000  # Black
```

The color must contain exactly six hexadecimal digits, optionally prefixed
with `0x` or `0X`. Arguments are logical RGB; the initialized profile determines
wire order. Every enabled pixel receives that color. Exit status zero means
the PRU reported completion after its full final reset/propagation/margin
hold. Success prints one line beginning with `OK`; errors go to stderr.

Wait for one command to finish before starting the next. A nonblocking lock
rejects overlapping initialization or sends; DLD does not queue them. Use one
build's commands and lock path for the active session, and never delete or
replace its lock file while a sender has it open. The standalone command needs
no resident process, and pixels need no continuous refresh to retain their
last color. Stop `dld-udp` before manual command testing or configuration
changes; a conflicting send fails rather than waiting.

The PRU drives one GPIO bank at a time. A 300-pixel bank uses a nominal 9.24 ms
protected window, plus setup and restoration overhead. Linux and networking
resume between banks. Whole-command latency includes all active banks, final
settling, admission waits, and Linux scheduling. The
[protected-operation guide](quiet-window.md) explains these bounds and the
hardware checks.

## Receive colors over UDP

After successful initialization, start the receiver in the foreground:

```sh
build/dld-udp
```

It accepts the first logical RGB pixel from OPC-over-UDP packets on port 7890
and uses the same sender as `dld-send`. The configuration file is not reopened
per packet. By default it flashes green at startup and red after more than
a minute without received UDP traffic. Suppress either behavior independently:

```sh
build/dld-udp --no-startup-flash --no-idle-flash
```

Stop with Ctrl+C or `SIGTERM`. Stop the receiver before changing configuration,
unloading the helper, or returning control to LEDscape. Sender failures are
fatal to the receiver; it does not retry or reinitialize. The
[UDP guide](udp.md) covers bind options, packet validation, flash behavior,
coalescing, counters, and recovery. Ethernet reception pauses during protected
bank windows, so packets can be lost; successful UDP transmission is not an
acknowledgment of a completed panel update. See the
[scene exerciser](exercise.md) for a separate controller-side test program.

## Change the panel configuration

Stop the receiver and all other callers, edit the active runtime copy of
`config/panel.json`, then run:

```sh
build/dld-init config/panel.json
```

Restart the receiver only after successful initialization. Editing JSON alone
does not change the retained PRU configuration; both senders use that retained
state until the next initialization. A failed initialization can invalidate the
previous session. The [configuration guide](configuration.md) defines the
profiles, string lengths, pin mapping, and how to turn strings black before
disabling them. Changes to a file in `/run` disappear on reboot; keep the
reviewed panel configuration on the deployment computer as well.

## Handle errors

Check the exit status and preserve stderr before deciding what to do next:

| Exit | Meaning and action |
|---:|---|
| 0 | Completed successfully, or the UDP receiver stopped normally. |
| 2 | Invalid color, arguments, or configuration; correct the input. |
| 3 | Missing prerequisite, unreadable configuration, or socket error; resolve the reported cause. |
| 4 | Initialization failed; resolve the cause and initialize again. |
| 5 | Critical or uncertain send failure; stop updates, inspect the error, and reinitialize before resuming. |
| 6 | Command/session busy; allow the active operation to finish and coordinate callers. |
| 7 | Invalid or uninitialized session; run `dld-init` with the panel configuration. |

DLD never automatically retransmits a failed frame or reinitializes after a
critical error. Once a request is published, the kernel owns completion or
failure cleanup even if the sender is killed. Critical cleanup attempts to
stop the PRUs and hold outputs low; inaccessible hardware can prevent recovery.
Forcing low after an interrupted frame can latch partial data and does not
black out the panel. See
[failure and restoration](quiet-window.md#failure-and-restoration) for
diagnostics and cancellation behavior.

## Return control to LEDscape

For the [RAM-only SSH trial](trial.md), reboot to return to the existing
LEDscape boot setup. Copy any trial logs off the board first if needed.

For a manually prepared session without rebooting, stop `dld-udp`, the calling
application, and all send loops; wait for active commands to finish. From this
session's runtime directory, use its saved preparation state:

```sh
set -e
rmmod dld_quiet
python3 tools/bench_prepare.py --restore "$PWD/preparation-state.json"
systemctl start ledscape.service
```

Restoration is explicit. Verify CPU policy, user-LED settings, Ethernet, and
MMC operation afterward. Retain failure logs and the saved state until recovery
is confirmed. Once LEDscape owns the hardware, perform the handover and
initialization again before using DLD.
