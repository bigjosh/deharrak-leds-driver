# Temporary SSH trial validation — 2026-09-06

This is the initial build-only validation record for the Windows/Linux
launchers, shared remote handover helper, and package builder. At this stage
they were validated without replacing the board's running hardware session. Native
build work used new directories on `/run` tmpfs. No LEDscape service change,
module load/unload, PRU initialization, or physical LED send was performed.

The subsequent [live handover](validation-trial-live.md),
[repeat deployment](validation-trial-redeploy.md), and completed
[three-panel exercise](validation-exercise.md) record later operational checks.
Package hashes and test counts below identify this earlier build; they do not
describe the latest release.

## Checks completed

| Check | Result |
|---|---|
| Native package build, Linux `3.8.13-bone80`, GCC 4.6.3 | Passed |
| Common / shared sender / admission / OPC / flash timing | 487 / 328 / 17 / 1,617 / 2,157 checks passed |
| Receiver loopback tests, including explicit readiness | 25 passed |
| Actual CLI argument/configuration rejection | 20 checks passed |
| Remote package, preflight, handover and startup tests | 48 passed on native Python 3.2 and Windows Python 3.12 |
| POSIX launcher using fake SSH/SCP | 9 passed on native Python 3.2 |
| Windows argument handling using compiled fake SSH/SCP | Passed on PowerShell 5.1 and 7.6, including Standard and Legacy argument modes |
| Real SSH detachment with a harmless fake receiver | Passed; process survived SSH exit, then only that verified test process was terminated |
| Real bundle extraction, configuration checker, read-only preparation check | Passed on the BBG |
| Existing loaded-helper preflight refusal | Passed before runtime changes |

The handover tests substitute OS and command boundaries. They exercise package
hashes, fixed archive members, traversal/link rejection, existing-file
preservation, RAM/root/swap/kernel checks, deployment serialization, service
stop verification, init failures, and explicit readiness from a live detached
child. Failure cases do not invoke automatic rollback. The launcher tests
exercise address/path handling, strict SSH options, transfer ordering and
failure propagation. The Windows checks include IPv6, spaces in file paths,
and preservation of the quoted known-hosts option.

The detachment probe used the production `start_receiver` function with a
temporary executable that printed readiness and slept. It did not open a UDP
port or access hardware. A subsequent SSH connection verified the exact test
executable in the retained process's command line before terminating it.

## Built package

The generated local archive is `build/dld-trial.tar.gz`, **69,322 bytes**, SHA-256
`e9147f620e9ce38076a34ce614c24cf3d62750666b2fb7f577eb4eb9d6b5c3b4`.
It targets the reference kernel and compiles the commands with
`LOCK_PATH=/run/dld.lock`. It contains no panel-specific configuration; the
launcher takes a separate reviewed local JSON file.

| Artifact | File bytes | SHA-256 |
|---|---:|---|
| `dld-init` | 32,985 | `a43c2420b0a5d7faad050a08bba48e292a9bbf19c9455d1cf74e1f621f2713c6` |
| `dld-send` | 29,928 | `bde54ec07ff941b615b3d48107534d1c021055dd6b7678dc4ef40794fa8203f4` |
| `dld-udp` | 35,662 | `a2b778ab94820c4a71bac1990e3013b0abe60188d1d5a68c3e4bf7429b6af4b0` |
| Internal config checker | 20,543 | `18a964a282ef9d51dd3b596709d6d2f1b89dfbc73aff302a423626af5153216f` |
| `dld_quiet.ko` | 15,451 | `1e9aa2b967c5930d820989a0c76acc1f7a88eafce6ff184f156ea49687753414` |

The firmware and kernel module retain the protected endurance build's hashes.
The firmware remains 4,412 bytes with SHA-256
`da45899a51fdedbf80ec2a7d60c785f5be82970caf115f807b54719661084a60`.
No timing-path code changed; the preceding physical and instruction-audit
results remain in the [earlier validation records](validation-udp.md).

Local Git-excluded evidence is under `build/trial-implementation/`: source
archive, extracted package, native log, and detachment/bundle-check logs.
The exact `native-package-validation.log` has SHA-256
`5242efca5c506ed59f06d713711f378f3e9524dafc44bab46424cb2c9f6d851a`.
The packaged build report has SHA-256
`31933997a2ae9ee2a42b01aa4c6763053a4e850336139fdc3fece4faba31bd5e`.
Windows argv fixtures/logs are retained under `build/trial-launcher-check/`.
The native source and retained package build were
`/run/dld-build-source.70srEH` and `/run/dld-package.oQB6LA`; both disappear
on reboot. The package has already been copied to the Windows workspace.

## Later live checks and remaining limits

At this build-only validation stage, a full live launch had not been performed.
The later records linked above document handover from LEDscape, replacement of
an existing DLD trial, and an 8-hour, 20-minute local UDP exercise. Sustained
delivery with the intended controller, packet-loss measurement, and downstream
waveform qualification remain separate work. Startup readiness is not an
ongoing health monitor or physical waveform qualification.
See the [trial procedure](trial.md) for both launcher commands and reboot
recovery, and [UDP operation](udp.md) for receiver behavior.
