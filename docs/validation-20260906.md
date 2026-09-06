# Physical bench validation — 2026-09-06

**Bench completed: 13:10 UTC.** The final protected build ran for 6 hours,
20 minutes of wall time: **155,917 successful sends, no failed sends, and no
waveform violations in more than 4.45 billion captured pulses**. All fourteen
full endurance phases passed; the final zero phase was shortened for handback.
The earlier protected run's eMMC admission rejection remains documented below.
At handback, the tested driver was left loaded and ready, with all six data
outputs low.
This establishes the measured bench result; installed-panel qualification and
production startup integration remain outstanding. The [portable evidence index](evidence/README.md) records artifact identities,
provenance, and the final handback before the 13:17 UTC cutoff. Detailed local
archives are indexed separately; raw captures are not included in Git.

## Board and measurement conditions

This replacement BBG runs Linux `3.8.13-bone80` with the native GCC 4.6.3/armhf
toolchain. Its legacy configuration specifies **100 pixels per string and GBR**,
unlike the preceding reference board. Pixel family, downstream wiring, and
actual LED color/latch behavior have not been established. The prior board's
[2026-09-05 report](validation.md) is separate historical evidence.

Captures used Saleae Logic Pro 8, Logic 2.4.46, six
digital channels at 500 MS/s, 3.3 V setting (nominal 1.65 V threshold), and no
software glitch filters. Channels 0–5 map to P8_8, P8_10, P8_12, P8_14, P8_16,
P8_18. Original `.sal` files and binary transition exports are retained. These
are data-pin digital measurements, not analog signal-integrity measurements,
last-pixel DIN measurements, or direct timestamps of PRU `DONE`.

At 05:04 UTC, read-only process inspection found an unrelated preexisting
terminal workload, started at 04:53:08 UTC. It added CPU and network traffic
during the loaded scout and was left untouched. Later phases labelled `none` mean no additional supervisor load,
not an otherwise idle board. It coincides with the measured cadence change
in the [timing comparison](evidence/2026-09-06/cadence-summary.json).

## Legacy and userspace-only comparison

Three original LEDscape/starfield captures were preserved before takeover:
[legacy-001](evidence/2026-09-06/legacy-captures.json),
[legacy-002](evidence/2026-09-06/legacy-captures.json), and
[legacy-003](evidence/2026-09-06/legacy-captures.json).
Their recorded durations are 1.090519040, 10.094291584, and 30.008846976 seconds.
The last capture contains 42,422,400 complete pulses across six channels, with
2,400-pulse bursts consistent with the configured 100-pixel strings. Ordinary
periods cluster near 1,400 ns, with extra pixel-boundary lows. In that capture,
highs reached 720 ns on GPIO2, 938 ns on GPIO1, and 1,130 ns on GPIO0; inferred
24-bit-boundary lows reached approximately 7,790 ns. It ran during a native
audit, so this is characterized legacy behavior, not a matched idle-load
comparison or a pass/fail decoding claim.

Handover completed at 03:31:59 UTC without a bus fault; original installation
hashes remained unchanged. The userspace-only driver initially passed 129
matrix cases plus four fully bracketed smoke captures: 3,513,384 complete
pulses and 1,566 complete channel frames. The first smoke attempt truncated a
final frame and was retained but excluded from that result.
[Reviewed short-test aggregate](evidence/2026-09-06/userspace-short-tests.json)

Longer normal-load captures then exposed faults that successful CLI completion
did not detect:

| Userspace-only capture | Recorded duration | Complete pulses, six channels | Observations |
|---|---:|---:|---|
| [First zero](evidence/2026-09-06/first-zero-analysis.json) | 30.025624192 s | 17,152,416 | GPIO0 high 570 ns, nearby high 248 ns, periods 1,320/1,100 ns, and a separate 1,252 ns period |
| [First one](evidence/2026-09-06/first-one-analysis.json) | 30.025624192 s | 17,163,093 | Three 1,252 ns periods on GPIO0 |

The zero capture's nine checker counts are overlapping consequences of two
event clusters, not nine independent glitches. The 570 ns high occurs at
15.717317702 s in that capture. Its software threshold classification is not
an observation of an LED decoding or latching the wrong bit. The 1,252 ns
period exceeds the unchanged 1,250 ns criterion by one 2 ns sample; it was not
silently waived. Other channels had no ±50 ns violations in that zero capture.
All 1,192 sends in its surrounding 90-second run reported success.
[Independent raw-edge review](evidence/2026-09-06/first-zero-anomaly.json)

The stopped [userspace-only scout](evidence/2026-09-06/userspace-scout-summary.json)
recorded another 73.232548 seconds / 41,491,358 pulses in its zero phase with
further violations. At 03:55 UTC the user redirected the bench to kernel
protection; the failing userspace-only soak was not resumed. Phase recovery
is consistent with transient edge-delivery latency, but these captures do not
identify a unique PRU, interconnect, electrical, or instrument cause.

## Protected implementation and completed checks

ABI4 uses a 72-byte mailbox and per-bank grants. The helper masks scheduling,
IRQ and FIQ activity on CPU0, checks clock-qualified MMC/EDMA inactivity,
asserts/verifies CPSW command-idle, grants one PRU bank, and uses an audited
PMU-register loop with one bank-completion observation. CPSW is restored and
verified before CPU handlers resume between banks. Final settling is awaited
with IRQs enabled. Submitted-failure cleanup belongs to the kernel, including
when the sender cannot perform userspace cleanup.
[Mechanism, register sources, and limits](quiet-window.md)

At 04:06 UTC the bench applied fixed 1 GHz and disabled the four user-LED
triggers, retaining their previous state in
[preparation-state.json](evidence/2026-09-06/preparation-state.json).
No swap was present. Test binaries and logs used a dedicated tmpfs runtime;
the root filesystem was not frozen. USB/gadget configuration remained intact:
the matching kernel uses `CONFIG_MUSB_PIO_ONLY=y`. No installed files or boot
configuration were changed.

The first module load was rejected before initialization because the old
Thumb-2 kernel loader could not interwork direct ARM-to-Thumb calls. Thumb-2
C plus a local Thumb bridge into the audited ARM loop corrected this; the
module loaded at 04:36 UTC without reboot or an installed-kernel change.
The tested PRU image was 4,412 bytes. The [build identity excerpts](evidence/2026-09-06/earlier-build-identity.json)
identify the unchanged firmware and CLIs; the [handback summary](evidence/2026-09-06/handback-summary.json)
identifies the final module. The earlier build report predates that module.

| Completed check | Result |
|---|---|
| Native portable C checks | PASS — 487 |
| Actual ARM CLI rejection checks | PASS — 15 |
| Gated PRU machine-code model | PASS — 139 frames |
| Final module instruction audit | PASS — including 11 rejected dangerous mutations |
| [Protected smoke](evidence/2026-09-06/protected-short-captures.json) | PASS — 5 cases; 864,864 complete pulses; 156 complete channel frames |
| [Protected matrix](evidence/2026-09-06/protected-short-captures.json) | PASS — 129 cases; 2,648,520 pulses; 1,410 complete channel frames; no fragments |
| Live lifecycle suite | PASS — 1,866 checks / 169 commands |
| Live signal suite | PASS — 256 checks / 23 commands |
| Direct kernel probe | PASS — 97 checks / 17 documented rejections; independent kernel cleanup after a published raw ioctl's sender was killed |

The [software and live-check excerpts](evidence/2026-09-06/software-and-live-checks.json)
retain original result lines and source hashes. The initial protected build
log predates the final Thumb bridge and expanded 11-case audit; the final
audit is identified separately. The [direct kernel probe](evidence/2026-09-06/kernel-probe-events.jsonl)
is retained in full. Detailed local logs are cataloged in the
[evidence manifest](evidence/manifest.json).
These tests cover mailbox,
gating, rejection, and cleanup behavior, not LED latch verification. The direct
probe did not rely on userspace cleanup or automatic reinitialization to pass.

## First longer protected captures

[protected-first-long](evidence/2026-09-06/early-protected-summary.json)
completed two 90-second normal-load zero/one phases. Five captures recorded
**122.540785536 seconds, 117,136,891 complete pulses, and 16,264 complete channel
frames, with zero recorded violations** across all six channels.

Independent raw GPIO0 review found:

| Phase | High width | In-frame low width | Rising period |
|---|---:|---:|---:|
| Zero | 348–360 ns | 848–864 ns | 1,206–1,212 ns |
| One | 696–712 ns | 498–512 ns | 1,206–1,212 ns |

The approximately 1,209.92 ns average period retains a roughly 10 ns bias from
the 1,200 ns target, within the unchanged ±50 ns test tolerance. Its cause is
not established. Maximum-length reported IRQ/FIQ counter intervals were
approximately 9.26 ms per bank against the normal 9.24 ms bank-timer target
plus overhead. These counter intervals begin after masking and are sampled
before restoration; entry and restore overhead is outside the measurement.
[Independent early protected review](evidence/2026-09-06/early-protected-summary.json)

Durations sum captured intervals, not the 180 seconds of sender wall time.
Export/analysis gaps are unobserved. Complete pulses can occur in partial
boundary frames; complete channel frames count whole frames on individual
strings, not complete six-string requests. Primary captures are counted once;
duplicate review analyses add no coverage. These early clean samples do not
establish a long-run failure rate or prove that all original causes disappeared.

## Loaded results and admission revision

**06:25 stop:** after 3,934 successful sends in its third phase, the next CLI
returned prerequisite error 3 before publishing its request. The helper's
20 ms DMA admission wait expired with MMC1/eMMC PSTATE `0x01E70106`; bits
`0x106` identify write/data activity. This observation does not establish that
the device was continuously busy for 20 ms, or identify the original writer;
Linux scheduling can delay repeat observations. No frame was retried.

The retained mailbox remained valid DONE at sequence 3,934, and all GPIO output
latches were low. The final capture contains 83 complete frames per string,
ending in the last successful `FFFFEF` color and then 14.922 seconds low on
the last bank. No additional frame followed for the rejected next request.
[Final capture review](evidence/2026-09-06/admission-failure-final-capture.md)

The supervisor had checked 1,007,092,287 pulses over 3,461.240321536 seconds
with zero violations. Separate analysis of its preserved final capture adds
3,585,600 clean pulses over 30.025624192 seconds: **1,010,677,887 pulses and
3,491.265945728 seconds total**, without changing the original failed-run status.
The subsequent revision lengthens only IRQ-enabled DMA admission. Waveform
acceptance, post-grant checks, and PRU deadlines remain unchanged.

At 06:43 UTC, the revised module was loaded with a 1 second/10,000-attempt
admission bound. The shared policy passed 17 offline assertions on the BBG;
the module instruction audit again passed all 11 negative cases. The PRU
firmware, CLI binaries, and protected bank implementation were unchanged.

Two subsequent controlled tests reused the retained initialization. They wrote
64 KiB and called `fsync`, with at least 250 ms between updates, into new 1 MiB
files in the dedicated build directory. Both tests passed without a failed send:

| Controlled storage case | Sends | Complete pulses | Complete channel frames |
|---|---:|---:|---:|
| [Zero](evidence/2026-09-06/storage-admission-captures.json) | 397 | 17,150,400 | 2,382 |
| [One](evidence/2026-09-06/storage-admission-captures.json) | 395 | 17,064,000 | 2,370 |

Every expected frame was present, with no partial frames or waveform violations.
The writers performed 317 and 312 `fsync` calls, with maxima of 51.991 ms and
45.870 ms wall time. Those durations include scheduling and are not measurements
of continuous MMC busy time. The original writer and its exact busy duration
remain unidentified. The test watchdog now allows 7 seconds per CLI and retains
bounded termination/deadline handling.

[Protected-loaded-001](evidence/2026-09-06/protected-short-captures.json)
completed all seven phases at 05:08:45 UTC: 21 captures, 539.600005312 seconds,
137,101,949 complete pulses, 19,037 complete channel frames, and zero waveform
violations or failed sends. It covered cycle/random colors, DDR zero/one,
CPU cycle, and Ethernet zero/one. Host UDP injection actually averaged about
640 packets/s with 128-byte payloads despite a 2,000 packets/s requested rate;
the log records local socket acceptance rather than network delivery.

The final build adds two reviewed error-path fixes: definitive ENOTTY/EPERM
rejections preserve the session, and kernel recovery-failure logs survive fatal
callers or failed result copyout. Five offline tests of the real CLI paths pass.
Native functional checks and the final module's 11-case instruction audit pass.
The unchanged native firmware passed the 139-frame model on Windows; its redundant
native repeat was deliberately stopped. The [model result](evidence/2026-09-06/pru-audit.txt)
records the firmware audit. The [earlier build identity](evidence/2026-09-06/earlier-build-identity.json)
records the same firmware and CLIs but an earlier module; use the
[handback identity](evidence/2026-09-06/handback-summary.json) for the final module. A [final physical smoke](evidence/2026-09-06/protected-short-captures.json)
passed five cases, 864,864 pulses and 156 channel frames with no fragments or violations.

[Protected-overnight-001](evidence/2026-09-06/final-overnight-summary.json)
started at 05:15 UTC with the [16-phase plan](evidence/2026-09-06/overnight-001-plan.json)
and stopped as described above. Its first two half-hour phases completed;
the third stopped early, and later phases were not run in that sequence.

## Final endurance run and handback

[Protected-overnight-002](evidence/2026-09-06/final-overnight-summary.json)
ran from **06:50:11 to 13:10:23 UTC**, following the
[revised schedule](evidence/2026-09-06/overnight-002-plan.json).
It completed fourteen full phases covering cycling and random colors, all four
pixel profiles, full and unequal string lengths, GPIO0 alone, CPU load, DDR
load, and Ethernet injection. The final black-frame phase added 2,132 sends
before the planned handback stop. Across all fifteen phases, **155,917 sends
succeeded and none failed**; every sender/load exit was zero and every cleanup
error list was empty.

The supervisor retains `stopped` and its last phase retains the generic `fail`
label with `Cancelled('operator stop requested')`. These identify the deliberate
STOP at 13:10:21, not a waveform, driver, or acquisition fault. They have not been
rewritten as a successful complete 1,800-second final phase.

The last 30-second acquisition finished at 13:10:15.56, before STOP interrupted
export bookkeeping. Its metadata remains `exporting`; all six binary exports
passed independent length/header checks and waveform analysis, and the native
`.sal` archive passed [ZIP CRC and export integrity checks](evidence/2026-09-06/final-export-integrity.json).
Its separate [manual analysis](evidence/2026-09-06/final-supplement-analysis.json) adds
8,696,683 complete pulses and 1,206 channel frames. Capture-boundary fragments
are retained and excluded from complete-frame counts. The capture ends during
data transmission; the later hardware snapshot establishes final idle state.

The [final aggregation](evidence/2026-09-06/final-overnight-summary.md)
and its [machine-readable source accounting](evidence/2026-09-06/final-overnight-summary.json)
record every phase and analysis identity. Primary analyses and each run's
manual final-capture supplement are counted once:

| Overnight run / admission policy | Reviewed captures | Captured seconds | Complete pulses | Complete channel frames | Waveform violations | Failed sends |
|---|---:|---:|---:|---:|---:|---:|
| 001 / former 20 ms admission | 117 | 3,491.265945728 | 1,010,677,887 | 140,332 | 0 | 1 prepublication rejection |
| 002 / final 1 s admission | 612 | 18,257.095062016 | 4,452,206,525 | 663,562 | 0 | 0 |

The final build therefore has **5 hours, 4 minutes, 17 seconds of analyzed
capture intervals** within its 6 hours, 20 minutes of wall time. These intervals
include reset and idle time. Acquisition/export/analysis gaps remain unobserved;
the send count spans both captured and uncaptured periods. The earlier policy's
results are kept separate rather than presented as coverage of the final build.

Across both overnight runs, the observed extrema were:

| Measurement | Target | Observed range |
|---|---:|---:|
| Zero high | 350 ns | 348–360 ns |
| One high | 700 ns | 696–712 ns |
| Zero in-frame low | 850 ns | 848–864 ns |
| One in-frame low | 500 ns | 498–514 ns |
| Rising-edge bit period | 1,200 ns | 1,206–1,214 ns |

All remain within the original ±50 ns limits. The approximately 10 ns period
bias was not tuned away, and tolerances were not widened. Digital threshold
crossings are measured at 2 ns sampling intervals; the displayed nanosecond
ranges do not imply better instrument resolution or analog qualification.

The [operational metrics](evidence/2026-09-06/operational-metrics.json)
record a maximum measured IRQ/FIQ counter interval of **9.266178 ms per bank**
and maximum reported CPSW drain of **2.783 microseconds**. The counter starts
after masking and is sampled before restoring interrupt state, so the reported
interval excludes that entry/restore overhead and is not a hard bound on all
interrupt-masked time. At the observed background load,
full-panel cadence was about 6.7 sends/s without an added supervisor load,
5.3 sends/s with CPU or DDR load, and 6.6 sends/s with Ethernet injection.
GPIO0-only cadence was 12.7 sends/s. These include Linux scheduling and CLI
overhead; they are not isolated PRU throughput measurements. Ethernet injection
averaged about 640 locally accepted 128-byte UDP packets/s, with no measurement
of packet delivery or loss.

The [read-only handback snapshot](evidence/2026-09-06/handback-summary.json)
passed all sixteen checks at **13:10:40–13:10:41 UTC**. At that historical
snapshot, the mailbox was valid ABI4 DONE at request/completion/accepted
sequence 2,132, with no error. The retained configuration was `ws2812b`, six
lengths of 300, and color `000000`. All six owned GPIOs were outputs with low
latches; PRU0 was enabled and PRU1 stopped. The tested helper was loaded, the
CPU was fixed at 1 GHz, all four user LEDs were prepared, and no swap was
active. No kernel messages appeared after the latest helper load. All owned
target workers had exited; LEDscape was inactive, and the unrelated terminal
workload was untouched.

[Windows cleanup evidence](evidence/2026-09-06/windows-handback-summary.json) records
no remaining owned supervisor, capture, or UDP worker. The finished Logic
capture tab may remain open. No additional BBG or Saleae operations were begun
after this handback. The runtime was retained in temporary `/run` storage,
which does not survive reboot. Durable local evidence is cataloged in the
[evidence index](evidence/README.md). Original CPU/LED settings were saved for
explicit restoration; boot configuration and the original installation were
unchanged. These statements describe the handback, not the present state of
any board used by a future checkout.


The protection depends on one online CPU, fixed frequency, the reserved PMU,
correct peripheral ownership, and the documented absence of unexamined
autonomous masters. A disabled/idle snapshot cannot rule out every future
external event, and a software timeout cannot bound wedged MMIO. Kernel and
capture success also do not qualify downstream propagation, last-pixel reset,
electrical margins, or actual installed LED revisions. Those limits remain
as described in [quiet-window operation](quiet-window.md) and the [spec](../spec.md).
