# Temporary replacement over SSH

The trial launchers copy a native DLD bundle and your panel configuration into
a new `/run/dld-trial.XXXXXX` directory on the BBG, stop an existing DLD session
and `ledscape.service`, initialize DLD, and leave `dld-udp` running after SSH
disconnects. Run the same command again to deploy a new bundle or configuration;
no reboot is required between trials. The launchers do not install software,
edit service files, or disable LEDscape at boot. Reboot clears the trial and
lets the board's existing boot configuration start LEDscape again.

This is an operational handover: running either launcher changes the displayed
panel. The green startup flash and red inactivity flash are enabled by default.
Successful startup confirms the process is ready; it does not qualify installed
color order, electrical timing, or controller-to-panel packet delivery.

## Before running

- Use the supported BBG kernel `3.8.13-bone80` and matching native bundle.
  The existing board must provide Python 3, the required runtime libraries,
  `uio_pruss`, systemd, and the prerequisites in the
  [operating guide](../README.md#requirements-and-build).
- Establish root SSH access without an interactive password prompt, through
  the board's existing authentication setup. The launchers accept the target's
  SSH host key automatically, including when a different board reuses an IP.
- Supply a local panel JSON file with the installed pixel profile and six
  string lengths, following [panel configuration](../README.md#configure-a-panel).
  The launchers do not infer this from LEDscape's configuration.
- Exclude independently launched renderers and other PRU/GPIO users. The
  handover controls the named `ledscape.service`; it cannot establish exclusive
  ownership against arbitrary hardware users.
- LEDscape must already be enabled at boot; the preflight checks this without
  changing its enablement. An existing DLD session and loaded `dld_quiet`
  helper are stopped and replaced after the new bundle passes validation.
- The scripts require RAM-backed `/run` and no swap so the trial's files
  remain in memory.

## SSH host keys

No preliminary SSH connection or host-key confirmation is needed. For their
own SSH and SCP calls, both launchers disable strict host-key checking and
ignore the computer's default user and global known-hosts files. This skips
SSH host identity verification; it also leaves those default files unchanged.
These options do not change the computer's global SSH configuration.

The optional `-KnownHosts` / `--known-hosts` option selects a separate user
known-hosts file for recording accepted keys. Strict checking remains disabled
when this option is supplied. Root authentication must still succeed without
interaction; accepting a host key does not supply a password or login key.

## Build the bundle once

Build on a compatible BBG using a dedicated checkout containing these scripts.
See [native build setup](build.md) for compiler and matching-kernel-header
requirements. From that source directory:

```sh
sh tools/package-trial.sh
```

The package helper creates a fresh build workspace under `/run`, builds the
commands with the common lock path `/run/dld.lock`, and writes
`build/dld-trial.tar.gz`. To choose another output path, supply it as the
single argument. Existing output files are never overwritten; choose a new
name when rebuilding. The temporary source/build directory remains under
`/run` until reboot. Packaging runs the native software suite and mocked
handover/launcher tests before producing the archive.

The archive contains all three commands, the matching kernel
module, an internal configuration checker, handover/preparation tools, a file
manifest, and a build report. PRU firmware is embedded in `dld-init`; it needs
no separate firmware installation. Packaging does not stop LEDscape, load a
module, initialize pins, or send colors.

Download that archive to `build/dld-trial.tar.gz` in this local checkout, using
the actual native build directory:

```sh
scp -O root@beaglebone:/root/dld-BUILD/build/dld-trial.tar.gz build/dld-trial.tar.gz
```

The same `scp` command works in PowerShell and a Linux shell. The bundle can be
reused on compatible targets. Keep the local deployment scripts from the same
version as the bundle: the remote bootstrap must exactly match its packaged
copy. A mismatch fails before changing runtime state. A regular build compiled with a project-local
lock is not a substitute: the trial package fixes all three commands to the
same RAM-backed lock path.

## Run from Windows

Use the batch launcher from PowerShell or Command Prompt. It requires Windows
PowerShell 5.1 and OpenSSH `ssh`/`scp` on PATH; `scp` must support the legacy
transfer option `-O` required by the reference BBG.

```powershell
.\tools\deploy-trial.bat 192.168.1.50 .\config\panel.json
```

The batch launcher runs the adjacent PowerShell script with an execution-policy
override for that child process only. It does not change the computer's saved
execution policy, and no `Set-ExecutionPolicy` command is needed. You can still
run `deploy-trial.ps1` directly from Windows PowerShell 5.1 or PowerShell 7 if
your execution policy allows it.

To select a different bundle, a separate known-hosts file, or disable either
flash, pass the same named options through the batch launcher. This
single-line example works in PowerShell and Command Prompt:

```powershell
.\tools\deploy-trial.bat beaglebone .\config\panel.json -Bundle .\build\dld-trial.tar.gz -KnownHosts .\build\known_hosts -NoStartupFlash -NoIdleFlash
```

## Run from Linux

Use a POSIX shell, standard `awk`, and OpenSSH `ssh`/`scp` with `-O` support.
The local Linux launcher needs no Python installation:

```sh
sh tools/deploy-trial.sh 192.168.1.50 config/panel.json
```

The equivalent explicit options are:

```sh
sh tools/deploy-trial.sh beaglebone config/panel.json \
  --bundle build/dld-trial.tar.gz --known-hosts build/known_hosts \
  --no-startup-flash --no-idle-flash
```

Both launchers accept a hostname, IPv4 address, or unbracketed numeric IPv6
address and run as `root` on the target. Their default bundle is
`build/dld-trial.tar.gz` relative to this checkout; the panel path identifies
a file on the local computer. Flash options are independent. A valid incoming
color can interrupt either enabled flash, so continuous controller traffic
may prevent a complete green startup animation.

## Replacing an existing DLD session

Update the local bundle and/or panel file, then rerun the same launcher command.
Each attempt uploads into a fresh directory and validates the bundle, panel
configuration, and prerequisites before interrupting the current session.

The handover finds running `dld-init`, `dld-send`, and `dld-udp` executables,
requests their termination with `SIGTERM`, and waits up to 20 seconds for them
to exit. This lets an in-flight transmission finish its cleanup. It then
stops LEDscape, unloads an existing `dld_quiet` helper normally, applies the
runtime preparation, loads the new helper, initializes the panel, and starts
the new receiver. It fails if the old commands do not exit or the
helper cannot unload; it does not force-kill them or force module removal.

Stop external supervisors or scripts that would restart old DLD commands.
The handover detects DLD processes directly; it does not change an external
supervisor's restart policy. Old trial directories and logs remain available
until reboot, and each successful deployment reports its new directory and PID.

## What stays temporary

The bundle, copied panel configuration, preparation record, PID, and logs live
under the new `/run` directory. The common DLD lock is `/run/dld.lock`; a
separate `/run/dld-trial.lock` prevents overlapping deployment attempts.
CPU-frequency policy, user-LED triggers, loaded modules, PRU firmware, and the
running receiver are runtime state. The handover stops LEDscape without
disabling it, and adds no boot service or automatic restart policy for DLD.

This keeps DLD's deployment files off persistent storage. Existing SSH,
systemd, and kernel logging still follow the board's normal logging policy;
the scripts do not reconfigure system logging.

The receiver listens on the existing OPC/UDP port **7890**. It remains running
when the launcher exits or the SSH connection closes. A sender error terminates
it rather than retrying or reinitializing. See the [UDP guide](udp.md) for
packet handling, status counters, and failure behavior.

## Startup result and logs

The launcher prints the new trial directory. The remote helper validates the
new files and prerequisites, replaces any existing DLD session as described
above, and verifies LEDscape has exited before initializing the panel.
Successful initialization is followed by receiver startup.
It waits up to 20 seconds for the exact `dld-udp: ready` message. With the default
green flash, readiness follows the completed flash or a successful UDP color
that interrupts it. With `--no-startup-flash`, attachment establishes readiness
without a test frame.

Within the printed directory:

| File | Contents |
|---|---|
| `handover.log` | Setup commands and diagnostics |
| `udp.log` | Receiver startup, errors, and final counters |
| `udp.pid` | Detached receiver's process ID |
| `preparation.json` | Recorded CPU and user-LED runtime settings |

Read or copy these files over SSH using the exact directory printed by the
launcher. The readiness check covers startup only; it does not supervise the
receiver after the launcher returns.

## If the swap fails

Exit 255 generally means SSH could not establish the connection. Read the SSH
diagnostic printed immediately before the launcher's error, such as an
authentication failure or an unreachable target. The Windows launcher forwards
these diagnostics explicitly. Unknown or changed host keys are accepted by the
deployment launchers automatically.

The launchers stop on a failed prerequisite, transfer, shutdown, initialization,
or startup check and report the trial directory. Failed uploads or validation
leave an existing DLD session running. Once shutdown begins, a later failure
can leave the panel without a receiver. No automatic rollback restarts
LEDscape or restores settings. A startup failure requests termination of the
receiver launched by that attempt. Preserve any needed RAM logs before rebooting;
they disappear when `/run` is cleared.

If recovery is needed, reboot the board through its usual management procedure.
Its existing startup configuration then takes over. The launcher can be rerun
after recovery; routine replacement of a healthy DLD session needs no reboot.

The [validation record](validation-trial.md) describes the completed native,
mock-handover, launcher, and SSH-detachment checks and their limits.
The [first live handover record](validation-trial-live.md) covers the subsequent
authorized deployment to a running BBG.
The [repeat-deployment record](validation-trial-redeploy.md) covers automatic
host-key acceptance and replacement of an already-running DLD session.
