# Hardware access notes

The included `src/dld_hw.c` is a small AM335x-specific UIO mapping and loader
implementation. It uses the memory layout and control-register behavior
documented by TI and the legacy `prussdrv` implementation as references; it
does not link to an installed LEDscape library or copy its streaming loop.

- [AM335x TRM](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf): PRUSS, GPIO,
  clock-control and pin-control register definitions.
- [TI/BeagleBoard legacy PRU package](https://github.com/beagleboard/am335x_pru_package):
  UIO map0, PRU control, instruction-RAM loading and notification indices.
- [Linux 3.8 UIO mapping](https://raw.githubusercontent.com/torvalds/linux/v3.8/drivers/uio/uio.c):
  physical mappings use `pgprot_noncached`.
- [Linux 3.8 ARM physical-memory mapping](https://raw.githubusercontent.com/torvalds/linux/v3.8/arch/arm/mm/mmu.c):
  non-RAM peripheral mappings are uncached; the loader also opens `/dev/mem`
  with `O_SYNC`.

The BBG's PRUSS UIO map0 reports physical base `0x4a300000`, size `0x80000`,
offset zero. The loader discovers a UIO map with that base, validates the
range, and maps only map0. It never maps external DDR buffers. PRU0 local
data RAM begins at offset zero; its instruction RAM starts at `0x34000`.
Closing a command releases mappings and descriptors without disabling PRUs.

GPIO initialization writes only LED bits to CLEARDATAOUT, drains with a
DATAOUT read, then clears only LED bits in OE. GPIO0, GPIO1 and GPIO2 clock
control offsets are `0x408`, `0xac`, and `0xb0` from `0x44e00000` respectively.
Module enable preserves other clock-control bits. No pinmux changes are made.

The GPIO setup directly accesses registers. It assumes Linux does not later
reconfigure or restore the banks' context through suspend/power management.
Reinitialize after a loss of PRUSS/GPIO configuration. Production integration
that permits such transitions needs kernel GPIO ownership/context handling;
the first-pass test does not modify that policy.

Only init and error cleanup write PRU control or GPIO setup registers in the
userspace hardware layer. Ordinary send attachment maps PRUSS and checks the
mailbox; the required kernel helper then validates the retained state, owns
request publication, and controls each protected GPIO-bank grant. Initialization
disables the conventional PRU0/PRU1 notification events 19/20 and host routes 2/3;
it does not reset the interrupt-controller mappings. Firmware raises no
events. Data publication and completion observation use explicit ARM barriers.

Before resetting a core, the loader writes `CONTROL=1` to disable execution,
waits for `RUNSTATE` to clear, and then resets it. Disabling allows the current
multicycle instruction to finish (TRM table 4-41). Takeover disables both PRUs
before polling, with a shared 20 ms wait limit. A core that does not stop is
reported as an error and is not forcibly reset. Cleanup still attempts mailbox
invalidation and all GPIO banks. This is an init/error path; ordinary sends
do not check or change PRU execution state.

An inaccessible userspace MMIO region can raise `SIGBUS` before ordinary error
paths can run. The program does not catch and retry bus faults. Kernel MMIO
accesses can also fault or stall; neither layer guarantees cleanup when the
subsystem itself is inaccessible. The helper qualifies peripheral clocks before
its MMIO checks, while userspace initialization and best-effort cleanup may
enable the owned GPIO clocks and reassert output directions. Kernel cleanup
only clears LED bits on already functional banks; it does not enable clocks
or change directions. See the observed historical bring-up fault and recovery
in [validation](validation.md).

The reference BBG reports available 300/600/800/1000 MHz operating points.
The current ABI4 path requires CPU0 alone and minimum, maximum, and current
frequency all fixed at 1 GHz. The module checks Cortex-A8 identity and reserves
an exclusive, pinned, undivided PMU cycle counter; it checks these prerequisites
before granting pixel output. The preparation script saves the original CPU
and user-LED settings before fixing the CPU policy and disabling those LED
triggers. The old frequency-ceiling/countdown calculation remains in diagnostic
test code and is not the production send wait.

For every granted bank, the helper excludes scheduling and IRQ/FIQ handlers,
verifies MMC/EDMA inactivity, and pauses CPSW CPDMA. The warmed CPU wait loop
reads only the internal cycle counter and registers; PRU0 streams from local
register state. The helper restores CPSW and CPU state before Linux resumes
between banks. Submitted-failure cleanup is kernel-owned, including when the
sender dies, and may be followed by userspace cleanup on an error return.
See [protected operation](quiet-window.md) for the exact clock-qualified
predicates, ownership assumptions, and limits, and the
[September 6 bench report](validation-20260906.md) for measured results.
