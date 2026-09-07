# Local three-panel exercise — 2026-09-06–07

The user authorized updating three BBGs and continuously exercising them from
the local Windows computer. The [exercise program](exercise.md) completed an
**8-hour, 20-minute run**, with **274,268 successful local UDP sends per panel**
and **zero local send errors**. It stopped gracefully at the user's request,
submitting a final black frame to every target. This record includes the initial
receiver checks and the completed run's saved results.

## Receiver preparation

Read-only inspection compared actual running executables, bundle files, and
bootstraps with the `build/dld-trial.tar.gz` manifest used for this run. The `.62` panel
already matched. `.56` and `.73` still ran LEDscape and were updated using
`tools/deploy-trial.bat` and the existing RAM-only handover procedure.

| Target | Configuration used | Runtime at startup | Receiver PID at startup |
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
MainPID zero, and still enabled at boot. Runtime directories were created in RAM.

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

## Completed run

The Windows process ran from `2026-09-06T18:34:13Z` to
`2026-09-07T02:54:15Z`:

- Local source address: `192.168.68.64`.
- Process ID at launch: 10468.
- Run directory: `build/panel-exercise-live`.
- Targets, in scene order: `.56`, `.62`, `.73`; UDP port 7890.
- Seed 1; maximum 10 packets per second per panel; started with no duration limit.

The final `status.json` and matching `finish` event in `events.log` record:

| Result | Value |
|---|---|
| Elapsed time | 30,002.531 seconds (8 hours, 20 minutes, 2.531 seconds) |
| Playlist coverage | 66 full 450-second passes, plus part of pass 67 |
| Successful local sends | 274,268 per panel; 822,804 total, including final black |
| Local send errors | Zero on all three targets |
| Last submitted color | `(0, 0, 0)` on all three targets |
| Stop reason | `stop_file`, requested by the user |

The redirected stdout confirms `Panel exercise stopped: stop_file`; stderr is
empty. This run is finished. The process ID and runtime paths above identify
the recorded run and are not current process-status claims.

`build/panel-exercise-live/run.json` preserves the source hashes and launch
configuration; `status.json` preserves the final counters, and the rotating
`events.log` files retain scene and progress records. The separate redirected
stdout/stderr files are under `build/panel-exercise-preflight/`.

## Initial packet observations and evidence limits

Short, read-only, non-promiscuous Python packet taps on each board captured
20 datagrams from the tester, then closed. All 60 observations had OPC channel
zero, command zero, payload length three, and `R + G + B = 255`. Each board's
sample contained 20 evolving rainbow colors. Average observed arrival rate
was approximately 9.2 packets per second; arrival intervals include network
and scheduling jitter. Receiver processes remained alive afterward, with no
new errors in their logs and zero socket queue/drop counts at inspection.

Inspection, deployment, and packet-capture evidence remains locally under
`build/panel-exercise-preflight/`. These checks establish packet arrival and
receiver liveness at startup. The final local counters establish successful
UDP submissions throughout the run; UDP provides no delivery acknowledgment.
They do not establish that every datagram arrived, that each frame completed,
or that waveform timing remained within bounds. No continuous receiver or
logic-analyzer capture accompanied this exercise; physical timing evidence is
recorded separately in the [physical bench report](validation-20260906.md).
