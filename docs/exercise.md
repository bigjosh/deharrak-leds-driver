# Exercise panels over UDP

`tools/exercise_panels.py` runs a repeating gallery of color scenes from a local
Windows or Linux machine. It needs Python 3.8 or newer and uses only the standard
library. Panels must already be initialized with `dld-udp` listening on port
7890; use the [trial deployment scripts](trial.md) to install or update the
running receiver before starting.

From the repository root:

```sh
python tools/exercise_panels.py --targets 192.168.68.56 192.168.68.62 192.168.68.73
```

The gallery repeats until stopped. Each panel receives at most ten packets per
second, with no catch-up bursts after a scheduling delay. Every packet contains
one OPC RGB pixel. All colors satisfy **R + G + B <= 255**; balanced white is
`85, 85, 85`. This limit applies to each packet, including transitions and the
final black frame.

Use `--fps` to lower the packet rate (maximum 10), `--seed` to select a
repeatable random sequence (default 1), or `--duration SECONDS` for a finite
run. `--targets` accepts numeric IPv4 addresses; `--port` changes the destination
port from 7890. Scene timing follows elapsed time, so lowering the packet rate
reduces animation smoothness without slowing the gallery.

```sh
python tools/exercise_panels.py --targets 192.168.68.62 --fps 5 --seed 7 --duration 120
```

## Scenes

The 19-scene playlist lasts 7.5 minutes. Random scenes change on successive
passes but remain reproducible for the same seed, pass, and elapsed time.

| Scenes | Coverage |
|---|---|
| Rainbow fades | Full color cycles lasting 30, 8, and 2 seconds. |
| Primary flashes | Alternating primary colors and black at 0.5, 1, and 2.5 complete flashes per second. |
| Random jumps | New colors at 1, 2, 5, and 10 changes per second. |
| White and pastel breathing | Smooth brightness cycles lasting 6 and 8 seconds. |
| Panel chase | One lit panel advances every half second. |
| Opposed rainbows and red-blue counterfade | Different panel phases and directions, on 8-second cycles. |
| Walking bits | Each of the 24 RGB bits alone, at five steps per second. |
| Byte boundaries | Primary-channel values around powers of two, including 0, 127, 128, 254, and 255. |
| Alternating bit patterns | Mixed `0x55`/`0xAA` bytes, primary endpoints, and other transitions. |
| Black soak | Twelve seconds of repeated all-zero packets. |

Most scenes keep the panels synchronized. Chase, pastel, opposed-rainbow, and
counterfade scenes use the panels' order in `--targets` to assign phases.
The walking-bit and byte-pattern scenes exercise data transitions alongside
the visual gallery; waveform timing still requires measurement at a data pin.

## Progress and stopping

The program prints its run directory at startup. By default it creates a new
directory under `build`; `--run-dir PATH` selects a new directory explicitly.
Retain that path when running the program in the background.

- `run.json` records the run configuration and process ID.
- `status.json` provides progress, refreshed after sends approximately every
  five seconds and on scene changes or exit.
- `events.log` records scene changes and errors, rotating at approximately
  1 MiB with three backup files.

Stop a foreground run with Ctrl+C, or ask a background run to stop from another
terminal:

```sh
python tools/exercise_panels.py --stop build/YOUR_RUN_DIRECTORY
```

Creating an empty file named `STOP` inside the run directory has the same
effect. A graceful stop sends black to each target, respecting the packet-rate
limit, and exits. Forced process termination does not perform that final send.

UDP has no acknowledgment. Successful local sends and the progress counters
show what the tester submitted, not what the panels received or displayed.
Network send failures are logged per target while the remaining panels
continue. The tester does not update, restart, or inspect remote receivers;
receiver logs and waveform captures remain separate checks.

The [three-panel startup record](validation-exercise.md) documents the initial
deployment checks, local software tests, and observed packet arrival.
