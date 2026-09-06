# Local three-panel exercise — 2026-09-06

The user authorized updating three BBGs and continuously exercising them from
the local Windows computer. The [exercise program](exercise.md) was started
with no duration limit; this record describes startup validation, not the
eventual duration or result of the ongoing run.

## Receiver preparation

Read-only inspection compared actual running executables, bundle files, and
bootstraps with the current `build/dld-trial.tar.gz` manifest. The `.62` panel
already matched. `.56` and `.73` still ran LEDscape and were updated using
`tools/deploy-trial.bat` and the existing RAM-only handover procedure.

| Target | Configuration used | New/current runtime | Receiver PID |
|---|---|---|---:|
| 192.168.68.56 | `ws2812b-bgr`, six strings of 100 | `/run/dld-trial.xUpNvV` | 1243 |
| 192.168.68.62 | `ws2812b`, six strings of 300 | `/run/dld-trial.SNIVaS` | 2214 |
| 192.168.68.73 | `ws2812b-bgr`, six strings of 100 | `/run/dld-trial.OW4bXO` | 27264 |

The legacy `/etc/ledscape-config.json` files on `.56` and `.73` specified BGR,
100 pixels per strip, and six used strips. Their exact pixel family was not
recorded. The BGR profile preserves the known wire order and uses the same
current timing as the other DLD profiles. `.62` retained its existing explicit
DLD configuration. No installed configuration or service files were changed.

Postdeployment checks found exactly one ready DLD receiver per host, each owning
UDP port 7890. Every packaged file and the running executable matched the local
bundle. Helper srcversion was `A68C9E83214A669AA2A7069`; LEDscape was inactive,
MainPID zero, and still enabled at boot. Runtime directories remain in RAM.

## Local program checks

All 12 scene tests and 18 runner tests passed under local Python 3.12. Scene
tests sample every 10 Hz frame across multiple playlist passes and panel
arrangements, plus off-grid times and random seeds. Runner tests cover the
final packet-budget guard, per-target pacing, scheduling delays, transport
errors and recovery, final black, stop requests, and argument validation.
Two subprocess tests used real UDP sockets on localhost only.

The playlist contains 19 scenes lasting 450 seconds in total. The program
enforces `R + G + B <= 255` both when selecting colors and when constructing
each seven-byte OPC packet. It waits a full packet interval after sending,
without accumulating missed-frame credit. No driver, firmware, or kernel
code was changed for this exerciser.

## Running instance and packet observations

The Windows process started at approximately `2026-09-06T18:34:12Z`:

- Local source address: `192.168.68.64`.
- Process ID at launch: 10468.
- Run directory: `build/panel-exercise-live`.
- Targets, in scene order: `.56`, `.62`, `.73`; UDP port 7890.
- Seed 1; maximum 10 packets per second per panel; no duration limit.

`run.json` records the source hashes and launch configuration. `status.json`
and rotating `events.log` report current progress. The separate redirected
stdout/stderr files are under `build/panel-exercise-preflight/`. A graceful
stop uses the program's `--stop` option or a `STOP` file in the run directory.

Short, read-only, non-promiscuous Python packet taps on each board captured
20 datagrams from the tester, then closed. All 60 observations had OPC channel
zero, command zero, payload length three, and `R + G + B = 255`. Each board's
sample contained 20 evolving rainbow colors. Average observed arrival rate
was approximately 9.2 packets per second; arrival intervals include network
and scheduling jitter. Receiver processes remained alive afterward, with no
new errors in their logs and zero socket queue/drop counts at inspection.

Inspection, deployment, and packet-capture evidence remains locally under
`build/panel-exercise-preflight/`. These checks establish packet arrival and
receiver liveness at startup. They do not measure physical LED appearance,
waveform timing, or every frame completed during the ongoing run.
