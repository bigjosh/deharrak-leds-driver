# UDP status-flash validation — 2026-09-06

The green startup and red inactivity flashes were built and tested natively
on the reference BeagleBone Green, using GCC 4.6.3 and Linux
`3.8.13-bone80`. Work used the new isolated directory
`/root/dld-flash-build-1788713380339`. No module or firmware was loaded,
no physical LED send was issued, and no service or boot configuration changed.

## Software results

`make -j2 LOCK_PATH="$PWD/dld.lock" test-native` completed successfully.

| Check | Result |
|---|---|
| Common logic | 487 checks passed |
| Shared sender and CLI lifecycle | 328 checks passed |
| DMA admission policy | 17 checks passed |
| OPC parser | 1,617 checks passed |
| Flash frame selection and inactivity boundaries | 2,157 checks passed |
| Real loopback UDP with a simulated sender | 25 tests passed |
| Actual CLI argument/configuration rejection | 20 checks passed |

The pure flash tests cover RGB scaling and rounding, the 0.5- and one-second
boundaries, variable send durations, a send that crosses both boundaries,
mandatory black/full-color/black endpoints, and the strictly greater than
60-second idle predicate. They exercise large timestamps and backwards-time
guards without depending on OS scheduling.

The socket tests cover default green/red behavior, independent opt-out flags,
completed-send pacing, elapsed-time jumps, packet takeover of either flash,
invalid packets during animation, inactivity reset by every received datagram,
the next red interval starting after final black completes, queued-input
priority over an overdue red flash, fatal send/clock failures, and signals.
The original packet, batching, 20 Hz synthetic-send, and lifecycle tests run
with both flashes disabled. All 25 tests completed in 22.857 seconds.

Only the simulated-sender test executable wraps `clock_gettime`; its optional
offset file advances idle time without minute-long waits. The production
binary imports the normal `clock_gettime@GLIBC_2.4` and has no clock override.
The green/red ramp tests use real elapsed time and 80 ms simulated blocking
sends. These checks do not measure actual panel appearance or hardware cadence.

## Artifacts

`make report` completed successfully. The utilities add no package dependency.

| Artifact | File bytes | SHA-256 |
|---|---:|---|
| `dld-init` | 33,017 | `6e824e65cd1adf4dea2e6a431304b3174de409212b743166734691f189935d44` |
| `dld-send` | 29,960 | `6582799bbbef730d53692c0f6b4994b5626bb6a8c2e1625e36e01d144fe05eaa` |
| `dld-udp` | 35,510 | `f5ab2352bde9dc32f95369dba44424032f8b47f91faed79a276aef43c7973d43` |
| PRU firmware | 4,412 | `da45899a51fdedbf80ec2a7d60c785f5be82970caf115f807b54719661084a60` |
| `dld_quiet.ko` | 15,451 | `1e9aa2b967c5930d820989a0c76acc1f7a88eafce6ff184f156ea49687753414` |

The PRU firmware and kernel module are byte-identical to the protected
endurance build. Their source and the shared sender are unchanged. The PRU
instruction-model and kernel machine-code audits were not repeated for this
userspace-only change; their preceding results remain in the
[initial UDP validation record](validation-udp.md). Different compile-time lock
paths change host executable sizes and hashes, including unchanged commands.

Local Git-excluded artifacts are retained under `build/flash-implementation/`:
the source archive, native artifact archive, and extracted `native/` directory.
The exact native log is `native/validation.log`, SHA-256
`89d8c34cc4bf16053add3c74d949aae562a77baf7e87f955a98995fd691c05b2`.
The build report is `native/build/build-report.txt`, SHA-256
`99047e8e6256d76f6a902c39e11a188acd2e600d7ff14612f3c39c9282eb0e98`.
The report includes compiled source hashes and runtime dependencies.

## Physical qualification

The new flashes have not been displayed or captured on the installed panel.
Their appearance, physical cadence, controller interaction, and UDP loss during
protected sends remain part of the planned installed-panel qualification.
The [test guide](../tests/README.md) describes that work, and the
[UDP guide](udp.md) documents operation and timing. The earlier
[waveform investigation](validation-20260906.md) remains the physical evidence
for the unchanged protected transmission path.
