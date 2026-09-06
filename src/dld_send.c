#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include "dld_hw.h"
#include "dld_quiet.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    struct dld_config config;
    struct dld_hw hw;
    struct dld_command state;
    struct dld_completion completion;
    struct dld_quiet_send request;
    uint32_t rgb, request_seq;
    char error[512];
    int code, lock_fd = -1, quiet_fd = -1, opened = 0, submitted = 0;
    unsigned i;
    if (argc != 2 || dld_parse_color(argv[1], &rgb) < 0) {
        fprintf(stderr, "usage: dld-send RRGGBB (optional 0x/0X prefix; exactly six hex digits)\n");
        return DLD_BAD_ARGUMENT;
    }
    if (dld_install_signals(error, sizeof(error)) < 0) { code = DLD_PREREQUISITE; goto report; }
    lock_fd = dld_lock(error, sizeof(error));
    if (lock_fd < 0) { code = -lock_fd; goto report; }
    if (geteuid() != 0) {
        snprintf(error, sizeof(error), "dld-send requires effective UID zero");
        code = DLD_PREREQUISITE; goto done;
    }
    if (dld_hw_open(&hw, 0, error, sizeof(error)) < 0) { code = DLD_PREREQUISITE; goto done; }
    opened = 1;
    dld_snapshot(hw.command, &state);
    code = dld_check_mailbox(&state, &config, error, sizeof(error));
    if (code != DLD_OK) goto done;
    quiet_fd = open(DLD_QUIET_DEVICE, O_RDWR | O_CLOEXEC);
    if (quiet_fd < 0) {
        snprintf(error, sizeof(error), "open %s: %s; load the matching dld_quiet kernel helper",
                 DLD_QUIET_DEVICE, strerror(errno));
        code = DLD_PREREQUISITE; goto done;
    }
    request_seq = state.request_seq + 1u; /* equality protocol intentionally wraps */
    memset(&request, 0, sizeof(request));
    request.api_version = DLD_QUIET_API_VERSION;
    request.rgb = rgb;
    request.profile_id = config.profile_id;
    request.previous_seq = state.request_seq;
    for (i = 0; i < DLD_STRING_COUNT; ++i)
        request.string_lengths[i] = config.string_lengths[i];
    if (dld_cancelled) {
        snprintf(error, sizeof(error), "send cancelled before request publication");
        code = DLD_PREREQUISITE; goto done;
    }
    /* Only the kernel publishes the frame and grants its individual banks.
     * There is deliberately no userspace-only fallback for gated firmware.
     * ENOTTY/EPERM are rejected before publication by this ioctl contract;
     * other syscall errors leave submission uncertain, including EFAULT
     * because result copyout can fail after the transaction has executed.
     */
    if (ioctl(quiet_fd, DLD_QUIET_IOCTL_SEND, &request) < 0) {
        int ioctl_error = errno;
        if (ioctl_error == ENOTTY || ioctl_error == EPERM) {
            snprintf(error, sizeof(error), "quiet helper rejected before publication: %s; %s",
                     strerror(ioctl_error), ioctl_error == ENOTTY ?
                     "load the matching dld_quiet kernel helper" : "CAP_SYS_RAWIO is required");
            code = DLD_PREREQUISITE;
            goto done;
        }
        submitted = 1;
        snprintf(error, sizeof(error), "CRITICAL quiet ioctl failed: %s; submission uncertain; run dld-init CONFIG_FILE",
                 strerror(ioctl_error));
        code = DLD_CRITICAL; goto done;
    }
    submitted = request.submitted != 0;
    if (request.result && !submitted) {
        code = request.result == -EBUSY ? DLD_BUSY :
               request.result == -EPROTO ? DLD_NOT_INITIALIZED : DLD_PREREQUISITE;
        snprintf(error, sizeof(error), "quiet helper rejected before publication: stage=%" PRIu32
                 " result=%" PRId32 " (%s) cpu_khz=%" PRIu32 " blocked_engine=%" PRIu32
                 " blocked_status=0x%08" PRIX32,
                 request.stage, request.result, strerror(-request.result), request.cpu_khz,
                 request.blocked_engine, request.blocked_status);
        goto done;
    }
    completion.status = request.status;
    completion.completion_seq = request.completion_seq;
    completion.error_detail = request.error_detail;
    if (request.result || dld_cancelled || !submitted || request.request_seq != request_seq ||
        !dld_completion_matches(&completion, request_seq)) {
        submitted = 1; /* Inconsistent helper success also requires explicit recovery. */
        snprintf(error, sizeof(error),
                 "CRITICAL %s: request=%" PRIu32 " status=0x%08" PRIX32
                 " completion=%" PRIu32 " error=%" PRIu32 " (%s) stage=%" PRIu32
                 " result=%" PRId32 " grant=%" PRIu32 " bank_done=%" PRIu32
                 " timer_fault=0x%" PRIX32 " blocked_engine=%" PRIu32 " blocked_status=0x%08" PRIX32
                 " cleanup_failed=0x%" PRIX32 " dma_restore_failed=0x%" PRIX32
                 "; run dld-init CONFIG_FILE",
                 dld_cancelled ? "send cancelled" : "protected send failed",
                 request_seq, completion.status, completion.completion_seq,
                 completion.error_detail, dld_error_name(completion.error_detail), request.stage,
                 request.result, request.bank_grant, request.bank_done, request.timer_fault_mask,
                 request.blocked_engine, request.blocked_status, request.cleanup_failed_mask,
                 request.dma_restore_failed_mask);
        code = DLD_CRITICAL;
        goto done;
    }
    printf("OK color=%06" PRIX32 " quiet_cycles=%" PRIu32 ",%" PRIu32 ",%" PRIu32
           " dma_cycles=%" PRIu32 ",%" PRIu32 ",%" PRIu32 " ", rgb,
           request.irq_off_cycles[0], request.irq_off_cycles[1], request.irq_off_cycles[2],
           request.dma_drain_cycles[0], request.dma_drain_cycles[1], request.dma_drain_cycles[2]);
    dld_print_configuration(&config);
    code = DLD_OK;
done:
    if (opened) {
        if (submitted && code != DLD_OK) dld_hw_cleanup(&hw);
        dld_hw_close(&hw);
    }
    if (quiet_fd >= 0) close(quiet_fd);
    if (lock_fd >= 0) close(lock_fd);
report:
    if (code != DLD_OK) fprintf(stderr, "dld-send: %s\n", error);
    return code;
}
