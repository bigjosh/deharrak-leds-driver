# Protected GPIO-bank operation

The required `dld_quiet.ko` helper implements the ABI4 send path authorized on
2026-09-06 after unprotected captures showed GPIO0 timing violations. It works
with the persistent PRU0 firmware loaded by `dld-init CONFIG_FILE`;
`dld-send RRGGBB` invokes its synchronous ioctl. The module is specific to this
AM335x Cortex-A8 appliance and Linux `3.8.13-bone80`. It is not a general DMA
arbiter or a proof of deterministic GPIO-write latency. Physical captures
remain the acceptance evidence.

## What happens during a send

The commands retain their shared nonblocking lock. The module also serializes
ioctls, requires `CAP_SYS_RAWIO`, validates the retained profile/lengths and
ABI4 handshake, and holds CPU-hotplug, network-device, RTNL, and CPSW runtime-PM
references through the operation. It requires only CPU0 online and CPU policy
minimum, maximum, and current frequency all equal to 1 GHz. Its exclusive
pinned perf event must own an advancing, undivided Cortex-A8 cycle counter.
An existing PMU user can make module loading fail; the helper does not take
that user's counter away.

The reference network interface must be named `eth0`, be running, and have a
parent bound to the `cpsw` driver. The helper rejects missing, stopped, renamed,
or differently driven interfaces before publication, including for an
all-disabled request. It does not bring the interface up or reconfigure it.

The 72-byte PRU mailbox contains request/completion sequences plus
`accepted_seq`, `bank_ready`, `bank_grant`, and `bank_done`. GPIO2, GPIO1, and
GPIO0 use fixed ordinals 1, 2, and 3. Empty banks are skipped without renumbering.
The PRU preloads each bank's register state, publishes `WAIT_BANK`, and emits
no data edge until the kernel grants that ordinal for the accepted request.
The kernel owns publication; there is no ABI4 userspace-only fallback.

For each nonempty bank:

1. Wait for its matching gate with normal IRQ execution allowed, under a
   20 ms deadline and an independent attempt cap.
2. Disable preemption and save/mask IRQ and FIQ state. Qualify peripheral
   clocks, then check MMC and EDMA activity in two passes.
3. Check CPSW's functional clock. Save DMACONTROL, reject preexisting
   CMD_IDLE, assert that bit without changing other bits, and verify readback.
   Wait for CPDMA IDLE, rejecting host-error status, within a 500,000-cycle
   drain target and finite polling cap. Recheck MMC/EDMA afterward.
4. Warm the exact assembly path without granting, then publish the grant.
   Wait using only CP15 `PMCCNTR` reads, integer operations, and branches.
   There is no data-memory access, PRU polling, function call, or cancellation
   read inside the repeated loop.
5. At the cycle target, read `bank_done` exactly once. A mismatch or timer
   guard failure is critical; no grace check or bank retransmission follows.
6. Restore and verify the original CPSW control word, then restore the saved
   IRQ/FIQ masks and preemption. Linux and networking may now run before the
   next bank is granted.

The timer target is derived from shared PRU timing constants. At the initial
1,200 ns bit period it is `24 × Lbank × 1,200 + 600,000` CPU cycles: 9.24 ms
for a 300-pixel bank. The 600 µs guard is an engineering allowance, not measured
signal slack. DMA admission, warmup, barriers, and restoration add overhead.
A separate 30-million-iteration cap exits if the PMU stops, but this fault path
can be much longer than the normal target. No software deadline bounds a
hardware MMIO transaction that never returns.

Transient pre-grant `EAGAIN` conditions restore CPU/CPSW state before a bounded
retry, under a 1 second admission deadline and an independent 10,000-attempt
cap. Initial MMC/EDMA admission before publication uses the same policy. The
larger bound allows ordinary storage work and Linux scheduling to finish; it
does not extend the budget of a granted bank. A granted bank, failed restoration,
or CPDMA drain error/timeout is never retried. After all banks the PRU performs
the full reset/propagation/margin hold; overall `DONE` is awaited with IRQs
enabled under a separate 20 ms deadline. All-disabled sends require no grant
but still perform that hold.

The original acceptance-to-`DONE` 30 ms requirement no longer applies. Three
maximum-length streams contain nominally 25.92 ms of data, but gate waits,
guard windows, and enabled Linux execution extend total latency. Measure each
bank, inter-bank gaps, final settling, and whole-command latency separately.

## DMA checks and ownership

CPSW v2 CPDMA DMACONTROL is `0x4a100820`, with CMD_IDLE at bit 3. DMASTATUS is
`0x4a100824`, with IDLE at bit 31 and host-error mask `0x00f0f000`. Command-idle
finishes an in-flight frame and suspends DMA while preserving descriptor
state; incoming frames during the pause can be discarded. Reading DMASTATUS
clears error-channel fields, so the helper retains the first error observation.
These are CPDMA controls, not the MAC sliver's separate idle control.
[TI TRM, §§14.3.2.4.5 and 14.5.2.8–9](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf)

The matching driver wakes queues, replenishes receive descriptors, and
re-enables interrupts from callbacks. RTNL protects lifecycle/reconfiguration;
single-CPU IRQ/FIQ/preemption exclusion prevents callbacks from racing the
raw control change. CPSW is restored **before** callbacks can run between banks.
No descriptors, head/completion pointers, interrupt masks, or channel events
are rewritten. Controller stop/start would tear down or reset state and is
not used as a pause. [Matching CPSW source](https://github.com/beagleboard/linux/blob/3c1c65e1e8f3bfd43a9956edc00f36a351f1cf23/drivers/net/ethernet/ti/cpsw.c),
[matching CPDMA source](https://github.com/beagleboard/linux/blob/3c1c65e1e8f3bfd43a9956edc00f36a351f1cf23/drivers/net/ethernet/ti/davinci_cpdma.c)

| Engine | Physical status register | Bits required zero | CLKCTRL offset |
|---|---|---|---|
| MMC0 | `0x48060224` SD_PSTATE | `0x307` | `0x03c` |
| MMC1 | `0x481d8224` SD_PSTATE | `0x307` | `0x0f4` |
| EDMA CC | `0x49000640` CCSTAT | `0x00073f17` | `0x0bc` |
| EDMA TC0 | `0x49800100` TCSTAT | `0x77` | `0x024` |
| EDMA TC1 | `0x49900100` TCSTAT | `0x77` | `0x0fc` |
| EDMA TC2 | `0x49a00100` TCSTAT | `0x77` | `0x100` |

Clock offsets are relative to CM_PER base `0x44e00000`; CPSW's offset is
`0x014`. MMC activity includes command/data inhibition and read/write transfer
activity, not just an EDMA burst. TCSTAT reserved bit 8 resets to one: requiring
the whole register to equal zero is wrong. MMC STAT is latched interrupt
status and is not used as an idle predicate. [TI TRM, table 2-1 and
§§11.4.1.85, 11.4.2.4, 18.5.1.16](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf),
[matching MMC source](https://github.com/beagleboard/linux/blob/3c1c65e1e8f3bfd43a9956edc00f36a351f1cf23/drivers/mmc/host/omap_hsmmc.c)

Before MMIO, require `(CLKCTRL & 0x30003) == 2`: MODULEMODE enabled and
IDLEST functional. A disabled MMC/CC is skipped only when that mask equals
`0x30000`; a disabled TC additionally requires STBYST bit 18. Transitional or
interface-only idle states are rejected. Checks and associated reads occur
inside short CPU-excluded batches so a Linux runtime-PM callback cannot gate
the controller between them. PRUSS and cleanup GPIO accesses are likewise
clock-qualified. The helper never directly enables clocks behind drivers.
[Matching clock offsets](https://github.com/beagleboard/linux/blob/3c1c65e1e8f3bfd43a9956edc00f36a351f1cf23/arch/arm/mach-omap2/cm33xx.h),
[TI TRM, §§8.1.12.1.7, .13, .32, .44, .46–47](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf)

Skipping a disabled engine assumes orderly kernel clock management and no
allowed asynchronous wake/event source. An idle snapshot cannot exclude a
later request from an unexamined autonomous peripheral. These are explicit
appliance assumptions, not properties enforced universally by the module.

## Reference bench preparation and build

The inspected board has Linux `3.8.13-bone80`, GCC 4.6.3, CPU0 only, no swap,
and `CONFIG_MUSB_PIO_ONLY=y`. USB host/gadget drivers and services stay intact:
their PIO/interrupt work cannot execute on the ARM during a bank window.
Read-only inspection found no bound McASP, SPI, ADC, or LCDC client and no
active SGX device. A different kernel or appliance needs a fresh inventory of
autonomous masters; USB DMA cannot be assumed absent there.

The September 6 ownership handover stopped LEDscape and its starfield sender
and excluded their restart for that bench. For a new run, the operator must
exclude every competing PRU/GPIO user: `tools/start-test.sh` checks the named
LEDscape service, but does not discover independent renderers. The core driver
does no service discovery or management. The root filesystem remains mounted
normally; do not freeze it.
Keep runtime binaries and logs in a newly created directory on `/run` tmpfs.

Build in the new dedicated source directory already transferred to the board.
For example, create a new runtime directory and build with its private lock:

```sh
DLD_RUNTIME_DIR=$(mktemp -d /run/dld-protected.XXXXXX)
make KDIR=/usr/src/linux-headers-3.8.13-bone80 LOCK_PATH="$DLD_RUNTIME_DIR/dld.lock"
make KDIR=/usr/src/linux-headers-3.8.13-bone80 LOCK_PATH="$DLD_RUNTIME_DIR/dld.lock" test-native audit
cp build/dld-init build/dld-send build/dld-udp "$DLD_RUNTIME_DIR/"
```

Use the panel's reviewed JSON file for initialization; an example profile does
not identify the installed LEDs. Retain artifact hashes, module vermagic,
assembler listing, module disassembly/audit results, and the actual runtime
directory. Do not overwrite installed tools/modules or run `depmod` as part of
this isolated build. Load only a matching module while no protected operation
is active. Use a fresh build or clean this project's generated objects when
changing `LOCK_PATH`; Make does not infer object dependencies from changed
compiler flags.

The implemented preparation script records the CPU governor/minimum/maximum
and four user-LED trigger/brightness settings before its first change. It then
fixes 1 GHz, sets those triggers to `none` and brightness to zero, and verifies
no swap. It does not unload USB/gadget drivers, stop services, or edit boot files.
Use a new state filename; retain it until restoration succeeds.

```sh
python3 tools/bench_prepare.py --apply "$DLD_RUNTIME_DIR/preparation.json"
insmod kernel/dld_quiet.ko
"$DLD_RUNTIME_DIR/dld-init" /path/to/reviewed-panel.json
"$DLD_RUNTIME_DIR/dld-send" 000000
```

Stop immediately on a failed prerequisite, initialization, or command. Begin
with short captures and controlled failure tests before sustained loaded runs.
The helper's success-line cycle diagnostics and ioctl status explain admission
and execution; only the physical capture measures delivered waveform timing.

## Failure and restoration

Once published, signals do not abandon the finite kernel transaction between
gates. It completes or performs kernel-owned failure cleanup even after
`SIGKILL`. On a failed granted bank, PRU0 execution is disabled before CPSW
resumes when PRUSS is accessible. Cleanup invalidates the mailbox, disables
both owned cores, waits at most 20 ms for RUNSTATE, and resets only stopped
cores. It attempts LED-only CLEARDATAOUT/readback up to eight times on each
functional bank; kernel cleanup does not change directions or unrelated bits.
If the CLI survives an error return or handles cancellation, its additional
userspace cleanup can enable GPIO clocks and reassert the six owned output
directions while clearing the latches. This is outside normal sends and outside
the protected timing loop.

Restore, stop, and low-check failures are separate diagnostics. Nonzero recovery
failure masks also produce a rate-limited kernel error after CPU state has been
restored, before result copyout, so fatal callers cannot hide those failures.
Definitive ENOTTY/EPERM ioctl rejection returns a prerequisite error without
invalidating an initialized session; uncertain errors such as EFAULT require
critical cleanup because output copyout may fail after transmission.
If a PRU cannot
stop, a low readback is only a best-effort observation: a pending write may
finish later. Bus faults or inaccessible hardware can prevent cleanup. A
critical failure requires explicit `dld-init`; no frame is retransmitted
automatically. Forcing low can latch interrupted data and does not guarantee
the previous display or a black display.

At the end of the bench, stop owned load generators and send loops and wait for
active commands to finish. The September 6 handback retains the tested helper
and fixed-frequency/LED preparation so the new driver remains usable. To return
to the recorded earlier runtime, unload only this session's module and restore
the saved preparation state:

```sh
rmmod dld_quiet
python3 tools/bench_prepare.py --restore "$DLD_RUNTIME_DIR/preparation.json"
```

Verify CPU policy, user-LED settings, Ethernet, and MMC operation afterward.
Retain failure logs and preparation state until recovery is confirmed. Return
PRU/GPIO ownership through the separately recorded LEDscape handover; do not
start a competing renderer while this initialized session is still in use.
No USB/gadget restoration is required because this preparation left it intact.
