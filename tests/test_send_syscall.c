#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include "dld_hw.h"
#include "dld_quiet.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <unistd.h>

/* Offline integration test: compile the real sender main with only its OS
 * boundary redirected. Real color parsing, mailbox validation and result
 * handling come from dld_common.o. No hardware object is linked, no device
 * or lock file is opened, and no privilege is required. */
static struct dld_command fake_command;
static int injected_errno, simulate_completed_frame, boundary_error;
static unsigned ioctl_calls, cleanup_calls, attach_calls, detach_calls;
static int lock_open, device_open;

static int fake_open(const char *path, int flags, ...);
static int fake_close(int fd);
static uid_t fake_geteuid(void);
static int fake_ioctl(int fd, unsigned long operation, ...);
static int fake_lock(char *error, size_t cap);

#define main sender_main_under_test
#define open fake_open
#define close fake_close
#define geteuid fake_geteuid
#define ioctl fake_ioctl
#define dld_lock fake_lock
#include "../src/dld_send.c"
#undef main
#undef open
#undef close
#undef geteuid
#undef ioctl
#undef dld_lock

static int fake_open(const char *path, int flags, ...)
{
    if (strcmp(path, DLD_QUIET_DEVICE) || (flags & O_ACCMODE) != O_RDWR || device_open) {
        boundary_error = 1;
        errno = EINVAL;
        return -1;
    }
    device_open = 1;
    return 102;
}

static int fake_close(int fd)
{
    if (fd == 101 && lock_open) lock_open = 0;
    else if (fd == 102 && device_open) device_open = 0;
    else boundary_error = 1;
    return 0;
}

static uid_t fake_geteuid(void) { return 0; }

static int fake_lock(char *error, size_t cap)
{
    (void)error; (void)cap;
    if (lock_open) { boundary_error = 1; return -DLD_BUSY; }
    lock_open = 1;
    return 101;
}

int dld_hw_open(struct dld_hw *hw, int for_init, char *error, size_t cap)
{
    (void)error; (void)cap;
    if (for_init || !lock_open) boundary_error = 1;
    memset(hw, 0, sizeof *hw);
    hw->command = &fake_command;
    attach_calls++;
    return 0;
}

void dld_hw_close(struct dld_hw *hw)
{
    if (hw->command != &fake_command) boundary_error = 1;
    hw->command = NULL;
    detach_calls++;
}

void dld_hw_cleanup(struct dld_hw *hw)
{
    if (hw->command != &fake_command || !lock_open) boundary_error = 1;
    cleanup_calls++;
    /* Observable cleanup effect: a prior initialized session is invalidated. */
    fake_command.magic = 0;
}

static int fake_ioctl(int fd, unsigned long operation, ...)
{
    struct dld_quiet_send *request;
    va_list args;
    unsigned i;
    va_start(args, operation);
    request = va_arg(args, struct dld_quiet_send *);
    va_end(args);
    ioctl_calls++;
    if (fd != 102 || !device_open || !lock_open || operation != DLD_QUIET_IOCTL_SEND ||
        request->api_version != DLD_QUIET_API_VERSION || request->rgb != 0x123456 ||
        request->profile_id != fake_command.profile_id || request->previous_seq != 0 ||
        request->reserved[0] || request->reserved[1] || request->submitted || request->result)
        boundary_error = 1;
    for (i = 0; i < DLD_STRING_COUNT; i++)
        if (request->string_lengths[i]) boundary_error = 1;

    if (simulate_completed_frame) {
        fake_command.request_seq = 1;
        fake_command.completion_seq = 1;
        fake_command.accepted_seq = 1;
        fake_command.status = DLD_STATUS_DONE;
    }
    if (injected_errno) {
        /* EFAULT can occur at result copyout after the frame completed, so
         * deliberately leave the caller's output fields untouched here. */
        errno = injected_errno;
        return -1;
    }
    request->submitted = 1;
    request->request_seq = 1;
    request->completion_seq = 1;
    request->accepted_seq = 1;
    request->status = DLD_STATUS_DONE;
    request->stage = DLD_QUIET_STAGE_COMPLETE;
    return 0;
}

static int run_case(const char *name, int error_number, int completed,
                    int expected_exit, unsigned expected_cleanup)
{
    char *argv[] = { "dld-send", "123456", NULL };
    struct dld_command before;
    int code;
    memset(&fake_command, 0, sizeof fake_command);
    fake_command.magic = DLD_COMMAND_MAGIC;
    fake_command.abi_version = DLD_ABI_VERSION;
    fake_command.profile_id = DLD_PROFILE_WS2812B;
    fake_command.status = DLD_STATUS_READY;
    before = fake_command;
    injected_errno = error_number;
    simulate_completed_frame = completed;
    boundary_error = lock_open = device_open = 0;
    ioctl_calls = cleanup_calls = attach_calls = detach_calls = 0;
    dld_cancelled = 0;
    code = sender_main_under_test(2, argv);
    if (code != expected_exit || cleanup_calls != expected_cleanup || ioctl_calls != 1 ||
        attach_calls != 1 || detach_calls != 1 || lock_open || device_open || boundary_error) {
        fprintf(stderr, "FAIL %s: exit=%d cleanup=%u ioctl=%u attach/detach=%u/%u "
                "lock/device=%d/%d boundary_error=%d\n", name, code, cleanup_calls,
                ioctl_calls, attach_calls, detach_calls, lock_open, device_open, boundary_error);
        return 1;
    }
    if (!completed && !expected_cleanup && memcmp(&before, &fake_command, sizeof before)) {
        fprintf(stderr, "FAIL %s: definitive rejection changed retained session\n", name);
        return 1;
    }
    if (expected_cleanup && fake_command.magic != 0) {
        fprintf(stderr, "FAIL %s: uncertain result did not invalidate retained session\n", name);
        return 1;
    }
    if (completed && (fake_command.request_seq != 1 || fake_command.completion_seq != 1)) {
        fprintf(stderr, "FAIL %s: completed-frame scenario was not exercised\n", name);
        return 1;
    }
    printf("PASS %s: exit=%d cleanup=%u\n", name, code, cleanup_calls);
    return 0;
}

int main(void)
{
    int failures = 0;
    failures += run_case("ENOTTY before publication", ENOTTY, 0, DLD_PREREQUISITE, 0);
    failures += run_case("EPERM before publication", EPERM, 0, DLD_PREREQUISITE, 0);
    failures += run_case("EFAULT after completed frame", EFAULT, 1, DLD_CRITICAL, 1);
    failures += run_case("uncertain interrupted ioctl", EINTR, 0, DLD_CRITICAL, 1);
    failures += run_case("successful completed frame", 0, 1, DLD_OK, 0);
    if (failures) return 1;
    puts("PASS offline sender syscall handling: 5 cases; no hardware or lock files accessed");
    return 0;
}
