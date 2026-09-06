#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include "dld_hw.h"
#include "dld_quiet.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Explicit live test only. Never calls dld-send, init or hardware cleanup.
 * Parent holds the common flock throughout. The sole harness hardware write
 * is a graceful stop of an idle PRU0; the ioctl under test owns all cleanup.
 */
#define PRU0_CONTROL 0x22000U
#define PRU1_CONTROL 0x24000U
#define PRU_ENABLE 2U
#define PRU_RUNSTATE 0x8000U
#define PAGE_BYTES 4096U
#define SECOND_NS 1000000000ULL

static const unsigned long gpio_bases[3] = { 0x44e07000UL, 0x4804c000UL, 0x481ac000UL };
static const uint32_t gpio_masks[3] = { 1U << 26, (1U << 12) | (1U << 14),
                                      (1U << 1) | (1U << 3) | (1U << 4) };
static const unsigned gpio_clock_offsets[3] = { 0x408, 0xac, 0xb0 };
static FILE *journal;
static unsigned checks, rejection_cases;

struct observed {
    struct dld_command command;
    uint32_t control[2], oe[3], data[3];
};

struct child_result {
    int ioctl_result;
    int error_number;
    struct dld_quiet_send request;
};

static volatile uint32_t *reg_at(void *base, unsigned offset)
{
    return (volatile uint32_t *)((unsigned char *)base + offset);
}

static void barrier(void)
{
    __asm__ __volatile__("dsb sy" ::: "memory");
}

static uint64_t now_ns(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) return 0;
    return (uint64_t)value.tv_sec * SECOND_NS + (uint64_t)value.tv_nsec;
}

static void pause_us(long microseconds)
{
    struct timespec pause = { 0, microseconds * 1000L };
    if (nanosleep(&pause, NULL) && errno != EINTR) {
        perror("quiet-kernel-probe: nanosleep");
        _exit(3);
    }
}

static void emergency_alarm(int signal_number)
{
    static const char message[] = "quiet-kernel-probe: 5-second harness watchdog expired; state left for operator\n";
    (void)signal_number;
    (void)write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(124);
}

static int flush_journal(void)
{
    if (fflush(journal) || fsync(fileno(journal))) {
        perror("quiet-kernel-probe: flush artifact");
        return -1;
    }
    return 0;
}

static int check(int condition, const char *message)
{
    checks++;
    if (condition) return 0;
    fprintf(stderr, "quiet-kernel-probe: %s\n", message);
    fprintf(journal, "{\"event\":\"failed_check\",\"message\":\"%s\"}\n", message);
    (void)flush_journal();
    return -1;
}

static int observe(struct dld_hw *hw, void *clocks, void **gpio, struct observed *state)
{
    unsigned word, bank;
    uint32_t *destination = (uint32_t *)&state->command;
    volatile const uint32_t *source = (volatile const uint32_t *)hw->command;
    if ((*reg_at(clocks, 0xe8) & 0x30003U) != 2U)
        return check(0, "PRUSS clock is not functional; register read withheld");
    for (word = 0; word < DLD_COMMAND_BYTES / 4; word++) destination[word] = source[word];
    barrier();
    state->control[0] = *reg_at(hw->pruss_map, PRU0_CONTROL);
    state->control[1] = *reg_at(hw->pruss_map, PRU1_CONTROL);
    for (bank = 0; bank < 3; bank++) {
        if ((*reg_at(clocks, gpio_clock_offsets[bank]) & 0x30003U) != 2U)
            return check(0, "GPIO clock is not functional; register read withheld");
        state->oe[bank] = *reg_at(gpio[bank], 0x134);
        state->data[bank] = *reg_at(gpio[bank], 0x13c);
    }
    return 0;
}

static int record_state(const char *event, const struct observed *state)
{
    const struct dld_command *c = &state->command;
    unsigned i;
    fprintf(journal, "{\"event\":\"%s\",\"magic\":%" PRIu32 ",\"abi\":%" PRIu32
            ",\"profile\":%" PRIu32 ",\"lengths\":[", event, c->magic, c->abi_version, c->profile_id);
    for (i = 0; i < 6; i++) fprintf(journal, "%s%" PRIu32, i ? "," : "", c->string_lengths[i]);
    fprintf(journal, "],\"request\":%" PRIu32 ",\"completion\":%" PRIu32 ",\"status\":%" PRIu32
            ",\"accepted\":%" PRIu32 ",\"ready\":%" PRIu32 ",\"grant\":%" PRIu32
            ",\"done\":%" PRIu32 ",\"pru_control\":[%" PRIu32 ",%" PRIu32 "]",
            c->request_seq, c->completion_seq, c->status, c->accepted_seq, c->bank_ready,
            c->bank_grant, c->bank_done, state->control[0], state->control[1]);
    fprintf(journal, ",\"gpio_oe\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
            "],\"gpio_dataout\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]}\n",
            state->oe[0], state->oe[1], state->oe[2], state->data[0], state->data[1], state->data[2]);
    return flush_journal();
}

static void valid_request(const struct observed *state, struct dld_quiet_send *request)
{
    unsigned i;
    memset(request, 0, sizeof *request);
    request->api_version = DLD_QUIET_API_VERSION;
    request->profile_id = state->command.profile_id;
    request->previous_seq = state->command.request_seq;
    for (i = 0; i < 6; i++) request->string_lengths[i] = state->command.string_lengths[i];
}

static int rejection(int fd, const char *name, struct dld_quiet_send *request,
                     int expected, struct dld_hw *hw, void *clocks, void **gpio,
                     const struct observed *before, int unknown_command)
{
    struct observed after;
    int result, saved_errno;
    errno = 0;
    alarm(5);
    result = ioctl(fd, unknown_command ? _IO('d', 0x7f) : DLD_QUIET_IOCTL_SEND, request);
    saved_errno = errno;
    alarm(0);
    fprintf(journal, "{\"event\":\"rejection\",\"case\":\"%s\",\"ioctl\":%d,\"errno\":%d,"
            "\"result\":%d,\"submitted\":%" PRIu32 ",\"stage\":%" PRIu32 "}\n",
            name, result, saved_errno, (int)request->result, request->submitted, request->stage);
    if (flush_journal()) return -1;
    if (unknown_command) {
        if (check(result == -1 && saved_errno == ENOTTY, "unknown ioctl did not return ENOTTY")) return -1;
    } else if (check(result == 0 && request->result == expected && !request->submitted,
                     "invalid ioctl did not reject before submission with the expected error")) return -1;
    if (observe(hw, clocks, gpio, &after)) return -1;
    if (check(!memcmp(&before->command, &after.command, sizeof before->command),
              "rejected ioctl changed the mailbox")) return -1;
    if (check(!memcmp(before->control, after.control, sizeof before->control),
              "rejected ioctl changed PRU control") ||
        check(!memcmp(before->oe, after.oe, sizeof before->oe) &&
              !memcmp(before->data, after.data, sizeof before->data),
              "rejected ioctl changed GPIO state")) return -1;
    rejection_cases++;
    return 0;
}

static int preflight_suite(int fd, struct dld_hw *hw, void *clocks, void **gpio,
                          const struct observed *before)
{
    struct dld_quiet_send request;
    unsigned i;
    char name[48];
#define REJECT(name_, expected_) do { if (rejection(fd, (name_), &request, (expected_), hw, clocks, gpio, before, 0)) return -1; } while (0)
    valid_request(before, &request); request.api_version = 0; REJECT("invalid_api_zero", -EINVAL);
    valid_request(before, &request); request.api_version++; REJECT("invalid_api_future", -EINVAL);
    for (i = 0; i < 2; i++) {
        valid_request(before, &request); request.reserved[i] = 1;
        snprintf(name, sizeof name, "reserved_%u", i); REJECT(name, -EINVAL);
    }
    valid_request(before, &request); request.rgb = 0x1000000U; REJECT("rgb_out_of_range", -EINVAL);
    valid_request(before, &request); request.profile_id = 0; REJECT("profile_zero", -EINVAL);
    valid_request(before, &request); request.profile_id = DLD_PROFILE_WS2811_HS_BGR + 1; REJECT("profile_unknown", -EINVAL);
    for (i = 0; i < 6; i++) {
        valid_request(before, &request); request.string_lengths[i] = DLD_MAX_LENGTH + 1;
        snprintf(name, sizeof name, "length_out_of_range_%u", i); REJECT(name, -EINVAL);
    }
    valid_request(before, &request); request.profile_id = before->command.profile_id == 1 ? 2 : 1;
    REJECT("profile_config_mismatch", -EPROTO);
    valid_request(before, &request); request.string_lengths[0] = 1; REJECT("length_config_mismatch", -EPROTO);
    valid_request(before, &request); request.previous_seq++; REJECT("previous_sequence_mismatch", -EPROTO);
    valid_request(before, &request);
    if (rejection(fd, "unknown_ioctl", &request, -ENOTTY, hw, clocks, gpio, before, 1)) return -1;
#undef REJECT
    return 0;
}

static int stop_idle_pru0(struct dld_hw *hw)
{
    uint64_t begin = now_ns(), current;
    unsigned attempts;
    if (!begin) return check(0, "failed to read PRU-stop clock");
    *reg_at(hw->pruss_map, PRU0_CONTROL) = 1;
    barrier();
    for (attempts = 0; attempts < 400; attempts++) {
        current = now_ns();
        if (!current || current - begin >= 20000000ULL) break;
        if (!(*reg_at(hw->pruss_map, PRU0_CONTROL) & PRU_RUNSTATE)) {
            *reg_at(hw->pruss_map, PRU0_CONTROL) = 0;
            barrier();
            return check(!(*reg_at(hw->pruss_map, PRU0_CONTROL) & (PRU_ENABLE | PRU_RUNSTATE)),
                         "PRU0 stop/reset readback failed");
        }
        pause_us(50);
    }
    return check(0, "idle PRU0 did not stop in 20 ms; reset withheld");
}

static int wait_child(pid_t child, int *status, uint64_t limit_ns)
{
    uint64_t begin = now_ns();
    unsigned attempts;
    if (!begin) return -1;
    for (attempts = 0; attempts < 20000; attempts++) {
        uint64_t current;
        pid_t value = waitpid(child, status, WNOHANG);
        if (value == child) return 0;
        if (value < 0 && errno != EINTR) return -1;
        current = now_ns();
        if (!current || current - begin >= limit_ns) break;
        pause_us(100);
    }
    return -1;
}

static int killed_ioctl(int fd, struct dld_hw *hw, void *clocks, void **gpio,
                        const struct observed *before)
{
    struct dld_quiet_send request;
    struct child_result child_result;
    struct observed after;
    int pipe_fd[2], status = 0, result = -1, killed = 0, reaped = 0;
    pid_t child;
    uint64_t begin, published = 0, signal_time = 0, current;
    unsigned attempts, bank;
    uint32_t sequence = before->command.request_seq + 1U;
    ssize_t bytes;
    valid_request(before, &request);
    if (pipe(pipe_fd)) { perror("quiet-kernel-probe: child result pipe"); return -1; }
    if (fcntl(pipe_fd[0], F_SETFL, O_NONBLOCK) < 0) {
        perror("quiet-kernel-probe: nonblocking child pipe");
        close(pipe_fd[0]); close(pipe_fd[1]); return -1;
    }
    begin = now_ns();
    if (!begin) { close(pipe_fd[0]); close(pipe_fd[1]); return check(0, "failed to read overlap clock"); }
    child = fork();
    if (child < 0) { perror("quiet-kernel-probe: fork"); close(pipe_fd[0]); close(pipe_fd[1]); return -1; }
    if (!child) {
        close(pipe_fd[0]);
        memset(&child_result, 0, sizeof child_result);
        child_result.request = request;
        child_result.ioctl_result = ioctl(fd, DLD_QUIET_IOCTL_SEND, &child_result.request);
        child_result.error_number = errno;
        (void)write(pipe_fd[1], &child_result, sizeof child_result);
        _exit(90); /* Reaching userspace is incompatible with the kill proof. */
    }
    close(pipe_fd[1]);
    for (attempts = 0; attempts < 2000; attempts++) {
        current = now_ns();
        if (!current || current - begin >= 10000000ULL) break;
        if (hw->command->request_seq == sequence) {
            barrier();
            published = now_ns();
            if (hw->command->magic != DLD_COMMAND_MAGIC ||
                hw->command->completion_seq != before->command.completion_seq ||
                hw->command->status != DLD_STATUS_READY || hw->command->accepted_seq != 0)
                break;
            signal_time = now_ns();
            if (!signal_time || signal_time - begin >= 10000000ULL) break;
            if (kill(child, SIGKILL) == 0) killed = 1;
            /* Check after kill returns as well: descheduling between the
             * prior clock read and kill must not create a false overlap. */
            signal_time = now_ns();
            break;
        }
        if (waitpid(child, &status, WNOHANG) == child) { reaped = 1; break; }
        pause_us(50);
    }
    if (!killed || !signal_time || signal_time - begin >= 10000000ULL) {
        check(0, "could not establish SIGKILL after publication within 10 ms of fork; no overlap proof, no retry");
        goto done;
    }
    if (check(wait_child(child, &status, SECOND_NS) == 0, "killed ioctl child did not exit within 1 second")) goto done;
    reaped = 1;
    bytes = read(pipe_fd[0], &child_result, sizeof child_result);
    fprintf(journal, "{\"event\":\"killed_ioctl\",\"publication_ns_after_fork\":%" PRIu64
            ",\"kill_ns_after_fork\":%" PRIu64 ",\"wait_status\":%d,\"child_result_bytes\":%ld}\n",
            published - begin, signal_time - begin, status, (long)bytes);
    if (flush_journal()) goto done;
    if (check(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL, "ioctl child was not killed by SIGKILL") ||
        check(bytes == 0, "child returned to userspace before SIGKILL; overlap proof failed")) goto done;
    if (observe(hw, clocks, gpio, &after) || record_state("after_kernel_cleanup", &after)) goto done;
    if (check(after.command.request_seq == sequence && after.command.completion_seq == before->command.completion_seq,
              "stopped PRU request/completion evidence is inconsistent") ||
        check(after.command.magic == 0, "kernel did not invalidate failed mailbox after sender death") ||
        check(!(after.control[0] & (PRU_ENABLE | PRU_RUNSTATE)) &&
              !(after.control[1] & (PRU_ENABLE | PRU_RUNSTATE)), "kernel did not leave both PRUs stopped") ||
        check(after.command.profile_id == before->command.profile_id &&
              !memcmp(after.command.string_lengths, before->command.string_lengths, sizeof after.command.string_lengths),
              "kernel cleanup changed configuration")) goto done;
    for (bank = 0; bank < 3; bank++) {
        if (check(!(after.data[bank] & gpio_masks[bank]), "kernel cleanup did not leave every owned GPIO low") ||
            check(after.oe[bank] == before->oe[bank], "kernel cleanup changed GPIO directions") ||
            check(((after.data[bank] ^ before->data[bank]) & ~gpio_masks[bank]) == 0,
                  "kernel cleanup changed unrelated GPIO output bits")) goto done;
    }
    result = 0;
done:
    if (!reaped) {
        (void)kill(child, SIGKILL);
        if (wait_child(child, &status, SECOND_NS))
            fprintf(stderr, "quiet-kernel-probe: child remains unobservably active; operator recovery required\n");
    }
    close(pipe_fd[0]);
    return result;
}

int main(int argc, char **argv)
{
    struct dld_hw hw;
    struct observed before;
    struct sigaction action;
    char error[256], path[4096];
    void *clocks = NULL, *gpio[3] = { NULL, NULL, NULL };
    int lock_fd = -1, quiet_fd = -1, mem_fd = -1, log_fd, opened = 0, result = 1;
    unsigned bank;
    if (argc != 3 || strcmp(argv[1], "--run-live") || argv[2][0] != '/') {
        fprintf(stderr, "usage: quiet-kernel-probe --run-live /absolute/NEW_OUTPUT_DIR\n"
                "Requires all-zero lengths, valid READY and exclusive operator-owned DLD hardware.\n");
        return 2;
    }
    if (geteuid() != 0) { fprintf(stderr, "quiet-kernel-probe: root required\n"); return 3; }
    if (snprintf(path, sizeof path, "%s/events.jsonl", argv[2]) >= (int)sizeof path) return 2;
    if (mkdir(argv[2], 0700)) { perror("quiet-kernel-probe: create NEW artifact directory"); return 3; }
    log_fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (log_fd < 0 || !(journal = fdopen(log_fd, "w"))) {
        perror("quiet-kernel-probe: journal");
        if (log_fd >= 0) close(log_fd);
        return 3;
    }
    memset(&action, 0, sizeof action); action.sa_handler = emergency_alarm;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGALRM, &action, NULL)) { perror("quiet-kernel-probe: watchdog"); goto done; }
    lock_fd = open(DLD_LOCK_PATH, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB)) { perror("quiet-kernel-probe: shared lock"); goto done; }
    quiet_fd = open(DLD_QUIET_DEVICE, O_RDWR | O_CLOEXEC);
    mem_fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (quiet_fd < 0 || mem_fd < 0) { perror("quiet-kernel-probe: open devices"); goto done; }
    clocks = mmap(NULL, PAGE_BYTES, PROT_READ, MAP_SHARED, mem_fd, 0x44e00000);
    if (clocks == MAP_FAILED) { clocks = NULL; perror("quiet-kernel-probe: map clocks"); goto done; }
    for (bank = 0; bank < 3; bank++) {
        gpio[bank] = mmap(NULL, PAGE_BYTES, PROT_READ, MAP_SHARED, mem_fd, (off_t)gpio_bases[bank]);
        if (gpio[bank] == MAP_FAILED) { gpio[bank] = NULL; perror("quiet-kernel-probe: map GPIO"); goto done; }
    }
    if (dld_hw_open(&hw, 0, error, sizeof error)) { fprintf(stderr, "quiet-kernel-probe: %s\n", error); goto done; }
    opened = 1;
    if (observe(&hw, clocks, gpio, &before) || record_state("initial", &before)) goto done;
    if (check(before.command.magic == DLD_COMMAND_MAGIC && before.command.abi_version == DLD_ABI_VERSION &&
              before.command.status == DLD_STATUS_READY && before.command.error_detail == 0 &&
              before.command.request_seq == 0 && before.command.completion_seq == 0 &&
              before.command.accepted_seq == 0 && before.command.bank_ready == 0 &&
              before.command.bank_grant == 0 && before.command.bank_done == 0,
              "explicit fresh valid READY initialization is required") ||
        check(before.command.profile_id >= DLD_PROFILE_WS2812B && before.command.profile_id <= DLD_PROFILE_WS2811_HS_BGR,
              "unsupported initialized profile") ||
        check((before.control[0] & PRU_ENABLE) && !(before.control[1] & (PRU_ENABLE | PRU_RUNSTATE)),
              "initial PRU0 must be enabled and PRU1 stopped")) goto done;
    for (bank = 0; bank < 6; bank++)
        if (check(before.command.string_lengths[bank] == 0, "all six initialized lengths must be zero")) goto done;
    for (bank = 0; bank < 3; bank++)
        if (check(!(before.oe[bank] & gpio_masks[bank]) && !(before.data[bank] & gpio_masks[bank]),
                  "all six GPIOs must initially be outputs and low")) goto done;
    if (preflight_suite(quiet_fd, &hw, clocks, gpio, &before)) goto done;
    fprintf(journal, "{\"event\":\"rejections_passed\",\"cases\":%u}\n", rejection_cases);
    if (flush_journal()) goto done;
    if (stop_idle_pru0(&hw)) goto done;
    if (killed_ioctl(quiet_fd, &hw, clocks, gpio, &before)) goto done;
    result = 0;
done:
    alarm(0);
    fprintf(journal, "{\"event\":\"summary\",\"result\":\"%s\",\"checks\":%u,\"rejection_cases\":%u,"
            "\"automatic_init\":false,\"userspace_cleanup_called\":false}\n", result ? "FAIL" : "PASS", checks, rejection_cases);
    if (flush_journal()) result = 1;
    fclose(journal);
    if (opened) dld_hw_close(&hw); /* Mapping release only: no cleanup. */
    for (bank = 0; bank < 3; bank++) if (gpio[bank]) munmap(gpio[bank], PAGE_BYTES);
    if (clocks) munmap(clocks, PAGE_BYTES);
    if (mem_fd >= 0) close(mem_fd);
    if (quiet_fd >= 0) close(quiet_fd);
    if (lock_fd >= 0) close(lock_fd);
    printf("%s quiet kernel probe: %u checks, %u rejection cases; artifacts %s\n",
           result ? "FAIL" : "PASS", checks, rejection_cases, argv[2]);
    if (result) fprintf(stderr, "Hardware state left for explicit operator recovery; no retry or init performed.\n");
    return result;
}
