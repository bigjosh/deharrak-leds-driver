#define _POSIX_C_SOURCE 200809L
#include "dld_sender.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <unistd.h>

/* Compile both real front end and shared core against fake OS boundaries.
 * Real parsing, mailbox validation and result handling remain linked.
 * No hardware mappings, device files, command lock or UDP socket are opened.
 */
static struct dld_command fake_command;
static int injected_errno, injected_result, simulate_completed_frame;
static int signal_after_submit, wrong_completion, missing_submission, submitted_failure;
static int acquire_errno, unlock_errno, helper_errno, map_error, initial_lock_code;
static unsigned ioctl_calls, cleanup_calls, attach_calls, detach_calls, helper_opens, lock_opens;
static int lock_open, lock_held, device_open, boundary_error;
static uint32_t expected_rgb;
static uid_t effective_uid;

static int fake_open(const char *path, int flags, ...);
static int fake_close(int fd);
static uid_t fake_geteuid(void);
static int fake_ioctl(int fd, unsigned long operation, ...);
static int fake_lock(char *error, size_t cap);
static int fake_flock(int fd, int operation);

#define open fake_open
#define close fake_close
#define geteuid fake_geteuid
#define ioctl fake_ioctl
#define dld_lock fake_lock
#define flock fake_flock
#include "../src/dld_sender.c"
#undef open
#undef close
#undef geteuid
#undef ioctl
#undef dld_lock
#undef flock
#define main sender_main_under_test
#include "../src/dld_send.c"
#undef main

static int fake_open(const char *path, int flags, ...)
{
    helper_opens++;
    if (strcmp(path, DLD_QUIET_DEVICE) || (flags & O_ACCMODE) != O_RDWR ||
        device_open || !lock_held) boundary_error = 1;
    if (helper_errno) { errno = helper_errno; return -1; }
    device_open = 1;
    return 102;
}

static int fake_close(int fd)
{
    if (fd == 101 && lock_open) lock_open = lock_held = 0;
    else if (fd == 102 && device_open) device_open = 0;
    else boundary_error = 1;
    return 0;
}

static uid_t fake_geteuid(void) { return effective_uid; }

static int fake_lock(char *error, size_t cap)
{
    (void)error; (void)cap;
    lock_opens++;
    if (initial_lock_code) return -initial_lock_code;
    if (lock_open) boundary_error = 1;
    lock_open = lock_held = 1;
    return 101;
}

static int fake_flock(int fd, int operation)
{
    if (fd != 101 || !lock_open) boundary_error = 1;
    if (operation == LOCK_UN) {
        if (!lock_held) boundary_error = 1;
        if (unlock_errno) { errno = unlock_errno; return -1; }
        lock_held = 0;
    } else if (operation == (LOCK_EX | LOCK_NB)) {
        if (lock_held) boundary_error = 1;
        if (acquire_errno) { errno = acquire_errno; return -1; }
        lock_held = 1;
    } else boundary_error = 1;
    return 0;
}

int dld_hw_open(struct dld_hw *hw, int for_init, char *error, size_t cap)
{
    (void)error; (void)cap;
    if (for_init || !lock_held) boundary_error = 1;
    memset(hw, 0, sizeof(*hw));
    if (map_error) return -1;
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
    if (hw->command != &fake_command || !lock_held) boundary_error = 1;
    cleanup_calls++;
    fake_command.magic = 0;
}

static void complete_fake_frame(uint32_t seq)
{
    struct dld_config config;
    uint32_t masks[3], lengths[3], last = 0;
    unsigned i;
    config.profile_id = fake_command.profile_id;
    for (i = 0; i < DLD_STRING_COUNT; i++) config.string_lengths[i] = fake_command.string_lengths[i];
    dld_bank_info(&config, masks, lengths);
    for (i = 0; i < 3; i++) if (lengths[i]) last = i + 1;
    fake_command.request_seq = seq;
    fake_command.completion_seq = seq;
    fake_command.accepted_seq = seq;
    fake_command.bank_grant = fake_command.bank_done = last;
    fake_command.bank_ready = 0;
    fake_command.status = DLD_STATUS_DONE;
}

static int fake_ioctl(int fd, unsigned long operation, ...)
{
    struct dld_quiet_send *request;
    uint32_t seq = fake_command.request_seq + 1u;
    va_list args;
    unsigned i;
    va_start(args, operation);
    request = va_arg(args, struct dld_quiet_send *);
    va_end(args);
    ioctl_calls++;
    if (fd != 102 || !device_open || !lock_held || operation != DLD_QUIET_IOCTL_SEND ||
        request->api_version != DLD_QUIET_API_VERSION || request->rgb != expected_rgb ||
        request->profile_id != fake_command.profile_id || request->previous_seq != fake_command.request_seq ||
        request->reserved[0] || request->reserved[1] || request->submitted || request->result ||
        request->completion_seq || request->cleanup_failed_mask)
        boundary_error = 1;
    for (i = 0; i < DLD_STRING_COUNT; i++)
        if (request->string_lengths[i] != fake_command.string_lengths[i]) boundary_error = 1;

    if (simulate_completed_frame) complete_fake_frame(seq);
    if (injected_errno) { errno = injected_errno; return -1; }
    if (injected_result) { request->result = injected_result; return 0; }
    complete_fake_frame(seq);
    request->submitted = missing_submission ? 0 : 1;
    request->result = submitted_failure ? -ETIMEDOUT : 0;
    request->request_seq = seq;
    request->completion_seq = wrong_completion ? seq + 1u : seq;
    request->accepted_seq = seq;
    request->status = DLD_STATUS_DONE;
    request->stage = DLD_QUIET_STAGE_COMPLETE;
    if (signal_after_submit) dld_cancelled = SIGTERM;
    return 0;
}

static int failures;
static unsigned checks;
#define CHECK(test) do { checks++; if (!(test)) { failures++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); } } while (0)

static void reset_fake(void)
{
    memset(&fake_command, 0, sizeof(fake_command));
    fake_command.magic = DLD_COMMAND_MAGIC;
    fake_command.abi_version = DLD_ABI_VERSION;
    fake_command.profile_id = DLD_PROFILE_WS2812B;
    fake_command.status = DLD_STATUS_READY;
    injected_errno = injected_result = simulate_completed_frame = 0;
    signal_after_submit = wrong_completion = missing_submission = submitted_failure = 0;
    acquire_errno = unlock_errno = helper_errno = map_error = initial_lock_code = 0;
    ioctl_calls = cleanup_calls = attach_calls = detach_calls = helper_opens = lock_opens = 0;
    lock_open = lock_held = device_open = boundary_error = 0;
    effective_uid = 0;
    expected_rgb = 0x123456;
    dld_cancelled = 0;
}

static void closed_cleanly(void)
{
    CHECK(!lock_open && !lock_held && !device_open && !boundary_error);
    CHECK(attach_calls == detach_calls);
}

static void cli_case(const char *name, int error_number, int completed,
                     int expected_exit, unsigned expected_cleanup)
{
    char *argv[] = { "dld-send", "123456", NULL };
    struct dld_command before;
    reset_fake();
    before = fake_command;
    injected_errno = error_number;
    simulate_completed_frame = completed;
    CHECK(sender_main_under_test(2, argv) == expected_exit);
    CHECK(cleanup_calls == expected_cleanup && ioctl_calls == 1);
    CHECK(attach_calls == 1 && helper_opens == 1 && lock_opens == 1);
    closed_cleanly();
    if (!completed && !expected_cleanup && error_number)
        CHECK(!memcmp(&before, &fake_command, sizeof(before)));
    if (expected_cleanup) CHECK(fake_command.magic == 0);
    if (completed) CHECK(fake_command.request_seq == 1 && fake_command.completion_seq == 1);
    printf("PASS CLI syscall case: %s\n", name);
}

static void resident_cases(void)
{
    struct dld_sender sender;
    struct dld_send_result result;
    struct dld_command before;
    char error[512];
    unsigned i, before_calls;
    reset_fake();
    CHECK(dld_sender_open(&sender, error, sizeof(error)) == DLD_OK);
    CHECK(lock_open && device_open && !lock_held && ioctl_calls == 0);
    for (i = 0; i < 100; i++) {
        expected_rgb = i * 10101u;
        CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_OK);
        CHECK(!lock_held && !boundary_error && result.quiet.request_seq == i + 1);
    }
    CHECK(attach_calls == 1 && helper_opens == 1 && lock_opens == 1 && !detach_calls);
    /* Another cooperating initializer changes profile and lengths while idle. */
    fake_command.profile_id = DLD_PROFILE_WS2811_HS_BGR;
    fake_command.string_lengths[0] = 300;
    fake_command.string_lengths[3] = 7;
    fake_command.status = DLD_STATUS_READY;
    fake_command.request_seq = fake_command.completion_seq = fake_command.accepted_seq = 0;
    fake_command.bank_grant = fake_command.bank_done = 0;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_OK);
    CHECK(result.config.profile_id == DLD_PROFILE_WS2811_HS_BGR &&
          result.config.string_lengths[0] == 300 && result.config.string_lengths[3] == 7);
    /* Intervening CLI sequence advancement and uint32 wrap must be refreshed. */
    complete_fake_frame(UINT32_MAX);
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_OK);
    CHECK(result.quiet.request_seq == 0 && !boundary_error);
    before = fake_command;
    before_calls = ioctl_calls;
    acquire_errno = EWOULDBLOCK;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_BUSY);
    CHECK(ioctl_calls == before_calls && !cleanup_calls && device_open && !lock_held);
    CHECK(!memcmp(&before, &fake_command, sizeof(before)));
    acquire_errno = 0;
    fake_command.status = DLD_STATUS_WAIT_BANK;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_BUSY);
    CHECK(!lock_held && ioctl_calls == before_calls && !cleanup_calls);
    fake_command.status = DLD_STATUS_DONE;
    fake_command.magic = 0;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_NOT_INITIALIZED);
    CHECK(!lock_held && ioctl_calls == before_calls && !cleanup_calls);
    fake_command.magic = DLD_COMMAND_MAGIC;
    dld_cancelled = SIGTERM;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_PREREQUISITE);
    CHECK(!lock_held && ioctl_calls == before_calls && !cleanup_calls);
    dld_cancelled = 0;
    CHECK(dld_sender_send(&sender, 0x1000000U, &result, error, sizeof(error)) == DLD_BAD_ARGUMENT);
    CHECK(ioctl_calls == before_calls);
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_OK);
    dld_sender_close(&sender);
    dld_sender_close(&sender);
    closed_cleanly();
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_NOT_INITIALIZED);
}

static void failure_cases(void)
{
    struct dld_sender sender;
    struct dld_send_result result;
    char error[512];
    const int rejections[] = { -EBUSY, -EPROTO, -EAGAIN };
    const int expected[] = { DLD_BUSY, DLD_NOT_INITIALIZED, DLD_PREREQUISITE };
    unsigned i, calls;
    for (i = 0; i < sizeof(rejections)/sizeof(rejections[0]); i++) {
        reset_fake();
        CHECK(dld_sender_open(&sender, error, sizeof(error)) == DLD_OK);
        injected_result = rejections[i];
        CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == expected[i]);
        CHECK(!cleanup_calls && fake_command.magic == DLD_COMMAND_MAGIC && !lock_held);
        dld_sender_close(&sender);
        closed_cleanly();
    }
    for (i = 0; i < 5; i++) {
        reset_fake();
        CHECK(dld_sender_open(&sender, error, sizeof(error)) == DLD_OK);
        if (i == 0) { injected_errno = EFAULT; simulate_completed_frame = 1; }
        if (i == 1) wrong_completion = 1;
        if (i == 2) missing_submission = 1;
        if (i == 3) signal_after_submit = 1;
        if (i == 4) submitted_failure = 1;
        CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_CRITICAL);
        CHECK(cleanup_calls == 1 && !lock_held && fake_command.magic == 0 && !boundary_error);
        calls = ioctl_calls;
        CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_CRITICAL);
        CHECK(ioctl_calls == calls && cleanup_calls == 1);
        dld_sender_close(&sender);
        closed_cleanly();
    }
    for (i = 0; i < 5; i++) {
        reset_fake();
        if (i == 0) initial_lock_code = DLD_BUSY;
        if (i == 1) effective_uid = 1000;
        if (i == 2) map_error = 1;
        if (i == 3) fake_command.magic = 0;
        if (i == 4) helper_errno = ENOENT;
        CHECK(dld_sender_open(&sender, error, sizeof(error)) != DLD_OK);
        dld_sender_close(&sender);
        CHECK(!ioctl_calls && !cleanup_calls);
        closed_cleanly();
    }
    reset_fake();
    CHECK(dld_sender_open(&sender, error, sizeof(error)) == DLD_OK);
    unlock_errno = EBADF;
    CHECK(dld_sender_send(&sender, expected_rgb, &result, error, sizeof(error)) == DLD_PREREQUISITE);
    CHECK(!lock_open && !lock_held && sender.faulted && !cleanup_calls);
    dld_sender_close(&sender);
    closed_cleanly();
}

int main(void)
{
    cli_case("ENOTTY before publication", ENOTTY, 0, DLD_PREREQUISITE, 0);
    cli_case("EPERM before publication", EPERM, 0, DLD_PREREQUISITE, 0);
    cli_case("EFAULT after completed frame", EFAULT, 1, DLD_CRITICAL, 1);
    cli_case("uncertain interrupted ioctl", EINTR, 0, DLD_CRITICAL, 1);
    cli_case("successful completed frame", 0, 1, DLD_OK, 0);
    resident_cases();
    failure_cases();
    if (failures) return 1;
    printf("PASS shared sender and CLI: %u checks; retained resources, config refresh, serialization, recovery; no hardware accessed\n", checks);
    return 0;
}
