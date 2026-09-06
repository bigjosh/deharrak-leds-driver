# Protected bank helper

`dld_quiet.ko` targets the AM335x Cortex-A8 and Linux
`3.8.13-bone80`. It provides `/dev/dld-quiet` (mode `0600`) and the
pointer-free `DLD_QUIET_IOCTL_SEND` interface in `include/dld_quiet.h`.
The ioctl additionally requires `CAP_SYS_RAWIO`. This is a board-specific
helper for the exclusively owned DLD session, not a general DMA arbiter.

Run from the repository root and build against the exact running kernel's
prepared headers, generated configuration, and `Module.symvers`. The validated
native toolchain is GCC 4.6.3/binutils 2.22 on the armhf board:

```sh
make -C /lib/modules/3.8.13-bone80/build M="$PWD/kernel" modules
make -C kernel audit
```

The top-level `make` also builds this module, with `KDIR` defaulting to
`/lib/modules/$(uname -r)/build`. Neither build command installs or loads it.
For a clean kernel rebuild use
`make -C /lib/modules/3.8.13-bone80/build M="$PWD/kernel" clean`;
the top-level `make clean` only removes listed userspace/firmware outputs.
Keep compilation outside an exclusive waveform-measurement interval.

The native audit uses `objdump -dr` and Python 3.2 or newer. It checks the
actual ARM instruction words for argument staging, cache-line warmup,
publication barriers, the PMU-only loop, the independent iteration guard,
and one completion load. Eleven deliberately dangerous mutations must be
rejected. It also rejects unresolved relocations inside that region.
The module must be rebuilt when the kernel, mailbox ABI or shared timing
constants change. Build success alone does not qualify physical timing.

C compilation inherits the kernel's Thumb2 instruction mode. A small Thumb
entry veneer switches locally into the audited ARM routine before warmup;
its saved Thumb return address is consumed by ARM `POP PC` after completion.
The Linux 3.8 module loader does not implement cross-mode call relocations,
so the audit requires the C call to target this Thumb veneer and rejects
ARM call relocations. The grant and PMU loop remain ARM instructions.

The module reserves an exclusive pinned Linux perf CPU-cycle event on CPU0.
It neither writes arbitrary PMU configuration nor takes a counter away from
another owner. Load fails if the reservation is inactive, assigned to the
wrong counter, divided by 64, or not advancing. The module requires the
only online CPU to be CPU0 on Cortex-A8; the caller's policy must have
minimum, maximum and current frequency all equal to 1 GHz. A PMU watchdog
or another perf session can prevent reservation. Operator preparation and
restoration are described in [quiet-window operation](../docs/quiet-window.md).
Before an ioctl, `eth0` must be running and its parent device must be bound to
the `cpsw` driver; this is required even for six zero lengths. Loading the module
does not start that interface, configure GPIOs, initialize the PRU, or apply
the CPU/user-LED preparation.

The kernel independently checks the mailbox ABI, profile, six lengths,
idle handshake fields and prior sequence. It owns publication of the new
request and each nonempty bank grant. The PRU processes banks in fixed
GPIO2, GPIO1, GPIO0 order, waiting at the next gate while Linux can run.
No user-supplied physical address, timing value or loop count is accepted.
The mailbox is ABI4/72 bytes; the separate ioctl ABI is version 1 and 192 bytes.
Raw ioctl clients must zero the entire structure and cooperate with the same
command `flock` as both CLIs. The module's nonblocking mutex serializes ioctls;
it does not protect against a raw caller racing an independent `dld-init` or
another PRU/GPIO owner outside that lock.

For each bank the kernel disables preemption and saves/disables both IRQ
and FIQ, qualifies controller clocks, verifies MMC and EDMA idleness, then
asserts CPSW `CMD_IDLE` and waits for acknowledgement. It preserves all
other CPSW control bits and keeps RTNL, net-device and runtime-PM references
through the operation. Short admission attempts allow interrupts between
attempts, under a one-second admission deadline and 10,000-attempt cap.
This allows ordinary storage writes to finish while withholding every bank
grant until the same idle predicates pass. A failure to restore CPSW is
always fatal, including after a retryable admission failure.
Initial admission before request publication uses the same one-second policy.
If it fails, `submitted=0` and the retained PRU session is unchanged. Later
admission happens after the frame is accepted but before the next grant;
failure there is critical and takes cleanup. Each PRU gate has its own
20 ms wait, separate from admission and final completion.

`make build/test-admission && build/test-admission` runs the shared admission
policy offline with a fake clock and hardware actions. It covers storage
activity lasting more than 20 ms, persistent activity, a frozen clock, and
terminal post-grant failures. The four possible one-second admission windows
can add about four seconds to a command; a supervising process should allow
additional time for bank execution, bounded gate/reset waits, and Linux
scheduling. This changes waiting before a bank, not its waveform budget.

The assembly executes a warmup traversal without granting the bank. It
then grants once and waits using only CP15 `PMCCNTR` reads, integer
register operations and branches. At 300 pixels the normal PMU target is
9.24 ms, including a 600 us engineering guard. CPSW draining has a separate
500 us target. The single `bank_done` observation after the spin must
match; there is no additional completion grace period or bank retry.
CPU masks and original CPSW control are restored after each bank. Final
frame completion and the full PRU settling period are awaited with IRQs
enabled, using a 20 ms timeout and an independent attempt limit.

The 30-million-iteration guard also exits if the PMU stops advancing.
That failure path can be much longer than the normal 9.24 ms target.
Neither this guard nor a Linux deadline bounds a hardware MMIO transaction
that never returns. The helper reduces documented sources of contention;
it does not prove a universal interconnect-latency limit.

A recognized, authorized ioctl with accessible user memory returns zero after
copying its result structure, even when the requested operation failed.
`result` contains zero or a negative Linux errno; `submitted` distinguishes
an unchanged preflight rejection from a published request. All diagnostic
outputs are initialized by the kernel. `elapsed_cycles`, `irq_off_cycles`,
`dma_drain_cycles` and masks use fixed GPIO2, GPIO1, GPIO0 ordering.
`blocked_engine` is 1 for CPSW, then 2 through 7 for MMC0, MMC1, EDMA CC,
TC0, TC1 and TC2. `blocked_status` contains the offending register value.

An unknown ioctl uses syscall errno `ENOTTY`; missing `CAP_SYS_RAWIO` uses
`EPERM`. Both occur before publication. Bad input or output pointers use
`EFAULT`; output-copy failure can occur after the frame completed, so syscall
errno alone cannot establish that hardware was untouched. `dld-send` treats
ENOTTY/EPERM as prerequisite rejection (exit 3) and other syscall failures as
uncertain/critical (exit 5 with cleanup). Structured prepublication `EBUSY`
maps to exit 6, `EPROTO` to exit 7, and other failures to exit 3. Any published
failure requires explicit initialization, with no automatic frame retry.

The timing arrays report CPU cycles at the required 1 GHz. `irq_off_cycles`
is sampled after IRQ/FIQ masking and before restoring those masks; it is a
diagnostic interval, not an independently measured hard upper bound on all
interrupt latency. `cleanup_stopped_mask` bits 0–1 identify PRU0–1;
`cleanup_low_mask` bits 0–2 identify GPIO0–2. `cleanup_failed_mask` bit 0 means
PRU-stop failure and bits 1–3 mean GPIO0–2 cleanup failures.
`dma_restore_failed_mask` uses the fixed bank ordinals.

After publication, pending signals do not abandon the PRU between gates.
The finite request either completes or takes kernel-owned failure cleanup,
even if the sender was killed or its result buffer is no longer writable.
On a failed granted bank, PRU0's enable bit is cleared and the mailbox is
invalidated before CPSW resumes, where the PRUSS clock permits access. Full
cleanup then disables both owned PRUs, waits
up to 20 ms for `RUNSTATE` to clear, and asserts reset only on stopped
cores. It then attempts each bank's six-pin subset through
`CLEARDATAOUT`, with bounded readback retries. It does not change unrelated
GPIO bits or enable clocks behind their drivers. Cleanup failure masks
remain visible in the ioctl result. Nonzero cleanup or DMA-restoration failure
masks also produce a rate-limited kernel error after IRQ/FIQ/preemption state
has been restored, before result copyout. If a core cannot stop, a low readback
is only a best-effort observation: a pending PRU write could complete
later. Userspace also performs its existing cleanup on reported failure.

The admission assumptions matter: all competing PRU/GPIO owners are
excluded; disabled DMA engines have no allowed asynchronous wake source;
no unexamined autonomous DMA client is active. A fully disabled MMC block
is not accessed. Disabled EDMA transfer controllers also require their
standby indication. Functional clock checks and the corresponding MMIO
reads occur under CPU exclusion so a Linux runtime-PM callback cannot gate
the block between them. See [quiet-window operation](../docs/quiet-window.md) for the register-source
references, board preparation, and physical qualification limits.
