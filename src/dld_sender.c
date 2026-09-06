#define _POSIX_C_SOURCE 200809L
#include "dld_sender.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int unlock_sender(struct dld_sender *sender, char *error, size_t cap)
{
    int rc;
    do { rc = flock(sender->lock_fd, LOCK_UN); } while (rc < 0 && errno == EINTR);
    if (rc == 0) return DLD_OK;
    snprintf(error, cap, "cannot release command lock: %s; restart the sender", strerror(errno));
    /* Closing also releases the lock. Never reuse a context with a bad fd. */
    close(sender->lock_fd);
    sender->lock_fd = -1;
    sender->faulted = DLD_PREREQUISITE;
    return DLD_PREREQUISITE;
}

void dld_sender_close(struct dld_sender *sender)
{
    if (sender->mapped) dld_hw_close(&sender->hw);
    if (sender->quiet_fd >= 0) close(sender->quiet_fd);
    if (sender->lock_fd >= 0) close(sender->lock_fd);
    memset(sender, 0, sizeof(*sender));
    sender->lock_fd = sender->quiet_fd = -1;
}

int dld_sender_open(struct dld_sender *sender, char *error, size_t cap)
{
    struct dld_command state;
    struct dld_config config;
    int code;
    memset(sender, 0, sizeof(*sender));
    sender->lock_fd = sender->quiet_fd = -1;
    sender->lock_fd = dld_lock(error, cap);
    if (sender->lock_fd < 0) {
        code = -sender->lock_fd;
        sender->lock_fd = -1;
        return code;
    }
    if (geteuid() != 0) {
        snprintf(error, cap, "sender requires effective UID zero");
        code = DLD_PREREQUISITE;
        goto failed;
    }
    if (dld_hw_open(&sender->hw, 0, error, cap) < 0) {
        code = DLD_PREREQUISITE;
        goto failed;
    }
    sender->mapped = 1;
    dld_snapshot(sender->hw.command, &state);
    code = dld_check_mailbox(&state, &config, error, cap);
    if (code != DLD_OK) goto failed;
    sender->quiet_fd = open(DLD_QUIET_DEVICE, O_RDWR | O_CLOEXEC);
    if (sender->quiet_fd < 0) {
        snprintf(error, cap, "open %s: %s; load the matching dld_quiet kernel helper",
                 DLD_QUIET_DEVICE, strerror(errno));
        code = DLD_PREREQUISITE;
        goto failed;
    }
    code = unlock_sender(sender, error, cap);
    if (code == DLD_OK) return code;
failed:
    dld_sender_close(sender);
    return code;
}

int dld_sender_send(struct dld_sender *sender, uint32_t rgb,
                    struct dld_send_result *result, char *error, size_t cap)
{
    struct dld_command state;
    struct dld_completion completion;
    struct dld_quiet_send *request;
    uint32_t request_seq;
    int code, submitted = 0;
    unsigned i;
    if (!result || rgb > 0xffffffU) {
        snprintf(error, cap, "send requires a 24-bit RGB color and result storage");
        return DLD_BAD_ARGUMENT;
    }
    memset(result, 0, sizeof(*result));
    if (sender->faulted) {
        snprintf(error, cap, "sender context failed; close and reopen after explicit recovery");
        return sender->faulted;
    }
    if (!sender->mapped || sender->lock_fd < 0 || sender->quiet_fd < 0) {
        snprintf(error, cap, "sender is not open");
        return DLD_NOT_INITIALIZED;
    }
    if (flock(sender->lock_fd, LOCK_EX | LOCK_NB) < 0) {
        code = errno == EWOULDBLOCK || errno == EAGAIN ? DLD_BUSY : DLD_PREREQUISITE;
        snprintf(error, cap, code == DLD_BUSY ? "another dld command holds the command lock" :
                 "cannot acquire command lock: %s", strerror(errno));
        return code;
    }
    /* A cooperating init or CLI may have run while this resident client was
     * idle. Refresh profile, lengths and sequence under the same lock every
     * time; only descriptors/mappings, never configuration, are cached.
     */
    dld_snapshot(sender->hw.command, &state);
    code = dld_check_mailbox(&state, &result->config, error, cap);
    if (code != DLD_OK) goto done;
    request_seq = state.request_seq + 1u;
    request = &result->quiet;
    request->api_version = DLD_QUIET_API_VERSION;
    request->rgb = rgb;
    request->profile_id = result->config.profile_id;
    request->previous_seq = state.request_seq;
    for (i = 0; i < DLD_STRING_COUNT; ++i)
        request->string_lengths[i] = result->config.string_lengths[i];
    if (dld_cancelled) {
        snprintf(error, cap, "send cancelled before request publication");
        code = DLD_PREREQUISITE;
        goto done;
    }
    /* ENOTTY/EPERM definitively reject before publication. Other syscall
     * errors are uncertain, including EFAULT on copyout after transmission.
     * The shared core preserves the CLI's conservative failure cleanup.
     */
    if (ioctl(sender->quiet_fd, DLD_QUIET_IOCTL_SEND, request) < 0) {
        int ioctl_error = errno;
        if (ioctl_error == ENOTTY || ioctl_error == EPERM) {
            snprintf(error, cap, "quiet helper rejected before publication: %s; %s",
                     strerror(ioctl_error), ioctl_error == ENOTTY ?
                     "load the matching dld_quiet kernel helper" : "CAP_SYS_RAWIO is required");
            code = DLD_PREREQUISITE;
            goto done;
        }
        submitted = 1;
        snprintf(error, cap, "CRITICAL quiet ioctl failed: %s; submission uncertain; run dld-init CONFIG_FILE",
                 strerror(ioctl_error));
        code = DLD_CRITICAL;
        goto done;
    }
    submitted = request->submitted != 0;
    if (request->result && !submitted) {
        code = request->result == -EBUSY ? DLD_BUSY :
               request->result == -EPROTO ? DLD_NOT_INITIALIZED : DLD_PREREQUISITE;
        snprintf(error, cap, "quiet helper rejected before publication: stage=%" PRIu32
                 " result=%" PRId32 " (%s) cpu_khz=%" PRIu32 " blocked_engine=%" PRIu32
                 " blocked_status=0x%08" PRIX32,
                 request->stage, request->result, strerror(-request->result), request->cpu_khz,
                 request->blocked_engine, request->blocked_status);
        goto done;
    }
    completion.status = request->status;
    completion.completion_seq = request->completion_seq;
    completion.error_detail = request->error_detail;
    if (request->result || dld_cancelled || !submitted || request->request_seq != request_seq ||
        !dld_completion_matches(&completion, request_seq)) {
        submitted = 1;
        snprintf(error, cap,
                 "CRITICAL %s: request=%" PRIu32 " status=0x%08" PRIX32
                 " completion=%" PRIu32 " error=%" PRIu32 " (%s) stage=%" PRIu32
                 " result=%" PRId32 " grant=%" PRIu32 " bank_done=%" PRIu32
                 " timer_fault=0x%" PRIX32 " blocked_engine=%" PRIu32 " blocked_status=0x%08" PRIX32
                 " cleanup_failed=0x%" PRIX32 " dma_restore_failed=0x%" PRIX32
                 "; run dld-init CONFIG_FILE",
                 dld_cancelled ? "send cancelled" : "protected send failed",
                 request_seq, completion.status, completion.completion_seq,
                 completion.error_detail, dld_error_name(completion.error_detail), request->stage,
                 request->result, request->bank_grant, request->bank_done, request->timer_fault_mask,
                 request->blocked_engine, request->blocked_status, request->cleanup_failed_mask,
                 request->dma_restore_failed_mask);
        code = DLD_CRITICAL;
        goto done;
    }
    code = DLD_OK;
done:
    if (submitted && code != DLD_OK) {
        dld_hw_cleanup(&sender->hw);
        sender->faulted = DLD_CRITICAL;
    }
    if (code == DLD_OK) {
        code = unlock_sender(sender, error, cap);
    } else {
        char unlock_error[160];
        int faulted = sender->faulted;
        (void)unlock_sender(sender, unlock_error, sizeof(unlock_error));
        if (faulted) sender->faulted = faulted;
    }
    return code;
}
