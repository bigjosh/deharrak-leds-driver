# First-pass validation — 2026-09-05

This is the historical ABI3 report for the original reference board. On the
replacement board, 2026-09-06 captures found GPIO0 timing violations and prompted
the ABI4 [protected kernel path](quiet-window.md). The measurements below remain
historical evidence and do not qualify the current implementation.

The two commands built and ran on the supplied BBG, and software and live
mailbox/GPIO lifecycle checks passed. Physical waveform and LED qualification
were still outstanding at this historical handback. Later replacement-board
waveform results appear in the [2026-09-06 report](validation-20260906.md); actual
installed-panel qualification remains separate.

## Build and memory fit

The reference build used the board's existing Debian 7, Linux
`3.8.13-bone80`, GCC 4.6.3, Make 3.81, binutils 2.22, armhf and glibc 2.13
environment in a dedicated project directory. No system packages or tools
were installed or upgraded. PASM 0.84 was built from the included, unmodified
TI source. The [evidence index](evidence/README.md) records the historical
local archive and source hashes.

| Artifact | Result |
|---|---|
| PRU0 firmware | 4,308 bytes / 1,077 instructions, within 8,192-byte instruction RAM |
| PRU0 data | 56-byte ABI-3 mailbox, within separate 8,192-byte local data RAM |
| `dld-init` | 33,050-byte ARM executable, including embedded firmware |
| `dld-send` | 27,702-byte ARM executable |
| Runtime libraries | Existing ARM loader, libc, librt, libgcc_s and libpthread; no LEDscape library |
| External DDR frame allocation | None |

Windows and BBG assembly produce the identical firmware SHA-256:

```text
05dcef6ad37e3b7854415616b188e0a3786766b5d2dd98909b1c4ebd7978a916
```

Both reference executables used the same compile-time lock path in that
dedicated project directory. The pair must be used together; rebuilding for
another deployment requires choosing its shared lock path explicitly.

## Automated checks

[Original result excerpts](evidence/2026-09-05/historical-checks.json) preserve
the source line numbers and SHA-256 hashes.

| Check | Result and coverage |
|---|---|
| Native C tests | PASS, 481 checks: parsing, profiles, masks, timing budgets, mailbox guards and completion policy |
| CLI rejection tests | PASS, 15 checks using the actual ARM commands |
| PRU machine-code audit | PASS on both Windows and BBG: 138 frames on the byte-identical firmware |
| Linked ARM audit | PASS: exact aligned 28-byte publication/spin routine; repeated loop is only `SUBS` and `BNE` |
| Live lifecycle suite | PASS, 1,390 checks / 136 commands |
| Live sender signals | PASS, 245 checks / 23 commands |
| LEDscape-to-DLD handover | PASS, two additional complete service-start/stop/init/send cycles without reboot |

The PRU audit decodes the assembled instructions and checks exact per-pin
lengths, bank masks/order, 70/140/240-cycle symbol paths, pixel-boundary timing,
5 ns parity adjustments, all 64 enable combinations, sequence wrap, initialization
errors, finite GPIO drains, final settling, and absence of reads or interrupts
inside timed bank streams. Base profiles have full traces; the BGR variants
have representative traces through the same transmitter. This is a nominal
instruction-issue model, not a model of physical GPIO edges or bus stalls.

The live suite checks zero, unequal and maximum lengths; retained configuration;
all four profiles' mailbox byte order; sequence progression; PRU0 running and
PRU1 disabled; output directions and low latches; malformed-input and lock
rejection; stopped READY/DONE causing critical failure; outstanding requests
remaining busy without cleanup; and explicit recovery. These endpoint checks
do not measure the intervening waveform or the pixels' actual colors.

The signal suite killed a still-running sender after publication and verified
that its request completed independently, followed by a successful send without
init. SIGTERM after publication produced critical exit 5, invalidation, stopped
cores and low GPIO latches; a subsequent send returned 7 until explicit init.
Publication and interruption are verified; the exact PRU bit phase at the
signal is not established. Sequence wrap is covered by the machine model,
not by rewriting both counters underneath a running PRU.

The supplied board's kernel independently drives its four user LEDs on GPIO1
bits 21–24. Live suites explicitly excluded only those four DATAOUT bits from
snapshot comparisons and logged the exclusion. Every OE bit and other DATAOUT
bit remained checked. Kernel LED triggers were not changed; endpoint comparisons
cannot establish absence of transient writes.

## Timing evidence and limits

PRUSS clock inspection reports 200 MHz. The supported ARM ceiling is 1 GHz;
the host uses one dependent counter cycle per iteration as a conservative
minimum bound. The linked assembly is warmed on a local dummy mailbox before
publication. The preserved [seven-sample benchmark](evidence/2026-09-05/spin-benchmark.txt) measured:

| Iterations | Conservative minimum | Observed minimum–maximum |
|---:|---:|---:|
| 100,000 | 0.100 ms | 0.201–0.331 ms |
| 1,000,000 | 1.000 ms | 2.014–2.100 ms |
| 28,219,000 | 28.219 ms | 56.999–57.214 ms |

Every observation exceeded the minimum bound. These samples do not establish
worst-case execution time or immunity to scheduling and DMA activity. CPU
frequency policy, Linux interrupts and scheduling remain active.

For six lengths of 300, the nominal bank data total is 25.920 ms. The initial
settling allowance is 0.699 ms: 300 us reset + 299 forwarding stages at 1 us
each + 100 us margin. The host's minimum wait budget is 28.219 ms after adding
100 us acceptance, 500 us control and 1 ms host guard allowances. Actual host
spin is longer, as measured above, and process startup adds more latency.
The ABI3 design then specified less than 30 ms for PRU execution through
settling, excluding total CLI elapsed time; it was not physically established
in this test. The later protected design withdrew that whole-frame bound
because it permits Linux activity between bank passes.

The 1 us forwarding allowance, 100 us margin and control allowances are initial
engineering choices. They are not qualified bounds for the installed LEDs or
worst-case interconnect contention. Success confirms the PRU published matching
DONE after its configured wait; the LEDs provide no return acknowledgment.

## Bring-up fault and board restoration

The first loader attempt encountered an ARM `SIGBUS` while resetting an
active PRU. Subsequent direct reads throughout PRUSS also aborted, and the
original LEDscape program failed on restart. PRCM readings nevertheless
reported clocks enabled and reset deasserted. A PRUSS-only reset pulse and
runtime UIO-driver reload did not recover access. One BBG reboot restored it.
The precise hardware cause was not established.

The final loader disables execution, waits for `RUNSTATE` to clear, and only
then resets a core, following the multicycle-instruction stop semantics in
[TI TRM table 4-41](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf).
The full live suites and the two additional handover cycles passed afterward;
no external-abort messages occurred after the recovery reboot. This evidence
supports the revised handover, but does not prove the earlier fault's cause.
Both reboot and the stop sequence changed before the successful tests, so
these observations do not isolate which change recovered access.
An MMIO bus fault bypasses normal C cleanup, so low-output or invalidation
guarantees cannot be claimed when the subsystem itself becomes inaccessible.

LEDscape was restored **active and enabled** after live testing. SHA-256 checks
confirmed unchanged contents for its service unit, configuration and startup
script. Its normal startup rewrites its configuration with the same content.
No existing project source, installed tools, service definitions or boot
configuration were edited. Test sources, diagnostics and results remain in
the dedicated new project directory. DLD must be handed ownership and
initialized again before further sends.

## Physical acceptance work outstanding at this handback

This list records the 2026-09-05 state. The following day's replacement-board
header captures are reported separately and do not qualify the installed LEDs.

- Confirm the panel's actual pixel family/revision and BGR mapping using
  primaries and an asymmetric color. The existing LEDscape file identifies
  BGR but only a generic `ws281x` output mode.
- Measure highs, lows, bit period, exact counts, bank ordering, final reset
  and total PRU duration at the header and first pixel's DIN after level shifting.
- Qualify propagation and settling margins against the installed chains.
- Run the separate several-hour `000000` / trigger `>400 ns` and `FFFFFF` /
  trigger `>800 ns` scope phases. No scope or pulse-width endurance result is
  claimed here. Repeat under deliberate load for characterization as agreed.

See [README.md](../README.md) for commands and [tests/README.md](../tests/README.md)
for the reproducible software and live-test procedures. Generated build reports,
disassemblies and raw test logs are retained under the reference build's `build/`
directory and copied into the local `build/bbg/` archive at delivery. That
archive is excluded from Git; the [portable evidence index](evidence/README.md)
links retained excerpts and catalogs the original files and hashes.
