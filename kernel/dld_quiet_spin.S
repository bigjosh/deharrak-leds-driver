#define DLD_PRU
#include "dld_abi.h"

    .syntax unified
    .arch armv7-a
    .text
    /* Linux 3.8's Thumb2 module loader rejects cross-mode CALL relocations.
     * Keep its C-facing symbol Thumb; perform the mode switch locally before
     * staging/warmup. No stack, data, or grant access occurs in this veneer. */
    .balign 64
    .thumb
    .thumb_func
    .global dld_quiet_bank_entry
    .type dld_quiet_bank_entry, %function
dld_quiet_bank_entry:
    bx pc
    nop
    .arm
    b .Lbank_arm
    .size dld_quiet_bank_entry, .-dld_quiet_bank_entry

    .balign 64
    .global dld_quiet_bank
    .type dld_quiet_bank, %function
/* r0=mailbox, r1=bank ordinal, r2=PMU cycles, r3=finite iteration cap.
 * Return u64: r0=single observed bank_done, r1=elapsed cycles/flags.
 * bit31 of r1 means counter guard expired; bit30 means grant was withheld.
 * All arguments, saved registers and the return address are staged before
 * publication. IRQ/FIQ and preemption are already masked by the C wrapper.
 *
 * First traverse the SAME cache line/loop with a 16-cycle target and no
 * publication. The actual grant and timed loop occupy that warmed line;
 * there is no C call, stack access or literal load between warmup and grant.
 * The PMU loop contains only CP15 register reads, integer ops and branches.
 */
dld_quiet_bank:
.Lbank_arm:
    push {r4-r8, lr}
    mov r6, r0
    mov r7, r2
    mov r8, r3
    mov r0, #0
    mov r2, #16
    mov r3, #64
    b .Ltimer_start

    .balign 64
    .global dld_quiet_grant_begin
dld_quiet_grant_begin:
.Lgrant_begin:
    dsb sy
    str r1, [r6, #DLD_OFF_BANK_GRANT]
    dsb sy
    .global dld_quiet_timer_start
dld_quiet_timer_start:
.Ltimer_start:
    mrc p15, 0, r4, c9, c13, 0
    .global dld_quiet_loop_begin
dld_quiet_loop_begin:
.Lloop_begin:
    mrc p15, 0, r5, c9, c13, 0
    sub r5, r5, r4
    cmp r5, r2
    bhs .Lloop_end
    subs r3, r3, #1
    bne .Lloop_begin
    orr r5, r5, #0x80000000
    .global dld_quiet_loop_end
dld_quiet_loop_end:
.Lloop_end:
    cmp r0, #0
    bne .Lobserve
    tst r5, #0x80000000
    bne dld_quiet_warm_failed
    mov r0, #1
    mov r2, r7
    mov r3, r8
    b .Lgrant_begin

    .global dld_quiet_observe
dld_quiet_observe:
.Lobserve:
    dmb sy
    ldr r0, [r6, #DLD_OFF_BANK_DONE]
    mov r1, r5
    pop {r4-r8, pc}
dld_quiet_warm_failed:
    mov r0, #0
    orr r1, r5, #0x40000000
    pop {r4-r8, pc}
    .size dld_quiet_bank, .-dld_quiet_bank

    .section .note.GNU-stack,"",%progbits
