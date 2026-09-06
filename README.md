# Deharrak LED driver

DLD drives the Deharrak Clock's LED panels from a BeagleBone Green. It replaces
LEDscape's rendering role with two commands: initialize a panel once, then send
a color whenever the display needs to change. Each panel supports six strings
of up to 300 pixels; every enabled pixel receives the same RGB color.

```sh
# On a prepared BBG, from the build's source directory:
build/dld-init config/panel.json
build/dld-send FF0000  # Red
build/dld-send 00FF00  # Green
build/dld-send 000000  # Black
```

The current utility provides the local command interface and protected PRU
output. Automatic startup and the network receiver that will connect it to
the clock controller are the next integration work. Use the setup below for
manual operation on the supported board.

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

The outputs are `build/dld-init`, `build/dld-send`, and
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
next initialization; `dld-send` continues using the retained configuration
until then. A failed initialization can invalidate the previous session.

## Start the driver

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
sends. Use one build's command pair and lock path for the active session.
There is no background userspace process to keep alive between updates, and
no continuous refresh is needed to retain the last color.

A 300-pixel bank uses a nominal 9.24 ms protected window, plus setup and
restoration overhead. Whole-command latency includes all active banks,
settling, admission waits, and Linux scheduling between banks. Ethernet
reception pauses during each window and incoming packets can be lost. The
future controller integration must account for that; the CLI itself provides
no network protocol or delivery acknowledgment to a remote controller.

## Handle errors

Check the exit status and preserve stderr before deciding what to do next:

| Exit | Meaning and action |
|---:|---|
| 0 | Completed successfully. |
| 2 | Invalid color, arguments, or configuration; correct the input. |
| 3 | Missing prerequisite or unreadable configuration; resolve the reported cause. |
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

Stop the calling application and all send loops, and wait for active commands
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

- [Build guide](docs/build.md), [hardware reference](docs/hardware.md), and
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
