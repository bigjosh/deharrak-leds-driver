#ifndef DLD_QUIET_H
#define DLD_QUIET_H

#include <linux/types.h>
#include <linux/ioctl.h>

#define DLD_QUIET_API_VERSION 1
#define DLD_QUIET_DEVICE "/dev/dld-quiet"

#define DLD_QUIET_STAGE_VALIDATE 1
#define DLD_QUIET_STAGE_PUBLISH 2
#define DLD_QUIET_STAGE_GATE 3
#define DLD_QUIET_STAGE_DMA 4
#define DLD_QUIET_STAGE_BANK 5
#define DLD_QUIET_STAGE_FINAL 6
#define DLD_QUIET_STAGE_COMPLETE 7
#define DLD_QUIET_STAGE_CPU 8
#define DLD_QUIET_STAGE_PMU 9

/* Fixed-width, pointer-free ABI for both ARM Linux userspace and its kernel.
 * Input fields end at reserved[]. Userspace must zero the entire structure.
 * No caller-selected addresses, masks, timing budgets or loop counts.
 *
 * A recognized ioctl returns zero after copying the structured result even
 * when the operation failed. result is zero or a negative Linux errno;
 * submitted distinguishes preflight rejection from a critical frame failure.
 * Unknown ioctl, bad user pointers and privilege failure use ioctl errno.
 * No granted bank or failed frame is retried. The kernel performs bounded
 * fail-low cleanup even if the caller dies; userspace cleanup is redundant.
 */
struct dld_quiet_send {
    __u32 api_version;
    __u32 rgb;
    __u32 profile_id;
    __u32 string_lengths[6];
    __u32 previous_seq;
    __u32 reserved[2];

    __s32 result;
    __u32 submitted;
    __u32 request_seq;
    __u32 completion_seq;
    __u32 status;
    __u32 error_detail;
    __u32 accepted_seq;
    __u32 bank_ready;
    __u32 bank_grant;
    __u32 bank_done;
    __u32 stage;
    __u32 granted_mask;
    __u32 completed_mask;
    __u32 cpu_khz;
    /* Arrays use fixed bank ordinals: GPIO2, GPIO1, GPIO0. */
    __u32 budget_cycles[3];
    __u32 elapsed_cycles[3];
    __u32 irq_off_cycles[3];
    __u32 dma_drain_cycles[3];
    __u32 dma_status[3];
    __u32 timer_fault_mask;
    __u32 blocked_engine;
    __u32 blocked_status;
    /* Cleanup diagnostics: failed bit0=PRU stop, bits1..3=GPIO0..2;
     * stopped_mask bits0..1=PRU0..1, low_mask bits0..2=GPIO0..2. */
    __u32 cleanup_failed_mask;
    __u32 cleanup_stopped_mask;
    __u32 cleanup_low_mask;
    __u32 dma_restore_failed_mask;
};

#define DLD_QUIET_IOCTL_SEND _IOWR('d', 0x71, struct dld_quiet_send)

#endif
