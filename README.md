# Deharrak LED driver

DLD replaces LEDscape on the Deharrak Clock's BeagleBone Green controllers.
It receives the existing OPC-over-UDP packets, takes the first RGB pixel, and
sends that color to every enabled pixel on the panel. Each panel supports six
strings of up to 300 pixels. A standalone command can send colors too.

The supported BBG target runs **Linux `3.8.13-bone80`**. Deployment currently
uses RAM and runtime settings; reboot returns the board to its existing
LEDscape boot setup. Persistent DLD boot installation is the next integration
step, tracked in [TODO](todo.md).

## Deploy to a panel

Download a [prebuilt release](https://github.com/bigjosh/deharrak-leds-driver/releases)
using the [gateway guide](docs/gateway.md), or create a matching bundle with
the [build guide](docs/build.md). The release includes the bundle and launchers;
the gateway machine needs no compiler. A Raspberry Pi can deploy to BBGs on
its own network through outbound GitHub access and local SSH.

Copy [the example configuration](config/panel.example.json) to a panel file
and set its actual pixel profile and six string lengths. For WS2812B with
all six strings enabled at 300 pixels:

```json
{
  "pixel_type": "ws2812b",
  "string_lengths": [300, 300, 300, 300, 300, 300]
}
```

A length of zero disables a string. See [panel configuration](docs/configuration.md)
for pin order, supported color orders, and applying changes.

From the extracted release or a checkout with `build/dld-trial.tar.gz`, pass
the target's address and your local panel file:

**Windows — PowerShell or Command Prompt**

```powershell
.\tools\deploy-trial.bat 192.168.1.50 .\config\panel.json
```

**Linux or Raspberry Pi**

```sh
sh ./dld-deploy 192.168.1.50 config/panel.json
```

The launcher accepts SSH host keys automatically and validates the uploaded
bundle and configuration before stopping existing DLD processes and LEDscape.
It prepares the board, initializes the panel, and starts `dld-udp` independently
of SSH. Rerun the same command to update a trial or change its configuration.
The [SSH deployment guide](docs/trial.md) covers prerequisites, options, logs,
and recovery.

## Use the driver

The deployed receiver listens on **UDP port 7890**. Each valid legacy OPC packet
provides the panel color through its first RGB pixel; subsequent pixels are
ignored. The panel retains its last color between updates.

Two status animations are enabled by default: a green flash at startup and a
red flash after more than a minute without received UDP traffic. Each ramps up
for 0.5 seconds and down for 0.5 seconds, ending black unless a valid color
packet takes over. Disable them independently with `--no-startup-flash` and
`--no-idle-flash`, or the corresponding [deployment options](docs/trial.md).
With the idle flash disabled, packet silence leaves the last color alone.
See the [UDP guide](docs/udp.md) for the packet format and receiver behavior.

For direct command use, follow [manual operation](docs/operations.md). In a
prepared BBG runtime directory, with other senders stopped:

```sh
build/dld-init config/panel.json
build/dld-send FF0000  # Red
build/dld-send 00FF00  # Green
build/dld-send 000000  # Black
# Or start the foreground UDP receiver:
build/dld-udp
```

Colors always use **RRGGBB**. Initialization sends no pixel data. Each send
waits for transmission and final reset/settling to complete. Coordinate callers:
overlapping commands fail busy, and sender errors require explicit handling.

For a repeating gallery of fades, flashes, and random colors, use the
[panel exerciser](docs/exercise.md). It sends at up to 10 Hz per panel and limits
each packet to `R + G + B <= 255`.

## How it works

`dld-init` configures all six pins as outputs held low, loads the embedded PRU
firmware, and retains the panel configuration in PRU memory. `dld-send` and
`dld-udp` share one C sender; the receiver keeps its resources open and does no
configuration-file I/O or process launch per packet.

The PRU writes one GPIO bank at a time. A required kernel helper pauses ARM
execution and Ethernet DMA around each bank's transmission and checks the
relevant storage/DMA engines for inactivity. Linux and networking resume
between banks. The [protected-operation guide](docs/quiet-window.md) explains
the timing protection and its hardware assumptions.

## Documentation

- [Panel configuration](docs/configuration.md) — profiles, pin order, lengths, and changes.
- [SSH deployment](docs/trial.md) and [Pi gateway](docs/gateway.md) — install or update a RAM-only trial.
- [Manual operation](docs/operations.md) and [UDP reference](docs/udp.md) — lifecycle, commands, errors, and logs.
- [Build from source](docs/build.md) — native BBG builds, Windows tooling, and packaging.
- [TODO](todo.md) — completed milestones and remaining work.

The [documentation index](docs/README.md) also links the specification, developer
references, and preserved investigation history, including the
[waveform measurements](docs/validation-20260906.md) and
[completed three-panel exercise](docs/validation-exercise.md).
