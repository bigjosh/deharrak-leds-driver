// DLD PRU0 firmware, PASM 0.84 / AM335x PRU v3.
// One shared 24-bit transmitter; no R30 output or R31 interrupt writes.
.origin 0
.entrypoint START

#define DLD_PRU 1
#include "../include/dld_abi.h"
#include "../include/dld_profiles.h"

#define GPIO0_CLEAR 0x44E07190
#define GPIO1_CLEAR 0x4804C190
#define GPIO2_CLEAR 0x481AC190
#define GPIO0_MASK (1 << 26)
#define GPIO1_MASK ((1 << 12) | (1 << 14))
#define GPIO2_MASK ((1 << 1) | (1 << 3) | (1 << 4))

// Persistent registers: r3 accepted sequence, r4 wire color, r5 settle-loop
// count, r20/r21 high counts, r22/r23 ordinary low counts, r24/r25 final-bit
// low counts, r27 delay parity flags. Bank registers: r7 CLEAR address,
// r8 fixed bank mask, r9 active mask, r10..r12 endpoint lengths,
// r13..r15 pin bitmasks, r16 completed-pixel count. r18 is the delay counter.
// r26 holds the fixed bank grant ordinal during a pass (1/2/3 = GPIO2/1/0).
// r28.w0 is the group/settle return PC; r29.w0 is the drain return PC.
// r0/r1/r2/r6/r17/r19 are scratch (r2 holds the drain error cause).

// Validate and accumulate the maximum length. PRU quick comparisons evaluate
// operand 3 against operand 2: QBLT bad, length, limit means limit < length.
.macro CHECK_LENGTH
.mparam length_reg
    QBLT bad_length, length_reg, r0
    QBGE length_done, length_reg, r26
    MOV r26, length_reg
    QBA length_done
bad_length:
    MOV r2, DLD_ERROR_LENGTH
    JMP FATAL
length_done:
.endm

.macro CHECK_BANK
.mparam clear_address, fixed_mask
    MOV r7, clear_address
    MOV r8, fixed_mask
    SUB r1, r7, 0x5c             // GPIO_OE = CLEAR - 0x5c
    LBBO r0, r1, 0, 4
    AND r0, r0, r8
    QBEQ directions_ok, r0, 0
    MOV r2, DLD_ERROR_DIRECTION
    JMP FATAL
directions_ok:
    MOV r2, DLD_ERROR_INIT_CLEAR
    JAL r29.w0, DRAIN_BANK
.endm

// DELAY_PARITY costs 2 cycles when the flag is clear and 3 when set.
// It supplies the odd cycle which a SUB/QBNE delay alone cannot express.
.macro DELAY_PARITY
.mparam parity_bit
    QBBS odd_delay, r27, parity_bit
    QBA parity_done
odd_delay:
    MOV r19, r19
    QBA parity_done
parity_done:
.endm

// Each aligned 4-byte external SBBO has a nominal one-cycle issue cost.
// It is a posted write, so this is NOT a bound on pin-edge latency under
// interconnect contention. See pru/README.md and the machine-code audit.
// High: 1(SBBO)+1(MOV)+2*count+2+parity = H cycles.
// Ordinary low: 1(SBBO)+1(MOV)+2*count+2+parity+1(QBA)+1(next QBBS)
//              = 6+2*count+parity = L cycles.
// Final-bit low additionally includes 12 cycles of pixel-boundary work:
//              = 18+2*count+parity = L cycles.
.macro SEND_BIT
.mparam bit_number, zero_low_count, one_low_count
    QBBS bit_one, r4, bit_number
    SBBO r9, r7, 4, 4
    MOV r18, r20
zero_high:
    SUB r18, r18, 1
    QBNE zero_high, r18, 0
    DELAY_PARITY 0
    SBBO r9, r7, 0, 4
    MOV r18, zero_low_count
zero_low:
    SUB r18, r18, 1
    QBNE zero_low, r18, 0
    DELAY_PARITY 2
    QBA bit_done
bit_one:
    SBBO r9, r7, 4, 4
    MOV r18, r21
one_high:
    SUB r18, r18, 1
    QBNE one_high, r18, 0
    DELAY_PARITY 1
    SBBO r9, r7, 0, 4
    MOV r18, one_low_count
one_low:
    SUB r18, r18, 1
    QBNE one_low, r18, 0
    DELAY_PARITY 3
    QBA bit_done
bit_done:
.endm

// Exactly three cycles whether this endpoint finishes or remains active.
// Length zero cannot equal the positive pixel count and is never toggled.
.macro FINISH_PIN
.mparam endpoint, pin_mask
    QBNE keep_pin, r16, endpoint
    XOR r9, r9, pin_mask
    QBA pin_done
keep_pin:
    MOV r19, r19
    MOV r19, r19
pin_done:
.endm

START:
    // Enable external GPIO access, then explicitly point C24 at local RAM 0.
    LBCO r0, C4, 4, 4
    CLR r0, r0, 4                // PRUSS_CFG_SYSCFG.STANDBY_INIT
    SBCO r0, C4, 4, 4
    MOV r1, 0x00022020          // PRU0 CTBIR0
    MOV r0, 0
    SBBO r0, r1, 0, 4

    LBCO r0, C24, DLD_OFF_MAGIC, 4
    MOV r1, DLD_COMMAND_MAGIC
    QBEQ magic_ok, r0, r1
    MOV r2, DLD_ERROR_MAGIC
    JMP FATAL
magic_ok:
    LBCO r0, C24, DLD_OFF_ABI_VERSION, 4
    QBEQ abi_ok, r0, DLD_ABI_VERSION
    MOV r2, DLD_ERROR_ABI
    JMP FATAL
abi_ok:
    LBCO r0, C24, DLD_OFF_PROFILE_ID, 4
    QBEQ profile_ok, r0, DLD_PROFILE_WS2812B
    QBEQ profile_ok, r0, DLD_PROFILE_WS2811_HS
    QBEQ profile_ok, r0, DLD_PROFILE_WS2812B_BGR
    QBEQ profile_ok, r0, DLD_PROFILE_WS2811_HS_BGR
    MOV r2, DLD_ERROR_PROFILE
    JMP FATAL
profile_ok:
    // All four initial profiles share these symbol/settle values. Wire-order
    // conversion is on the host. A profile meaning change requires ABI bump.
    // Positive loop counts are mandatory: zero would underflow and hang.
    MOV r0, DLD_T0H_CYCLES
    QBGT timing_bad, r0, 6
    MOV r0, DLD_T1H_CYCLES
    QBGT timing_bad, r0, 6
    MOV r0, DLD_BIT_CYCLES
    MOV r1, DLD_T0H_CYCLES
    QBGE timing_bad, r0, r1
    SUB r0, r0, r1
    QBGT timing_bad, r0, 20
    MOV r0, DLD_BIT_CYCLES
    MOV r1, DLD_T1H_CYCLES
    QBGE timing_bad, r0, r1
    SUB r0, r0, r1
    QBGT timing_bad, r0, 20
    QBA timing_ok
timing_bad:
    MOV r2, DLD_ERROR_TIMING
    JMP FATAL
timing_ok:
    MOV r20, ((DLD_T0H_CYCLES - 4) / 2)
    MOV r21, ((DLD_T1H_CYCLES - 4) / 2)
    MOV r22, ((DLD_BIT_CYCLES - DLD_T0H_CYCLES - 6) / 2)
    MOV r23, ((DLD_BIT_CYCLES - DLD_T1H_CYCLES - 6) / 2)
    MOV r24, ((DLD_BIT_CYCLES - DLD_T0H_CYCLES - 18) / 2)
    MOV r25, ((DLD_BIT_CYCLES - DLD_T1H_CYCLES - 18) / 2)
    MOV r27, ((DLD_T0H_CYCLES & 1) | ((DLD_T1H_CYCLES & 1) << 1) | (((DLD_BIT_CYCLES - DLD_T0H_CYCLES) & 1) << 2) | (((DLD_BIT_CYCLES - DLD_T1H_CYCLES) & 1) << 3))

    LBCO r10, C24, DLD_OFF_LENGTHS, 24
    MOV r0, DLD_MAX_LENGTH
    MOV r26, 0
    CHECK_LENGTH r10
    CHECK_LENGTH r11
    CHECK_LENGTH r12
    CHECK_LENGTH r13
    CHECK_LENGTH r14
    CHECK_LENGTH r15

    // Tsettle = reset floor + max(Lmax-1,0)*propagation + positive margin.
    // This bounded init-only addition loop avoids the multiplier register ABI.
    MOV r5, (DLD_RESET_CYCLES + DLD_SETTLE_MARGIN_CYCLES)
    MOV r0, DLD_PROPAGATION_CYCLES
    QBEQ settle_calculated, r26, 0
    SUB r26, r26, 1
settle_add:
    QBEQ settle_calculated, r26, 0
    ADD r5, r5, r0
    SUB r26, r26, 1
    QBA settle_add
settle_calculated:
    ADD r5, r5, 1
    LSR r5, r5, 1               // ceil(Tsettle cycles/2); setup adds margin

    CHECK_BANK GPIO2_CLEAR, GPIO2_MASK
    CHECK_BANK GPIO1_CLEAR, GPIO1_MASK
    CHECK_BANK GPIO0_CLEAR, GPIO0_MASK
    JAL r28.w0, WAIT_SETTLE
    MOV r3, 0
    MOV r0, DLD_ERROR_NONE
    SBCO r0, C24, DLD_OFF_ERROR_DETAIL, 4
    SBCO r3, C24, DLD_OFF_COMPLETION_SEQ, 4
    SBCO r3, C24, DLD_OFF_BANK_READY, 4
    SBCO r3, C24, DLD_OFF_BANK_DONE, 4
    SBCO r3, C24, DLD_OFF_ACCEPTED_SEQ, 4
    MOV r0, DLD_STATUS_READY
    SBCO r0, C24, DLD_OFF_STATUS, 4

REQUEST_LOOP:
    LBCO r0, C24, DLD_OFF_REQUEST_SEQ, 4
    QBEQ REQUEST_LOOP, r0, r3
    MOV r3, r0
    // The kernel must clear the old grant BEFORE publishing this frame.
    // A stale grant must never release a new frame's first bank by accident.
    LBCO r0, C24, DLD_OFF_BANK_GRANT, 4
    QBEQ grant_cleared, r0, 0
    MOV r2, DLD_ERROR_GATE
    JMP FATAL
grant_cleared:
    SBCO r0, C24, DLD_OFF_BANK_READY, 4
    SBCO r0, C24, DLD_OFF_BANK_DONE, 4
    SBCO r3, C24, DLD_OFF_ACCEPTED_SEQ, 4
    LBCO r4, C24, DLD_OFF_WIRE_COLOR, 4
    LSR r0, r4, 24
    QBEQ color_ok, r0, 0
    MOV r2, DLD_ERROR_INTERNAL
    JMP FATAL
color_ok:
    MOV r0, DLD_STATUS_RUNNING
    SBCO r0, C24, DLD_OFF_STATUS, 4

    MOV r7, GPIO2_CLEAR
    MOV r8, GPIO2_MASK
    LBCO r10, C24, (DLD_OFF_LENGTHS + 0), 8
    LBCO r12, C24, (DLD_OFF_LENGTHS + 20), 4
    MOV r13, (1 << 3)
    MOV r14, (1 << 4)
    MOV r15, (1 << 1)
    MOV r26, DLD_BANK_GPIO2
    JAL r28.w0, SEND_GROUP

    MOV r7, GPIO1_CLEAR
    MOV r8, GPIO1_MASK
    LBCO r10, C24, (DLD_OFF_LENGTHS + 8), 4
    LBCO r11, C24, (DLD_OFF_LENGTHS + 16), 4
    MOV r12, 0
    MOV r13, (1 << 12)
    MOV r14, (1 << 14)
    MOV r15, 0
    MOV r26, DLD_BANK_GPIO1
    JAL r28.w0, SEND_GROUP

    MOV r7, GPIO0_CLEAR
    MOV r8, GPIO0_MASK
    LBCO r10, C24, (DLD_OFF_LENGTHS + 12), 4
    MOV r11, 0
    MOV r12, 0
    MOV r13, (1 << 26)
    MOV r14, 0
    MOV r15, 0
    MOV r26, DLD_BANK_GPIO0
    JAL r28.w0, SEND_GROUP

    JAL r28.w0, WAIT_SETTLE
    SBCO r3, C24, DLD_OFF_COMPLETION_SEQ, 4
    MOV r0, DLD_ERROR_NONE
    SBCO r0, C24, DLD_OFF_ERROR_DETAIL, 4
    MOV r0, DLD_STATUS_DONE
    SBCO r0, C24, DLD_OFF_STATUS, 4
    JMP REQUEST_LOOP

WAIT_SETTLE:
    MOV r18, r5
settle_wait:
    SUB r18, r18, 1
    QBNE settle_wait, r18, 0
    JMP r28.w0

DRAIN_BANK:
    MOV r17, DLD_DRAIN_ATTEMPTS
    SUB r1, r7, 0x54            // GPIO_DATAOUT = CLEAR - 0x54
drain_retry:
    SBBO r8, r7, 0, 4
    LBBO r0, r1, 0, 4
    AND r0, r0, r8
    QBEQ drain_ok, r0, 0
    SUB r17, r17, 1
    QBNE drain_retry, r17, 0
    JMP FATAL
drain_ok:
    JMP r29.w0

FATAL:
    // Error detail is visible before ERROR. The host owns fail-low shutdown;
    // a failed GPIO access cannot be made reliable by an unbounded PRU retry.
    SBCO r2, C24, DLD_OFF_ERROR_DETAIL, 4
    MOV r0, DLD_STATUS_ERROR
    SBCO r0, C24, DLD_OFF_STATUS, 4
    HALT

SEND_GROUP:
    MOV r9, 0
    QBEQ no_pin_a, r10, 0
    OR r9, r9, r13
no_pin_a:
    QBEQ no_pin_b, r11, 0
    OR r9, r9, r14
no_pin_b:
    QBEQ no_pin_c, r12, 0
    OR r9, r9, r15
no_pin_c:
    QBNE group_nonempty, r9, 0
    JMP r28.w0                 // Empty banks cause no GPIO writes or loops.
group_nonempty:
    // All configuration reads and setup precede the gate. The kernel writes
    // the matching ordinal only after it has entered the quiet window.
    MOV r0, DLD_STATUS_WAIT_BANK
    SBCO r0, C24, DLD_OFF_STATUS, 4
    SBCO r26, C24, DLD_OFF_BANK_READY, 4
GATE_WAIT:
    LBCO r0, C24, DLD_OFF_BANK_GRANT, 4
    QBEQ gate_released, r0, r26
    // Zero or an earlier completed grant waits; an out-of-order future grant
    // is a protocol fault. PRU comparisons evaluate operand3 vs operand2.
    QBGT GATE_WAIT, r0, r26
    MOV r2, DLD_ERROR_GATE
    JMP FATAL
gate_released:
    MOV r0, 0
    SBCO r0, C24, DLD_OFF_BANK_READY, 4
    MOV r0, DLD_STATUS_RUNNING
    SBCO r0, C24, DLD_OFF_STATUS, 4
    MOV r16, 0

TIMED_BEGIN:
SEND_PIXEL:
    SEND_BIT 23, r22, r23
    SEND_BIT 22, r22, r23
    SEND_BIT 21, r22, r23
    SEND_BIT 20, r22, r23
    SEND_BIT 19, r22, r23
    SEND_BIT 18, r22, r23
    SEND_BIT 17, r22, r23
    SEND_BIT 16, r22, r23
    SEND_BIT 15, r22, r23
    SEND_BIT 14, r22, r23
    SEND_BIT 13, r22, r23
    SEND_BIT 12, r22, r23
    SEND_BIT 11, r22, r23
    SEND_BIT 10, r22, r23
    SEND_BIT 9, r22, r23
    SEND_BIT 8, r22, r23
    SEND_BIT 7, r22, r23
    SEND_BIT 6, r22, r23
    SEND_BIT 5, r22, r23
    SEND_BIT 4, r22, r23
    SEND_BIT 3, r22, r23
    SEND_BIT 2, r22, r23
    SEND_BIT 1, r22, r23
    SEND_BIT 0, r24, r25
PIXEL_BOUNDARY:
    ADD r16, r16, 1
    FINISH_PIN r10, r13
    FINISH_PIN r11, r14
    FINISH_PIN r12, r15
    QBEQ TIMED_END, r9, 0
    JMP SEND_PIXEL             // Absolute: kernel exceeds QBNE branch reach.
TIMED_END:
    MOV r2, DLD_ERROR_BANK_CLEAR
    JAL r29.w0, DRAIN_BANK
    // Clear/readback has completed. The next bank remains gated, allowing
    // the host to restore interrupts before opening another quiet window.
    SBCO r26, C24, DLD_OFF_BANK_DONE, 4
    JMP r28.w0
