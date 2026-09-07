# Documentation

Start with the [project README](../README.md) for the utility overview and
quick start. The guides below describe operation and development; the historical
reports preserve what was tested and the limits of that evidence.

The source checkout contains the developer references and full evidence tree.
Prebuilt gateway archives include the operating guides and selected history
documents; use the [GitHub repository](https://github.com/bigjosh/deharrak-leds-driver)
for source, tests, and evidence files that are not packaged.

## Use and deploy

- [Panel configuration](configuration.md): pixel profiles, wire order, string
  lengths, and the six physical data pins.
- [Raspberry Pi gateway](gateway.md): download the release and deploy to BBGs
  on a remote network through outbound GitHub access and local SSH.
- [Temporary SSH deployment](trial.md): Windows/Linux launchers, replacement of
  a running trial, startup checks, logs, and reboot recovery.
- [Manual operation](operations.md): prerequisites, staging, initialization,
  sending colors, changing configuration, error handling, and handback.
- [LEDscape UDP input](udp.md): accepted packets, optional status flashes,
  queue handling, counters, and receiver lifecycle.
- [Visual scene exerciser](exercise.md): run and stop the local UDP gallery,
  control packet rate, and inspect progress.
- [Roadmap](../todo.md): completed milestones and remaining production work.

## Build and understand

- [Build guide](build.md): native reference builds, legacy toolchain,
  reproducibility, Windows assembly, and tests.
- [Hardware access](hardware.md): PRU loading, GPIO registers, memory access,
  bus faults, and hardware ownership assumptions.
- [Protected operation](quiet-window.md): kernel quiet windows, DMA ownership,
  preparation, and failure/restoration behavior.
- [Specification](../spec.md): the accepted interface, protocol, and timing
  contract.
- [Kernel helper](../kernel/README.md) and [PRU firmware](../pru/README.md):
  implementation details for the protected transmission path.
- [Validation procedures](../tests/README.md): software checks, hardware
  lifecycle tests, and waveform endurance procedures.
- [Capture analysis](../tools/analyze_capture.md): waveform checker setup and
  interpreting exported digital captures.

## Decisions and recorded evidence

These are dated records, not an inventory of currently running processes or
machine settings. Preserve their original configurations, artifact identities,
failures, and measurement limits when interpreting later results.

- [Design decisions and history](design-decisions.md): the original numbered
  review, superseded userspace approach, protected implementation decisions,
  and later utility/deployment milestones.
- [Initial bring-up](validation.md): original native/Windows build comparison,
  the recovered bus fault, and early software and live checks.
- [September 6 waveform bench](validation-20260906.md): legacy measurements,
  userspace timing violations, protected physical test matrix, and the final
  endurance run.
- [Initial UDP shim validation](validation-udp.md): native build, packet and
  shared-sender tests, and artifact identities.
- [Status-flash validation](validation-flashes.md): startup/inactivity
  animations and their receive/lifecycle behavior.
- [Trial packaging validation](validation-trial.md): native bundle checks and
  Windows/Linux launcher tests.
- [First live SSH handover](validation-trial-live.md): the connection issue,
  deployment, initialization, and receiver survival after SSH disconnect.
- [Repeat-deployment validation](validation-trial-redeploy.md): automatic SSH
  host-key acceptance and replacing an existing DLD trial.
- [Completed three-panel exercise](validation-exercise.md): startup checks,
  packet observations, and the 8-hour-20-minute run stopped on September 7.
- [Retained evidence](evidence/README.md): checked-in bench summaries, machine
  reports, excerpts, hashes, and the boundary between retained and external
  capture material.
