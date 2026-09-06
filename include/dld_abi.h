#ifndef DLD_ABI_H
#define DLD_ABI_H

// Plain integer constants are shared with the PASM preprocessor.
#define DLD_COMMAND_MAGIC 0x444C4431
#define DLD_ABI_VERSION 4
#define DLD_MAX_LENGTH 300
#define DLD_STRING_COUNT 6
#define DLD_PRU_IRAM_BYTES 8192
#define DLD_PRU_DRAM_BYTES 8192

#define DLD_OFF_MAGIC 0
#define DLD_OFF_ABI_VERSION 4
#define DLD_OFF_PROFILE_ID 8
#define DLD_OFF_LENGTHS 12
#define DLD_OFF_WIRE_COLOR 36
#define DLD_OFF_REQUEST_SEQ 40
#define DLD_OFF_COMPLETION_SEQ 44
#define DLD_OFF_STATUS 48
#define DLD_OFF_ERROR_DETAIL 52
#define DLD_OFF_BANK_READY 56
#define DLD_OFF_BANK_GRANT 60
#define DLD_OFF_BANK_DONE 64
#define DLD_OFF_ACCEPTED_SEQ 68
#define DLD_COMMAND_BYTES 72

// Fixed pass ordinals; empty banks are skipped, never renumbered.
#define DLD_BANK_GPIO2 1
#define DLD_BANK_GPIO1 2
#define DLD_BANK_GPIO0 3

#define DLD_STATUS_INITIALIZING 0
#define DLD_STATUS_READY 1
#define DLD_STATUS_RUNNING 2
#define DLD_STATUS_DONE 3
#define DLD_STATUS_WAIT_BANK 4
#define DLD_STATUS_ERROR 0x80000000
#define DLD_ERROR_NONE 0
#define DLD_ERROR_MAGIC 1
#define DLD_ERROR_ABI 2
#define DLD_ERROR_PROFILE 3
#define DLD_ERROR_LENGTH 4
#define DLD_ERROR_TIMING 5
#define DLD_ERROR_DIRECTION 6
#define DLD_ERROR_INIT_CLEAR 7
#define DLD_ERROR_BANK_CLEAR 8
#define DLD_ERROR_INTERNAL 9
#define DLD_ERROR_GATE 10

#ifndef DLD_PRU
#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/stddef.h>
#define DLD_ABI_U32 __u32
#else
#include <stdint.h>
#include <stddef.h>
#define DLD_ABI_U32 uint32_t
#endif
struct dld_command {
    DLD_ABI_U32 magic;
    DLD_ABI_U32 abi_version;
    DLD_ABI_U32 profile_id;
    DLD_ABI_U32 string_lengths[DLD_STRING_COUNT];
    DLD_ABI_U32 wire_color;
    DLD_ABI_U32 request_seq;
    DLD_ABI_U32 completion_seq;
    DLD_ABI_U32 status;
    DLD_ABI_U32 error_detail;
    DLD_ABI_U32 bank_ready;
    DLD_ABI_U32 bank_grant;
    DLD_ABI_U32 bank_done;
    DLD_ABI_U32 accepted_seq;
};
/* GCC 4.6/C99 compatible compile-time ABI assertions. */
#define DLD_ABI_ASSERT(field, off) typedef char dld_offset_##field[(offsetof(struct dld_command, field) == (off)) ? 1 : -1]
DLD_ABI_ASSERT(magic, DLD_OFF_MAGIC);
DLD_ABI_ASSERT(abi_version, DLD_OFF_ABI_VERSION);
DLD_ABI_ASSERT(profile_id, DLD_OFF_PROFILE_ID);
DLD_ABI_ASSERT(string_lengths, DLD_OFF_LENGTHS);
DLD_ABI_ASSERT(wire_color, DLD_OFF_WIRE_COLOR);
DLD_ABI_ASSERT(request_seq, DLD_OFF_REQUEST_SEQ);
DLD_ABI_ASSERT(completion_seq, DLD_OFF_COMPLETION_SEQ);
DLD_ABI_ASSERT(status, DLD_OFF_STATUS);
DLD_ABI_ASSERT(error_detail, DLD_OFF_ERROR_DETAIL);
DLD_ABI_ASSERT(bank_ready, DLD_OFF_BANK_READY);
DLD_ABI_ASSERT(bank_grant, DLD_OFF_BANK_GRANT);
DLD_ABI_ASSERT(bank_done, DLD_OFF_BANK_DONE);
DLD_ABI_ASSERT(accepted_seq, DLD_OFF_ACCEPTED_SEQ);
typedef char dld_command_size[(sizeof(struct dld_command) == DLD_COMMAND_BYTES && DLD_COMMAND_BYTES <= DLD_PRU_DRAM_BYTES) ? 1 : -1];
#undef DLD_ABI_ASSERT
#undef DLD_ABI_U32
#endif
#endif
