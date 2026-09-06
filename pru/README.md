# PRU firmware and timing audit

`ws2812_uniform.p` targets PRU0 on AM335x and assembles with the vendored
PASM 0.84 (`-V3 -b -l -L`). Its entry point is instruction zero. All four
initial profile identifiers are supported, including the distinct BGR
variants; their wire-order conversion is performed by the ARM kernel helper.
The image
uses the shared definitions in `include/dld_abi.h` and `include/dld_profiles.h`.
It does not use PRU1, R30 outputs, R31 events, DDR, the PRUSS shared RAM, or a
stack. C24 is explicitly configured for PRU0 local RAM at startup. The command
block is the only local data allocation; firmware configuration is never
overwritten by its working counters.

One transmitter is reused for GPIO2, GPIO1, then GPIO0. Before each bank it
loads the bank's endpoint lengths and pin masks. All 24 bit positions are
unrolled, but every pin still active in the bank uses the same SET and CLEAR.
After bit 0, equal-to-endpoint comparisons remove finished pins. Zero lengths
are excluded before transmission. Original lengths are reloaded for every
request, including after a sequence-number wrap.

## ABI4 bank gates

The mailbox is 72 bytes. Offsets 0–52 retain their previous layout; ABI3 is
rejected because it permits an ungated whole-frame start. The appended words
are:

| Offset | Field | Writer | Meaning |
|---:|---|---|---|
| 56 | `bank_ready` | PRU | Bank awaiting a grant; zero while active or finished |
| 60 | `bank_grant` | Kernel | Zero before request publication; matching ordinal releases one bank |
| 64 | `bank_done` | PRU | Last bank whose final clear/readback completed |
| 68 | `accepted_seq` | PRU | Echo of the frame request accepted for this gate sequence |

Ordinals are fixed: 1 = GPIO2, 2 = GPIO1, 3 = GPIO0. Empty banks are skipped
without renumbering or grants. Initialization starts with all four fields zero.
Before publishing a new frame, the kernel clears `bank_grant`; a nonzero stale
grant at acceptance causes `DLD_ERROR_GATE` (10) before any pixel edge. Firmware
clears the previous ready/done values and publishes `accepted_seq`.

For each enabled bank, configuration and mask setup precede `WAIT_BANK` (4) and
`bank_ready` publication. The PRU reads only the local grant word until the
matching ordinal arrives. Zero or an earlier ordinal waits; a future ordinal
is a protocol error. The kernel confirms the accepted sequence and bank
readiness outside the quiet window, then masks the required CPU activity and
writes the grant from its audited grant/cycle-counter routine. The PRU clears
`bank_ready`, sets RUNNING, and emits the existing timed bank stream.

After final GPIO clear/readback, the PRU publishes `bank_done`. It performs no
next-bank pixel writes until the next matching grant, so pending interrupts
can run between protected passes. The kernel observes that bank completion
once after its cycle-counter wait. An indefinite wait for a missing grant is safe
for the outputs (held low); the host must still bound its own coordination
wait and handle a failed operation.

The final bank completion is **not frame DONE**. Firmware waits through the
full panel-wide settling allowance before updating `completion_seq` and DONE.
The kernel can restore interrupts during that low-only wait. All-zero lengths
need no grants and still perform the settling wait. No ungated compatibility
path exists in this firmware; plain request publication cannot start data.

The host-side limits are separate from this gate protocol: initial and per-bank
DMA admission use a 1 second deadline and 10,000-attempt cap, with CPSW and CPU
state restored between attempts. Gate readiness and final frame completion
each use a separate 20 ms deadline and 200-attempt cap. A granted bank has a
`24 × Lbank × 1,200 + 600,000` cycle timer at fixed 1 GHz, or 9.24 ms at length
300, with no post-grant grace period or retry. IRQ/FIQ and preemption are masked
for each protected bank. A finite iteration guard handles a stopped PMU but
can exceed the normal timer target; no counter bounds a wedged MMIO access.
See [quiet-window operation](../docs/quiet-window.md) for the implementation
and appliance limits.

## Nominal issuing schedule

At 200 MHz, each ordinary register/branch instruction takes one 5 ns cycle.
The instruction model assigns an aligned four-byte external `SBBO` a nominal
one-cycle issue cost. It is a posted operation: GPIO edge latency is additional
and can vary with interconnect contention. The model below is therefore an issuing schedule,
not a physical timing guarantee. TI documents this distinction in its
[PRU read/write latency FAQ](https://e2e.ti.com/support/processors-group/processors/f/processors-forum/1096933/faq-pru-how-do-i-calculate-read-and-write-latencies).

For a delay with count `n` and parity flag `p` (0 or 1), the SUB/QBNE loop is
`2n` cycles and the parity helper is `2+p` cycles. The current profile loads:

| Interval | Formula | Count | Parity | Cycles |
|---|---|---:|---:|---:|
| Zero high | `4 + 2n + p` | 33 | 0 | 70 |
| One high | `4 + 2n + p` | 68 | 0 | 140 |
| Ordinary zero low | `6 + 2n + p` | 82 | 0 | 170 |
| Ordinary one low | `6 + 2n + p` | 47 | 0 | 100 |
| Pixel-ending zero low | `18 + 2n + p` | 76 | 0 | 170 |
| Pixel-ending one low | `18 + 2n + p` | 41 | 0 | 100 |

The low-interval formula includes the next bit's selection instruction.
The pixel boundary contributes twelve additional cycles: one increment,
three balanced three-cycle endpoint checks, and the conditional exit plus
absolute loop jump. An absolute jump is necessary because the unrolled
kernel exceeds the reach of a quick relative branch. Both branch paths of
each endpoint check have the same duration. The SET and CLEAR masks cannot
change within a bit. Consequently every continuing pin has 240 cycles
between rising-edge issue points in this nominal model, including when
neighboring strings finish.

The completed September 6 protected overnight captures measured rising periods
of 1,206–1,214 ns against the 1,200 ns target. The approximately 10 ns bias was
not tuned away; its cause is not established. High, in-frame low, and period
checks remained within the unchanged ±50 ns diagnostic tolerances. This
physical result does not prove the assumed issue cost or isolate the source
of the bias; the [bench report](../docs/validation-20260906.md) records measured
ranges, sampling limits, and acquisition gaps.

Parity paths retain one-cycle (5 ns) adjustment capability. Timing profiles
must leave enough time for setup and boundary accounting: positive loop
counts require at least six high cycles and twenty low cycles. The supplied
profiles have considerably more time than these implementation minima.

## Completion and errors

Initialization verifies every LED pin's output direction, clears each bank's
entire fixed LED mask, and reads GPIO_DATAOUT. A bank readback can be retried
at most `DLD_DRAIN_ATTEMPTS` times. The same check occurs outside the timed
stream at the end of each nonempty bank. A stalled external bus transaction
itself cannot be given a wall-clock bound by a PRU instruction counter; the
host's critical-completion failure path remains necessary.

The firmware computes the panel-wide settling delay once at initialization:
`reset + max(longest_length - 1, 0) * propagation + margin`. It uses the
longest configured string across all banks and waits after the final
successful readback. With no active banks it still executes the settling
wait. The loop count is rounded upward to two-cycle iterations; setup and
return instructions add time. Initialization uses the same wait.

The initial 1 us per forwarding pixel and 100 us margin are diagnostic
engineering allowances from the shared header. They are not measured bounds
for installed hardware and must be qualified alongside symbol timing,
voltage levels, byte order, and the host timing assumptions before production.

Completion sequence and zero error detail are stored before DONE. Error
detail is stored before ERROR, then the PRU halts. No interrupts are raised.
The kernel owns shutdown and best-effort forcing of the pins low after a
submitted execution failure, even when the sender receives `SIGKILL` or the
failure result cannot be copied back. The surviving CLI adds cleanup after
critical/uncertain results or handled cancellation. A successful raw ioctl
whose result copyout fails does not itself trigger kernel execution-failure
cleanup; the CLI conservatively treats that `EFAULT` as uncertain. No firmware
error causes automatic frame retransmission. Inaccessible hardware may
prevent cleanup, and driving data low does not restore the prior picture.

## Automated audit

Run `python3 tests/pru_audit.py IMAGE.bin IMAGE.lst` after assembly. Python 3.2
or newer is sufficient and no third-party packages are used. The audit:

- compares every raw-listing opcode with the binary, then decodes the binary;
- rejects memory reads, other GPIO accesses, and interrupt/output-register
  writes in the timed kernel and verifies 24 immediate bit selections;
- executes initialization, bank selection, the generated transmitter, drains,
  settling, completion, and error paths;
- holds every bank gate without GPIO activity, releases each enabled ordinal,
  checks bank completion follows clear/readback, and rejects stale/future
  grants; an earlier grant cannot release a later bank;
- checks every GPIO symbol's masks, high width, rising-edge period, pin count,
  skipped-bank behavior, unrelated-bit preservation, and final low state;
- covers both base profiles with full traces, both BGR variants with
  representative traces, all 64 enable combinations on base profile 1,
  unequal and equal endpoints, maximum lengths, repeated requests, and
  sequence wrap;
- executes all eight combinations of odd/even zero-high, one-high, and bit
  period using the generated parity paths with register-loaded test values;
- injects invalid magic/ABI/profile/lengths, incorrect directions, and failed
  clear/readbacks, checking the finite retry count and error publication.

Only a decoded SUB/QBNE pair that decrements one register to zero is
accelerated; its exact cycle count includes the final failed branch. Memory
operations outside the stream are assigned a lower-bound nominal cost,
which suffices to check that the explicit settling loop cannot finish early.
The audit does not model OCP arbitration, write buffering, output-pad or
LED propagation, GPIO clock gating, PRU/ARM concurrent access, or Linux
scheduling. Oscilloscope validation remains required.
