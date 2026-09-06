# Deharrak LED driver

DLD drives the Deharrak Clock's LED panels from a BeagleBone Green. It replaces
LEDscape's rendering role: initialize a panel once, then receive its existing
UDP color packets or send colors from the command line. Each panel supports
six strings of up to 300 pixels; every enabled pixel receives the same RGB color.

```sh
# On a prepared BBG, from the build's source directory:
build/dld-init config/panel.json
build/dld-send FF0000  # Red
build/dld-send 00FF00  # Green
build/dld-send 000000  # Black
# Or receive existing LEDscape OPC/UDP packets on port 7890:
build/dld-udp
```

The UDP shim uses the first RGB pixel of each accepted packet as the uniform
color for the entire panel. It runs in the foreground after initialization;
by default it flashes green at startup and red after a minute without UDP
traffic. Production boot-service installation remains separate integration work.

To replace LEDscape temporarily over SSH, use the supplied
[RAM-only trial procedure](docs/trial.md). After building its bundle once,
run either launcher from this checkout with the target address and local panel
configuration:

```powershell
.\tools\deploy-trial.bat 192.168.1.50 .\config\panel.json
```

```sh
sh tools/deploy-trial.sh 192.168.1.50 config/panel.json
```

The launcher automatically accepts the target's SSH host key and copies the
bundle into a fresh `/run` directory. After validation, it stops any existing
DLD session and LEDscape, replaces the helper, initializes the panel, and
starts `dld-udp` independently of SSH. Rerun the same command to update an
existing trial without rebooting. It changes runtime state without installing
a service or disabling LEDscape at boot. Reboot is the recovery procedure.

## How it works

`dld-init CONFIG_FILE` configures all six pins as outputs held low, stores the
panel configuration in PRU memory, and loads and starts the embedded firmware.
The firmware and configuration remain after the command exits. Initialization
sends no pixel data, so it does not turn an already lit panel black.

`dld-send RRGGBB` uses that retained configuration and returns only after all
enabled strings have received the color and the final reset/settling period
has elapsed. The PRU drives one GPIO bank at a time. Before each bank, the
required `dld_quiet` kernel helper pauses ARM execution and Ethernet DMA and
checks the relevant storage/DMA engines for inactivity. Linux and networking
resume between banks. This protects the GPIO timing on the existing wiring.

`dld-udp` receives the existing LEDscape OPC-over-UDP format and calls the same
shared C sender as `dld-send`. It keeps its device and memory mappings open,
reads the current initialized configuration from PRU memory for each send,
and performs no configuration-file I/O or process launch per packet.

The [protected-operation guide](docs/quiet-window.md) explains the timing,
ownership requirements, and failure handling in detail. The
[investigation and measurements](docs/validation-20260906.md) explain why this
approach was chosen and what was tested.

## Requirements and build

The supported target is the AM335x BeagleBone Green running Linux
`3.8.13-bone80` with its matching kernel build headers. Run the commands as
root. The board must have:

- The six pins below already muxed as GPIOs, `uio_pruss` available, and
  `/dev/mem` access. DLD does not configure pinmux.
- Exclusive use of both PRUs and the six data pins; LEDscape and any
  independently launched renderer must be stopped before initialization.
- Only CPU0 online, fixed at 1 GHz, no swap, and an available PMU cycle
  counter. The preparation tool below sets the CPU policy and disables the
  four user-LED triggers.
- A running interface named `eth0`, driven by `cpsw`, even when every string
  is disabled. Other peripheral ownership must meet the
  [supported hardware assumptions](docs/quiet-window.md#dma-checks-and-ownership).

From Windows, build and test in a fresh directory on the BBG:

```powershell
.\tools\build-bbg.ps1
```

The script uses `root@beaglebone`, prints the new build directory, and leaves
services and hardware ownership unchanged. Alternatively, build in a dedicated
source directory on the BBG:

```sh
make -j2
make test report
```

The outputs are `build/dld-init`, `build/dld-send`, `build/dld-udp`, and
`kernel/dld_quiet.ko`; firmware is embedded in `dld-init`. Use commands and a
module from the same build. See the [build guide](docs/build.md) for SSH and
compiler prerequisites, lock paths, rebuilding, and optional Windows PRU
assembly. Building and these tests do not load the module or take over the pins.

## Configure a panel

Create `config/panel.json` using [the example](config/panel.example.json),
with the actual pixel profile and number of pixels on each string:

```json
{
  "pixel_type": "ws2812b",
  "string_lengths": [300, 180, 0, 0, 0, 0]
}
```

Lengths must be integers from 0 through 300, in this order:

| Index | Header pin | GPIO |
|---:|---|---|
| 0 | P8_8 | GPIO2[3] |
| 1 | P8_10 | GPIO2[4] |
| 2 | P8_12 | GPIO1[12] |
| 3 | P8_14 | GPIO0[26] |
| 4 | P8_16 | GPIO1[14] |
| 5 | P8_18 | GPIO2[1] |

A zero length disables transmission on that string and holds its pin low.
It does not clear the string's previously latched color. To turn a string
black before disabling it, send `000000` while it is still enabled, then
change the configuration and reinitialize. That send turns every currently
enabled string black.

All pixels on a panel use the same profile:

| `pixel_type` | Wire color order |
|---|---|
| `ws2812b` | GRB |
| `ws2811-hs` | RGB |
| `ws2812b-bgr` | BGR |
| `ws2811-hs-bgr` | BGR |

Color arguments always use **RRGGBB**; DLD applies the profile's wire order.
Select the profile for the installed panel. The current catalog has no GBR
profile, and black/white tests cannot establish color order. All current
profiles use nominal 350 ns zero-high, 700 ns one-high, and 1,200 ns bit timing;
reset and propagation allowances are defined in the [specification](spec.md).
Installed-panel color order and downstream timing still need qualification.

The configuration file is read only by `dld-init`. Changes are applied by the
next initialization; both senders continue using the retained configuration
until then. A failed initialization can invalidate the previous session.

## Start the driver

For a temporary SSH replacement, the [trial launchers](docs/trial.md) perform
this handover and start the UDP receiver. The following commands are the
manual alternative for a native build already on the BBG.

Run from the chosen build's source directory on the BBG, as root, after
excluding independent PRU/GPIO users. These commands prepare the board, load
the helper, stop the named LEDscape service, and initialize the panel:

```sh
set -e
modprobe uio_pruss
python3 tools/bench_prepare.py --apply "$PWD/build/preparation-state.json"
insmod kernel/dld_quiet.ko
sh tools/start-test.sh config/panel.json
```

Despite their bench-oriented names, these are the supplied preparation and
handover tools. `start-test.sh` stops `ledscape.service`, verifies it has
stopped, and calls `build/dld-init` with your configuration. It does not find
or stop a separately launched starfield sender or other renderer.

Use a new preparation-state filename and retain it for restoration. The tool
saves the prior CPU and user-LED settings and refuses to overwrite that record.
If this session is already prepared and its matching helper is loaded, skip
preparation and `insmod`, but still confirm ownership before initialization.
Run `start-test.sh` if LEDscape's service state is uncertain. Resolve an existing
module from a different build or a PMU user before loading the helper. Stop on
any setup error and read its diagnostic before continuing.

For later configuration changes, stop the calling application, edit the panel
file, and run:

```sh
build/dld-init config/panel.json
```

After reboot, repeat the full startup sequence, including preparation with a
new state filename, module loading, and initialization. Reestablish ownership
and initialize again after PRU/GPIO state loss or use by another application.
Setup currently changes runtime state only; it does not install a boot service
or disable LEDscape at boot.

## Send colors from an application

Invoke `build/dld-send RRGGBB` synchronously for each update. The argument must
contain exactly six hexadecimal digits, optionally prefixed with `0x` or `0X`.
Exit status zero means the PRU reported completion after its final settling
wait. Success prints one line beginning with `OK`; errors go to stderr.

Wait for one command to finish before starting the next. DLD does not queue
overlapping commands; a nonblocking lock rejects concurrent initialization or
sends. Use one build's commands and lock path for the active session.
The command-line path needs no resident process, and no continuous refresh is
needed to retain the last color. Stop `dld-udp` before manual command testing
or configuration changes; a conflicting send fails rather than waiting.

## Receive colors from the existing controller

After [driver startup](#start-the-driver), run:

```sh
build/dld-udp
```

It listens on UDP port **7890**, on IPv4 and IPv6 by default. For an explicit
IPv4 address or different port:

```sh
build/dld-udp --bind 0.0.0.0 --port 7890
```

Each packet must contain an OPC header with command zero and a complete
declared payload of at least three bytes. The first pixel is logical RGB;
the local panel profile still determines wire order. Other pixels are ignored.
Queued updates are coalesced in bounded batches so a burst favors the latest
valid color; malformed packets are discarded. A repeated color is sent again.

Two status animations are enabled by default: one green flash at startup, and
a red flash after more than 60 seconds since the last received UDP datagram or
previous red flash. Each ramps from black to full color over 0.5 seconds, then
back to black over 0.5 seconds. Frames follow the sender's actual completion
speed; valid incoming color packets take over between frames. All received
datagrams reset the inactivity timer, including malformed or unsupported ones.
Suppress either behavior independently:

```sh
build/dld-udp --no-startup-flash --no-idle-flash
```

With `--no-idle-flash`, packet silence leaves the last displayed color alone.
An uninterrupted status flash ends black; it does not restore the prior color.
The shim exits on a sender error, without retrying or reinitializing. It sends
no acknowledgment and preserves no application-side frame queue.
Stop it with Ctrl+C or `SIGTERM` before unloading
the helper or returning control to LEDscape. See the [UDP operating guide](docs/udp.md)
for the packet format, flash timing, lifecycle, counters, and recovery behavior.

For a repeating local test gallery, see [Exercise panels over UDP](docs/exercise.md).
It sends fades, flashes, and color changes at up to 10 Hz while keeping the sum
of the three RGB channels at or below 255.

A 300-pixel bank uses a nominal 9.24 ms protected window, plus setup and
restoration overhead. Whole-command latency includes all active banks,
settling, admission waits, and Linux scheduling between banks. Ethernet
reception pauses during each window and incoming packets can be lost. The
controller must account for that. The shim is compatible with the legacy
unacknowledged packet format; sustained 20 Hz output and end-to-end latency
still need measurement on the target with the real controller.

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
critical error. After publication, the kernel owns completion or failure cleanup
even if the sender is killed. Critical cleanup attempts to stop the PRUs and
hold the outputs low; inaccessible hardware can prevent recovery. Forcing low
after an interrupted frame can latch partial data and does not black out the
panel. See [failure and restoration](docs/quiet-window.md#failure-and-restoration)
for detailed diagnostics and cancellation behavior.

## Return control to LEDscape

For the [RAM-only SSH trial](docs/trial.md), reboot to return to the existing
LEDscape boot setup. Copy any trial logs off the board first if needed.

For a manually prepared session without rebooting:

Stop `dld-udp`, the calling application, and all send loops; wait for active commands
to finish. To return to the previous runtime settings and service, use the
state file saved for this session:

```sh
set -e
rmmod dld_quiet
python3 tools/bench_prepare.py --restore "$PWD/build/preparation-state.json"
systemctl start ledscape.service
```

Restoration is explicit. Once LEDscape owns the hardware, perform the handover
and initialization again before using DLD.

## Reference and project history

The investigation and test records remain part of this repository:

- [Temporary SSH deployment](docs/trial.md), [UDP operating guide](docs/udp.md),
  [build guide](docs/build.md), [hardware reference](docs/hardware.md), and
  [full specification](spec.md): supported environment and command contract.
- [Protected operation](docs/quiet-window.md), [kernel helper](kernel/README.md),
  and [PRU firmware](pru/README.md): how the timing protection works.
- [Initial bring-up](docs/validation.md): the first implementation, bus fault,
  recovery, and software validation.
- [Waveform investigation and protected endurance results](docs/validation-20260906.md):
  the observed timing faults, changes, measurements, and remaining limits.
- [Evidence archive index](docs/evidence/README.md): portable measurements,
  provenance, and the inventory of larger local captures.
- [Test procedures](tests/README.md) and [capture analysis](tools/analyze_capture.md):
  how to reproduce software and waveform checks.
- [Decisions and remaining work](todo.md): review history, installed-panel
  qualification, network behavior, and production rollout.
