# Repeat SSH deployment — 2026-09-06

The deployment launchers were updated at the user's request to accept SSH host
keys automatically and replace an existing DLD trial without rebooting. The
previous [live handover record](validation-trial-live.md) remains historical;
the current procedure is in the [trial guide](trial.md).

## Software checks

- All 12 Windows PowerShell 5.1 launcher cases passed, covering both `.bat`
  and direct script invocation, default and explicit paths, host-key options,
  error propagation, and large native stderr output.
- All 71 remote-helper tests passed on Windows Python 3.12 and native BBG
  Python 3.2. These cover validation before interruption, graceful shutdown,
  process identity checks, exit races, respawns, bounded shutdown, ordinary
  module unloading, failure ordering, and detached receiver readiness.
- All 10 POSIX launcher cases passed on the native BBG, using fake SSH/SCP.
- The final native package build passed the common (487), shared sender
  (328), admission (17), OPC (1,617), flash (2,157), UDP (25), and CLI
  rejection (20) checks, followed by the 71 helper and 10 launcher cases.

The first native run encountered an intermittent failure in the existing
queued-UDP versus overdue-idle test. Five focused reruns passed. Source review
identified a race in the test's arbitrary `SIGSTOP`: it could pause between
the empty-socket check and the clock read, then advance the test clock after
the receiver had already checked for input. The failed run did not preserve
its color trace. The fixture now uses a test-only first-`poll` barrier to
queue the packet and advance time at a known loop boundary. The complete
25-case UDP suite passed after this change. Product code was unchanged.

The final package was built in `/run/dld-package.ld9LVW` on the compatible
build BBG at `192.168.68.73`, without runtime hardware changes there. Its SHA-256:

```text
bdf0e7f11988f1d97675bd6b0e75c7e30749f16aa4cd09f85261f2e288ff1b15
```

Every payload checksum was verified after downloading. The packaged bootstrap
matched the local helper exactly. All product executables, embedded firmware,
kernel module, and preparation tool matched the prior package byte-for-byte;
only the handover helper and build report changed. The local ready-to-deploy
bundle at `build/dld-trial.tar.gz` was refreshed.

## Host-key acceptance

The live deployment used the new default connection options, which bypass the
default user/global known-hosts files without modifying them. A separate
read-only SSH command also succeeded with the same acceptance options and
an intentionally stale key in a temporary custom known-hosts file. OpenSSH
printed its mismatch warning but returned success. These launchers skip SSH
host identity verification; root authentication is still required.

## Live replacement

The following command completed against the already-running DLD trial:

```powershell
.\tools\deploy-trial.bat 192.168.68.62 .\config\panel.json
```

The new bundle and `ws2812b` configuration, with six strings of 300 pixels,
passed validation before interruption. The helper sent `SIGTERM` to old
receiver PID 1808 and waited for it to exit. Its final log reported signal 15,
exit 0. The handover verified LEDscape inactive, unloaded `dld_quiet`, applied
runtime preparation, loaded the new helper, initialized successfully, and
waited for the new receiver's readiness marker.

A separate SSH connection after deployment verified:

- New runtime `/run/dld-trial.SNIVaS`, receiver PID 2214, parent PID 1.
- The new receiver owned UDP port 7890 and had both default status flashes on.
- Old PID 1808 had exited; `/run/dld-trial.YZ1k7L` and its logs remained.
- The panel configuration still contained six lengths of 300 and `ws2812b`.
- CPU frequency was 1,000,000 kHz; helper srcversion was
  `A68C9E83214A669AA2A7069`.
- LEDscape was inactive, MainPID 0, and still enabled at boot.
- Boot ID remained `ac523718-0526-4f40-8e7f-930cef851669`: no reboot occurred.

DLD was left running. No installed files, service definitions, or boot
enablement were changed. Local Git-excluded build, connection, deployment,
and verification evidence is under `build/redeploy-20260906/`; remote logs
remain in the two trial directories until reboot.

This verifies live replacement and survival of SSH disconnect. It adds no
physical waveform, pixel appearance, sustained UDP delivery, or reboot
recovery measurement.
