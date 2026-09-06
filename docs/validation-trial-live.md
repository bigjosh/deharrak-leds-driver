# Live SSH handover — 2026-09-06

The user authorized running and debugging the Windows batch deployment against
`192.168.68.62`, using `config/panel.json`: profile `ws2812b`, with 300 pixels on
each of six enabled strings. The existing LEDscape service was active before
the handover; the DLD helper was not loaded.

## Connection failure and correction

The initial exit 255 occurred before any remote command because the Windows
SSH client had no saved host key for this IP. This was a different BBG from
the earlier `beaglebone` target at `192.168.68.71`. Its key was recorded using
SSH first-use trust for this specific address; subsequent commands and the
deployment retained strict host-key checking. No existing key was replaced.

The Windows launcher also hid the native SSH diagnostic. Its process wrapper
now redirects stderr explicitly, drains it concurrently with stdout, and
forwards the diagnostic while preserving the native exit status. All 12
offline Windows launcher cases passed, including both batch/direct invocation
and a 128 KiB diagnostic that checks for pipe deadlock or truncation.

## Live result

The exact two-argument batch invocation then completed successfully:

```powershell
.\tools\deploy-trial.bat 192.168.68.62 .\config\panel.json
```

The remote helper verified the bundle and panel configuration, applied the
runtime CPU/user-LED preparation, loaded `uio_pruss` and the matching helper,
stopped LEDscape, verified `ActiveState=inactive` and `MainPID=0`, and ran
`dld-init` successfully. The detached `dld-udp` emitted its explicit readiness
message with both status flashes enabled.

A separate SSH connection after the deployment connection closed verified:

- Runtime directory: `/run/dld-trial.YZ1k7L`.
- Receiver PID 1808, parent PID 1, alive with its executable in that directory.
- The receiver owned the IPv6/IPv4 UDP socket on port 7890.
- The staged configuration contained six lengths of 300 and profile `ws2812b`.
- CPU frequency was 1,000,000 kHz; helper `srcversion` was
  `A68C9E83214A669AA2A7069`.
- LEDscape remained inactive and enabled at boot.

The native bundle was unchanged from the
[packaging validation](validation-trial.md). DLD was left running. This handover
changed runtime state and created files under `/run`; it did not install a DLD
boot service or modify the board's existing installed/startup files. The new
SSH trust entry is on the Windows client.

Local Git-excluded evidence is retained under `build/deploy-192.168.68.62/`:
`attempt-1.log`, `handover.log`, `udp-startup.log`, and `verification.log`.
The on-board logs remain under the runtime directory until reboot.

This confirms real deployment, initialization, receiver startup, and survival
of SSH disconnect. It does not measure physical pixel appearance, waveforms,
sustained controller delivery, or packet loss. No post-handover reboot recovery
test was performed; the existing LEDscape boot enablement was preserved.
