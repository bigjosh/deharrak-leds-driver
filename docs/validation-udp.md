# UDP shim implementation validation — 2026-09-06

The resident `dld-udp` receiver and shared sender were built and tested on the
reference BeagleBone Green with GCC 4.6.3, Make 3.81, binutils 2.22, glibc 2.13,
Python 3.2, and matching Linux `3.8.13-bone80` headers. All work used a new,
isolated build directory. No firmware or module was loaded, no valid hardware
send was issued, and no service or boot configuration was changed.

## Functional results

| Check | Result |
|---|---|
| Existing common logic | 487 checks passed |
| Shared sender and CLI lifecycle | 328 checks passed |
| DMA admission policy | 17 checks passed |
| OPC packet parser | 1,617 checks passed |
| Actual CLI argument/configuration rejection | 20 checks passed |
| Receiver with real loopback sockets and a simulated sender | 14 tests passed |
| Native PRU instruction-model audit | 139 frames passed |
| Native kernel machine-code audit | Passed; 11 dangerous mutations rejected |
| Existing host Python sender, storage, and capture-checker suites | 50 tests passed |

The complete native `make all test audit report` invocation exited successfully.

The shared-sender tests exercise the actual CLI and sender code with mocked OS
boundaries. One attachment serves 100 consecutive sends without reopening the
device, lock file, or PRU mapping. Further cases change the retained panel
configuration, advance/wrap the request sequence, reject overlapping commands,
and exercise cancellation, incomplete helper results, uncertain result copyout,
failure cleanup under lock, and refusal to reuse a failed sender context.

The socket tests use the actual UDP loop with a linked simulated sender and
ephemeral loopback ports. They cover IPv4, IPv6, IPv4 broadcast, malformed and
unsupported packets, first-pixel extraction, trailing bytes, exclusive binding,
bounded batches, repeated colors, signals, and fatal sender failures. A paced
20 Hz input with 30 ms simulated sends delivered all 20 colors. With 80 ms
simulated sends, queued updates coalesced and the final requested color arrived.
Neither case measures actual LED output or Ethernet loss during quiet windows.

## Built artifacts

| Artifact | File bytes | SHA-256 |
|---|---:|---|
| `dld-init` | 33,013 | `8bae30b2dc34384741baa72cf3a65d2005b0911efb02f6dadb1ba419c50b59ce` |
| `dld-send` | 29,956 | `b3ff12f2477180640bc3b702a6ba3c1d1e76d4782a933bd6e55593c44cd7c72d` |
| `dld-udp` | 33,662 | `39a12218a21ddbe4fd792be188302d055e1ec4aa3a796e339ac9d641cea2e494` |
| PRU firmware | 4,412 | `da45899a51fdedbf80ec2a7d60c785f5be82970caf115f807b54719661084a60` |
| `dld_quiet.ko` | 15,451 | `1e9aa2b967c5930d820989a0c76acc1f7a88eafce6ff184f156ea49687753414` |

The PRU firmware and kernel module match the protected endurance build byte for
byte. The host binaries use the test directory's compile-time lock path;
choosing another path changes their hashes. The receiver uses the existing
system libraries and adds no package dependency.

The local, Git-excluded archive `build/udp-implementation/` retains the native
build report, source archive, and verification log. The build report's SHA-256
is `e2811634560efc0c81e770c6b78bfd099c81c04528639dd1920593b81f3062c0`.
That report includes the compiled source hashes and runtime dependencies.
The exact native verification log is retained at
`build/udp-implementation/native/validation-final.log`, with SHA-256
`cf015cf7b3c7b5913d2c19f83e14db3d6149fbcdcce8dffd1e0a784208f7d21a`.
The same archive retains the built binaries, firmware listing, and module
disassembly. These paths identify local artifacts excluded from Git.

## Remaining physical work

Run the shim with the actual controller and capture the panel outputs to
measure sustained 20 Hz delivery, packet loss, latency, and waveform behavior
under the intended load. Matching protected binaries and passing software tests
do not extend the earlier CLI capture results to new end-to-end UDP coverage.
Installed-panel qualification and production startup integration remain open.

See [UDP operation](udp.md) for the supported interface and
[the test guide](../tests/README.md) for repeatable checks. The prior
[waveform investigation and endurance report](validation-20260906.md) remains
the record of the completed physical bench.
