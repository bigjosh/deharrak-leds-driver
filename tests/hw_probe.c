#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include "dld_hw.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

/* Test-only access. This executable is never called by either product CLI.
 * Mutations are limited to PRU0 CONTROL and the owned mailbox request/status.
 * GPIO is mapped read-only and no loader/setup/cleanup functions are called.
 */
#define PRU0_CONTROL 0x22000u
#define PRU1_CONTROL 0x24000u
#define PRU_ENABLE 2u
#define GPIO_OE 0x134u
#define GPIO_DATAOUT 0x13cu
#define GPIO_MAP_BYTES 4096u

static const unsigned long gpio_bases[3] = {
    0x44e07000UL, 0x4804c000UL, 0x481ac000UL
};
static const uint32_t gpio_masks[3] = {
    1u << 26, (1u << 12) | (1u << 14),
    (1u << 1) | (1u << 3) | (1u << 4)
};

static volatile uint32_t *reg_at(void *base, unsigned offset)
{
    return (volatile uint32_t *)((unsigned char *)base + offset);
}

static void barrier(void)
{
    __asm__ __volatile__("dsb sy" ::: "memory");
}

static void snapshot_command(volatile const struct dld_command *source,
                              struct dld_command *target)
{
    unsigned pin;
    target->status = source->status;
    barrier();
    target->magic = source->magic;
    target->abi_version = source->abi_version;
    target->profile_id = source->profile_id;
    for (pin = 0; pin < DLD_STRING_COUNT; ++pin)
        target->string_lengths[pin] = source->string_lengths[pin];
    target->wire_color = source->wire_color;
    target->request_seq = source->request_seq;
    target->completion_seq = source->completion_seq;
    target->error_detail = source->error_detail;
    target->bank_ready = source->bank_ready;
    target->bank_grant = source->bank_grant;
    target->bank_done = source->bank_done;
    target->accepted_seq = source->accepted_seq;
}

static int valid_idle(const struct dld_command *command)
{
    unsigned pin;
    if (command->magic != DLD_COMMAND_MAGIC || command->abi_version != DLD_ABI_VERSION ||
        (command->profile_id != DLD_PROFILE_WS2812B && command->profile_id != DLD_PROFILE_WS2811_HS &&
         command->profile_id != DLD_PROFILE_WS2812B_BGR && command->profile_id != DLD_PROFILE_WS2811_HS_BGR) ||
        command->error_detail != DLD_ERROR_NONE ||
        (command->status != DLD_STATUS_READY && command->status != DLD_STATUS_DONE) ||
        command->request_seq != command->completion_seq || command->bank_ready != 0 ||
        command->accepted_seq != command->completion_seq ||
        command->bank_grant != command->bank_done || command->bank_done > DLD_BANK_GPIO0)
        return 0;
    for (pin = 0; pin < DLD_STRING_COUNT; ++pin)
        if (command->string_lengths[pin] > DLD_MAX_LENGTH) return 0;
    return 1;
}

static int withhold_first_grant(struct dld_hw *hw, const struct dld_command *state)
{
    struct timespec deadline, now;
    const struct timespec pause = { 0, 1000000 };
    uint32_t first = 0, sequence = state->request_seq + 1u;
    if (state->string_lengths[0] || state->string_lengths[1] || state->string_lengths[5])
        first = DLD_BANK_GPIO2;
    else if (state->string_lengths[2] || state->string_lengths[4])
        first = DLD_BANK_GPIO1;
    else if (state->string_lengths[3]) first = DLD_BANK_GPIO0;
    if (!first) {
        fprintf(stderr, "hw-probe: withholding a grant requires an enabled string\n");
        return -1;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) {
        perror("hw-probe: grant deadline");
        return -1;
    }
    deadline.tv_nsec += 100000000;
    if (deadline.tv_nsec >= 1000000000) { deadline.tv_sec++; deadline.tv_nsec -= 1000000000; }
    hw->command->bank_grant = 0;
    hw->command->wire_color = 0;
    barrier();
    hw->command->request_seq = sequence;
    barrier();
    for (;;) {
        struct dld_command current;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            perror("hw-probe: grant deadline clock");
            return -1;
        }
        if (now.tv_sec > deadline.tv_sec ||
            (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec)) {
            fprintf(stderr, "hw-probe: PRU did not reach the withheld gate within 100 ms\n");
            return -1;
        }
        snapshot_command(hw->command, &current);
        if (current.status == DLD_STATUS_ERROR || current.error_detail) {
            fprintf(stderr, "hw-probe: PRU rejected the withheld request (error %" PRIu32 ")\n", current.error_detail);
            return -1;
        }
        if (current.status == DLD_STATUS_WAIT_BANK && current.accepted_seq == sequence &&
            current.bank_ready == first && current.bank_grant == 0 && current.bank_done == 0 &&
            current.completion_seq == state->completion_seq) return 0;
        if (nanosleep(&pause, NULL) != 0 && errno != EINTR) {
            perror("hw-probe: withheld-gate sleep");
            return -1;
        }
    }
}

static int print_snapshot(struct dld_hw *hw)
{
    struct dld_command command;
    uint32_t control[2], oe[3], dataout[3];
    int fd = -1, result = 1;
    void *maps[3] = { NULL, NULL, NULL };
    unsigned bank, pin;
    fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) { perror("hw-probe: open GPIO read-only mapping"); goto done; }
    for (bank = 0; bank < 3; ++bank) {
        maps[bank] = mmap(NULL, GPIO_MAP_BYTES, PROT_READ, MAP_SHARED,
                          fd, (off_t)gpio_bases[bank]);
        if (maps[bank] == MAP_FAILED) {
            maps[bank] = NULL;
            perror("hw-probe: map GPIO read-only");
            goto done;
        }
    }
    snapshot_command(hw->command, &command);
    control[0] = *reg_at(hw->pruss_map, PRU0_CONTROL);
    control[1] = *reg_at(hw->pruss_map, PRU1_CONTROL);
    for (bank = 0; bank < 3; ++bank) {
        oe[bank] = *reg_at(maps[bank], GPIO_OE);
        dataout[bank] = *reg_at(maps[bank], GPIO_DATAOUT);
    }
    printf("{\"magic\":%" PRIu32 ",\"abi_version\":%" PRIu32
           ",\"profile_id\":%" PRIu32 ",\"string_lengths\":[",
           command.magic, command.abi_version, command.profile_id);
    for (pin = 0; pin < DLD_STRING_COUNT; ++pin)
        printf("%s%" PRIu32, pin ? "," : "", command.string_lengths[pin]);
    printf("],\"wire_color\":%" PRIu32 ",\"request_seq\":%" PRIu32
           ",\"completion_seq\":%" PRIu32 ",\"status\":%" PRIu32
           ",\"error_detail\":%" PRIu32 ",\"pru_control\":[%" PRIu32 ",%" PRIu32 "]",
           command.wire_color, command.request_seq, command.completion_seq,
           command.status, command.error_detail, control[0], control[1]);
    printf(",\"bank_ready\":%" PRIu32 ",\"bank_grant\":%" PRIu32
           ",\"bank_done\":%" PRIu32 ",\"accepted_seq\":%" PRIu32,
           command.bank_ready, command.bank_grant, command.bank_done, command.accepted_seq);
    printf(",\"gpio_oe\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]",
           oe[0], oe[1], oe[2]);
    printf(",\"gpio_dataout\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]",
           dataout[0], dataout[1], dataout[2]);
    printf(",\"gpio_led_masks\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]}\n",
           gpio_masks[0], gpio_masks[1], gpio_masks[2]);
    result = 0;
done:
    for (bank = 0; bank < 3; ++bank)
        if (maps[bank]) munmap(maps[bank], GPIO_MAP_BYTES);
    if (fd >= 0) close(fd);
    return result;
}

int main(int argc, char **argv)
{
    const char *action;
    struct dld_hw hw;
    struct dld_command command;
    char error[256];
    int lock_fd, result = 1, mutation;
    uint32_t enabled;
    if (argc == 2 && strcmp(argv[1], "--lock-path") == 0) {
        puts(DLD_LOCK_PATH);
        return 0;
    }
    mutation = argc == 3 && strcmp(argv[1], "--run-live") == 0;
    action = mutation ? argv[2] : (argc == 2 ? argv[1] : "");
    if ((!mutation && strcmp(action, "snapshot") != 0) ||
        (mutation && strcmp(action, "stop-pru0") != 0 &&
         strcmp(action, "inject-outstanding") != 0 && strcmp(action, "inject-running") != 0 &&
         strcmp(action, "withhold-first-grant") != 0)) {
        fprintf(stderr, "usage: hw-probe --lock-path | snapshot | --run-live "
                "{stop-pru0|inject-outstanding|inject-running|withhold-first-grant}\n");
        return 2;
    }
    if (geteuid() != 0) { fprintf(stderr, "hw-probe: root is required\n"); return 3; }
    /* The test build must give this probe and both CLIs the same new-project
     * lock path. Creating that lock is the only non-device write by snapshot.
     */
    lock_fd = open(DLD_LOCK_PATH, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lock_fd < 0) { perror("hw-probe: open command lock"); return 3; }
    if (flock(lock_fd, LOCK_EX | LOCK_NB) < 0) {
        perror("hw-probe: acquire command lock");
        close(lock_fd);
        return 6;
    }
    if (dld_hw_open(&hw, 0, error, sizeof(error)) < 0) {
        fprintf(stderr, "hw-probe: %s\n", error);
        close(lock_fd);
        return 3;
    }
    if (mutation) {
        snapshot_command(hw.command, &command);
        enabled = *reg_at(hw.pruss_map, PRU0_CONTROL) & PRU_ENABLE;
        if (!valid_idle(&command) || (*reg_at(hw.pruss_map, PRU1_CONTROL) & PRU_ENABLE)) {
            fprintf(stderr, "hw-probe: fault injection requires valid idle DLD state and disabled PRU1\n");
            goto done;
        }
        if (strcmp(action, "stop-pru0") == 0) {
            if (!enabled) { fprintf(stderr, "hw-probe: PRU0 is already disabled\n"); goto done; }
            *reg_at(hw.pruss_map, PRU0_CONTROL) = 0;
            barrier();
            if (*reg_at(hw.pruss_map, PRU0_CONTROL) & PRU_ENABLE) {
                fprintf(stderr, "hw-probe: PRU0 did not disable\n");
                goto done;
            }
        } else if (strcmp(action, "withhold-first-grant") == 0) {
            if (!enabled) { fprintf(stderr, "hw-probe: withholding a grant requires enabled PRU0\n"); goto done; }
            if (withhold_first_grant(&hw, &command) != 0) goto done;
        } else {
            if (enabled) { fprintf(stderr, "hw-probe: stop PRU0 before editing the mailbox\n"); goto done; }
            hw.command->request_seq = command.request_seq + 1u;
            barrier();
            if (strcmp(action, "inject-running") == 0) {
                hw.command->status = DLD_STATUS_RUNNING;
                barrier();
            }
        }
    }
    result = print_snapshot(&hw);
done:
    /* Intentionally leave injected state intact for the command under test.
     * Closing a probe never clears pins, invalidates RAM, or starts a PRU.
     */
    dld_hw_close(&hw);
    close(lock_fd);
    return result;
}
