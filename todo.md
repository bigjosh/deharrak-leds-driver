# Roadmap

Status as of September 7, 2026. The core driver, LEDscape UDP replacement,
repeatable trial deployment, and Raspberry Pi gateway release are implemented.
The user reports successful operation. The remaining work is production
integration and qualification on the installed system.

The [README](README.md) explains how to use the utility. The detailed numbered
review, agreed tradeoffs, superseded approaches, and original bench snapshots
are preserved in [design decisions and history](docs/design-decisions.md).
The [specification](spec.md) defines the accepted behavior.

## Remaining work

- [ ] **Persistent production startup and recovery.** Replace LEDscape boot
  ownership with the matching helper, runtime preparation, successful
  `dld-init`, and then `dld-udp`. Decide the service response to fatal sender
  errors. Current deployments use `/run` and preserve the existing LEDscape
  boot setup; this work provides the separately agreed permanent replacement.
- [ ] **Installed-panel and electrical qualification.** Confirm each installed
  panel's pixel profile, wire color order, and lengths. Check waveforms after
  level shifting/wiring and downstream latch behavior, including reset,
  propagation, and safety margins. The successful header captures and visual
  deployment feedback support the utility, while these installation-specific
  measurements remain open.
- [ ] **Real-controller delivery at 20 Hz.** Measure controller cadence,
  receiver/send rates, packet loss, and visible behavior with the actual input
  stream, including the effect of Ethernet pauses during protected sends. The
  completed local exerciser was capped at 10 packets per second. A sequenced or
  acknowledged protocol remains a possible future controller change, not a
  requirement of the existing UDP shim.

These continue the previously recorded follow-ups; they do not reopen the
accepted uniform-color interface, timing targets, bank skew, or temporary
reboot-recovery policy.

## Completed milestones

- [x] **Core commands and shared sender.** `dld-init` configures all six outputs,
  lengths, and pixel profile; `dld-send` transmits a uniform color and waits for
  final settling. Firmware and userspace use the protected ABI4 path.
- [x] **Kernel protection and waveform bench testing.** The final protected
  run completed 155,917 sends with no failed sends or observed timing violations
  in 4,452,206,525 complete captured pulses. This is header-level evidence with
  acquisition gaps. [Bench report](docs/validation-20260906.md).
- [x] **Legacy UDP replacement and status flashes.** `dld-udp` uses the shared
  sender, takes the first OPC pixel, retains resources between packets, and
  provides independently optional green startup and red inactivity flashes.
- [x] **SSH trial deployment and replacement of a running trial.** Windows and
  Linux launchers stage validated files in RAM, stop the current receiver,
  initialize, and start DLD. Reboot restores the existing LEDscape boot setup.
  [Trial guide](docs/trial.md).
- [x] **Versioned Raspberry Pi gateway release.** Published
  [v0.1.0](https://github.com/bigjosh/deharrak-leds-driver/releases/tag/v0.1.0)
  with tested ARMv7 binaries for `3.8.13-bone80`, scripts, documentation, and
  verified download checksums. The Pi needs outbound GitHub access and SSH to
  its BBGs. The repository and release downloads are public; no GitHub login
  is needed. [Gateway guide](docs/gateway.md).
- [x] **Three-panel visual exercise.** The 19-scene run completed 8 hours
  20 minutes, with 274,268 local UDP sends per panel and zero local send errors.
  It stopped on request after sending black. These counters do not measure
  end-to-end delivery or waveform correctness.
  [Completed run record](docs/validation-exercise.md).
