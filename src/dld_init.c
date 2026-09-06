#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include "dld_hw.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int expired(const struct timespec *now, const struct timespec *deadline)
{
    return now->tv_sec > deadline->tv_sec ||
        (now->tv_sec == deadline->tv_sec && now->tv_nsec >= deadline->tv_nsec);
}

static int wait_ready(struct dld_hw *hw, struct timespec deadline,
                       char *error, size_t cap)
{
    const struct timespec pause = { 0, 1000000 }; /* no pixel stream during init */
    for (;;) {
        struct timespec now;
        struct dld_command state;
        if (dld_cancelled) {
            snprintf(error, cap, "initialization cancelled by signal %d", (int)dld_cancelled);
            return -1;
        }
        if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
            snprintf(error, cap, "cannot read readiness clock: %s", strerror(errno));
            return -1;
        }
        if (expired(&now, &deadline)) {
            snprintf(error, cap, "PRU readiness exceeded the 100 ms deadline");
            return -1;
        }
        dld_snapshot(hw->command, &state);
        if (state.error_detail != DLD_ERROR_NONE || state.status == DLD_STATUS_ERROR) {
            snprintf(error, cap, "PRU initialization error %" PRIu32 ": %s",
                     state.error_detail, dld_error_name(state.error_detail));
            return -1;
        }
        if (state.status == DLD_STATUS_READY && state.request_seq == 0 && state.completion_seq == 0 &&
            state.bank_ready == 0 && state.bank_grant == 0 && state.bank_done == 0 && state.accepted_seq == 0) {
            /* A scheduler pause between the earlier clock read and this
             * snapshot must not grant a late readiness observation success.
             */
            if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
                snprintf(error, cap, "cannot verify readiness clock: %s", strerror(errno));
                return -1;
            }
            if (expired(&now, &deadline)) {
                snprintf(error, cap, "PRU readiness was not observed within the 100 ms deadline");
                return -1;
            }
            return 0;
        }
        if (state.status != DLD_STATUS_INITIALIZING) {
            snprintf(error, cap, "unexpected initialization status=0x%08" PRIX32
                     " request=%" PRIu32 " completion=%" PRIu32,
                     state.status, state.request_seq, state.completion_seq);
            return -1;
        }
        /* EINTR returns to the same absolute deadline and cancellation check. */
        if (nanosleep(&pause, NULL) < 0 && errno != EINTR) {
            snprintf(error, cap, "readiness sleep failed: %s", strerror(errno));
            return -1;
        }
    }
}

int main(int argc, char **argv)
{
    struct dld_config config;
    struct dld_hw hw;
    struct timespec deadline;
    char error[256];
    int code, lock_fd = -1, opened = 0, taken = 0;
    unsigned i;
    if (argc != 2) {
        fprintf(stderr, "usage: dld-init CONFIG_FILE\n");
        return DLD_BAD_ARGUMENT;
    }
    code = dld_read_config(argv[1], &config, error, sizeof(error));
    if (code != DLD_OK) goto report;
    if (dld_install_signals(error, sizeof(error)) < 0) { code = DLD_PREREQUISITE; goto report; }
    lock_fd = dld_lock(error, sizeof(error));
    if (lock_fd < 0) { code = -lock_fd; goto report; }
    if (geteuid() != 0) {
        snprintf(error, sizeof(error), "dld-init requires effective UID zero");
        code = DLD_PREREQUISITE; goto done;
    }
    if (dld_hw_open(&hw, 1, error, sizeof(error)) < 0) { code = DLD_PREREQUISITE; goto done; }
    opened = 1;
    code = DLD_INIT_FAILURE;
    if (dld_cancelled) {
        snprintf(error, sizeof(error), "initialization cancelled before taking control");
        goto done;
    }
    taken = 1;
    if (dld_hw_take_control(&hw, error, sizeof(error)) < 0 ||
        dld_hw_configure_outputs(&hw, error, sizeof(error)) < 0) goto done;
    hw.command->magic = DLD_COMMAND_MAGIC;
    hw.command->abi_version = DLD_ABI_VERSION;
    hw.command->profile_id = config.profile_id;
    for (i = 0; i < DLD_STRING_COUNT; ++i) hw.command->string_lengths[i] = config.string_lengths[i];
    hw.command->wire_color = 0;
    hw.command->request_seq = 0;
    hw.command->completion_seq = 0;
    hw.command->error_detail = DLD_ERROR_NONE;
    hw.command->bank_ready = 0;
    hw.command->bank_grant = 0;
    hw.command->bank_done = 0;
    hw.command->accepted_seq = 0;
    hw.command->status = DLD_STATUS_INITIALIZING;
    dld_memory_barrier();
    if (dld_cancelled) {
        snprintf(error, sizeof(error), "initialization cancelled before firmware startup");
        goto done;
    }
    /* Start the deadline immediately before the loader starts the PRU. This
     * includes its small code-copy cost conservatively; never grant extra time
     * after an interrupted sleep or a delayed process resume.
     */
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) < 0) {
        snprintf(error, sizeof(error), "cannot start readiness clock: %s", strerror(errno));
        goto done;
    }
    deadline.tv_nsec += 100000000;
    if (deadline.tv_nsec >= 1000000000) { ++deadline.tv_sec; deadline.tv_nsec -= 1000000000; }
    if (dld_hw_start(&hw, dld_pru_code, dld_pru_code_size, error, sizeof(error)) < 0) goto done;
    if (wait_ready(&hw, deadline, error, sizeof(error)) < 0) goto done;
    if (dld_cancelled) {
        snprintf(error, sizeof(error), "initialization cancelled by signal %d", (int)dld_cancelled);
        goto done;
    }
    printf("OK ");
    dld_print_configuration(&config);
    code = DLD_OK;
done:
    if (opened) {
        if (code != DLD_OK && taken) dld_hw_cleanup(&hw);
        dld_hw_close(&hw);
    }
    if (lock_fd >= 0) close(lock_fd);
report:
    if (code != DLD_OK) fprintf(stderr, "dld-init: %s\n", error);
    return code;
}
