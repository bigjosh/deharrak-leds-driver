#define DLD_PRU
#include "dld_abi.h"

    .syntax unified
    .arch armv7-a
    .arm
    .text
    .balign 64
    .global dld_publish_spin
    .type dld_publish_spin, %function
/* AAPCS arguments are already in registers on entry:
 * r0=mailbox, r1=wire colour, r2=new request sequence, r3=positive count.
 * No prologue, stack use, literal loads or calls. The complete routine fits
 * inside one aligned 64-byte instruction-cache line. Calling it on a local
 * dummy command first prefaults/warms this same publication and loop path.
 *
 * Cmin=1 cycle per SUBS/BNE iteration is a conservative Cortex-A8 lower
 * bound: successive counter results have a one-cycle integer dependency.
 * Dual issue cannot eliminate that dependency. The runtime verifies a
 * Cortex-A8 and bounds the clock by 1 GHz; slower execution only adds wait.
 * Hardware measurements still qualify the diagnostic normal-execution budget.
 */
dld_publish_spin:
    str r1, [r0, #DLD_OFF_WIRE_COLOR]
    dmb sy
    str r2, [r0, #DLD_OFF_REQUEST_SEQ]
    dsb sy
    .global dld_spin_begin
dld_spin_begin:
    subs r3, r3, #1
    bne dld_spin_begin
    .global dld_spin_end
dld_spin_end:
    bx lr
    .size dld_publish_spin, .-dld_publish_spin

    .section .note.GNU-stack,"",%progbits
