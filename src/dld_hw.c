#define _POSIX_C_SOURCE 200809L
#include "dld_hw.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

/* AM335x-only mapping/loader. See docs/hardware.md for TI/Linux references.
 * Unlike the legacy prussdrv attachment path, only UIO map0 is mapped.
 * Mapping and releasing resources never implicitly reset either PRU.
 */
#define PRUSS_BASE 0x4a300000UL
#define PRU0_CONTROL 0x22000
#define PRU1_CONTROL 0x24000
#define PRU_RUNSTATE (1U << 15)
#define PRU_STOP_TIMEOUT_NS 20000000ULL
#define PRU0_IRAM 0x34000
#define INTC_BASE 0x20000
#define INTC_SICR 0x024
#define INTC_EICR 0x02c
#define INTC_HIDISR 0x038
#define PRU0_ARM_EVENT 19
#define PRU1_ARM_EVENT 20
#define GPIO_OE 0x134
#define GPIO_CTRL 0x130
#define GPIO_DATAOUT 0x13c
#define GPIO_CLEAR 0x190
#define MAP_BYTES 4096

static const unsigned long gpio_bases[3] = {
    0x44e07000UL, 0x4804c000UL, 0x481ac000UL
};
static const uint32_t gpio_masks[3] = {
    (1U << 26), (1U << 12) | (1U << 14),
    (1U << 1) | (1U << 3) | (1U << 4)
};
/* CM_WKUP_GPIO0_CLKCTRL, CM_PER_GPIO1_CLKCTRL, CM_PER_GPIO2_CLKCTRL. */
static const unsigned clock_offsets[3] = { 0x408, 0xac, 0xb0 };

static void barrier(void)
{
    __asm__ __volatile__("dsb sy" ::: "memory");
}

static volatile uint32_t *reg_at(void *map, unsigned offset)
{
    return (volatile uint32_t *)((unsigned char *)map + offset);
}

static int fail(char *error, size_t cap, const char *what)
{
    if (cap) snprintf(error, cap, "%s: %s", what, strerror(errno));
    return -1;
}

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

static int stop_prus(struct dld_hw *hw, unsigned mask, char *error, size_t cap)
{
    static const unsigned control_offsets[2] = { PRU0_CONTROL, PRU1_CONTROL };
    const struct timespec pause = { 0, 1000000 };
    uint64_t begin = monotonic_ns();
    unsigned pru, pending = mask, attempts = 0;
    /* EN=0 lets a multicycle instruction finish. Do not assert SOFT_RST_N
     * until RUNSTATE proves the core has stopped (AM335x TRM table 4-41).
     * Issue both disables before waiting so PRU1 cannot keep executing while
     * a stuck PRU0 consumes the shared stop deadline.
     */
    for (pru = 0; pru < 2; pru++) {
        if (mask & (1U << pru))
            *reg_at(hw->pruss_map, control_offsets[pru]) = 1;
    }
    barrier();
    while (pending) {
        uint64_t now;
        for (pru = 0; pru < 2; pru++) {
            volatile uint32_t *control;
            if (!(pending & (1U << pru))) continue;
            control = reg_at(hw->pruss_map, control_offsets[pru]);
            if (!(*control & PRU_RUNSTATE)) {
                *control = 0;
                pending &= ~(1U << pru);
            }
        }
        barrier();
        if (!pending) return 0;
        now = monotonic_ns();
        if (!begin || !now) return fail(error, cap, "read PRU-stop deadline");
        if (now - begin >= PRU_STOP_TIMEOUT_NS || ++attempts > 100) {
            errno = ETIMEDOUT;
            if (cap) snprintf(error, cap, "%s did not stop within 20 ms; reset withheld",
                              pending == 3 ? "PRU0 and PRU1" :
                              (pending & 1) ? "PRU0" : "PRU1");
            return -1;
        }
        if (nanosleep(&pause, NULL) < 0 && errno != EINTR)
            return fail(error, cap, "wait for PRU stop");
    }
    return 0;
}

static int read_number(const char *path, unsigned long *value)
{
    char buffer[64], *end;
    FILE *stream = fopen(path, "r");
    if (!stream) return -1;
    if (!fgets(buffer, sizeof buffer, stream)) {
        fclose(stream);
        errno = EINVAL;
        return -1;
    }
    fclose(stream);
    errno = 0;
    *value = strtoul(buffer, &end, 0);
    if (errno || end == buffer) return -1;
    while (*end == ' ' || *end == '\n' || *end == '\r' || *end == '\t') end++;
    if (*end) { errno = EINVAL; return -1; }
    return 0;
}

int dld_hw_open(struct dld_hw *hw, int for_init, char *error, size_t cap)
{
    DIR *dir;
    struct dirent *entry;
    char path[256], device[128];
    unsigned long address, size, offset;
    int found = 0;
    (void)for_init;
    memset(hw, 0, sizeof *hw);
    hw->uio_fd = hw->mem_fd = -1;
    dir = opendir("/sys/class/uio");
    if (!dir) return fail(error, cap, "open UIO inventory (uio_pruss required)");
    while ((entry = readdir(dir)) != NULL) {
        const char *suffix = entry->d_name + 3;
        if (strncmp(entry->d_name, "uio", 3)) continue;
        if (!*suffix || strspn(suffix, "0123456789") != strlen(suffix)) continue;
        snprintf(path, sizeof path, "/sys/class/uio/%s/maps/map0/addr", entry->d_name);
        if (read_number(path, &address) || address != PRUSS_BASE) continue;
        snprintf(path, sizeof path, "/sys/class/uio/%s/maps/map0/size", entry->d_name);
        if (read_number(path, &size)) continue;
        snprintf(path, sizeof path, "/sys/class/uio/%s/maps/map0/offset", entry->d_name);
        if (read_number(path, &offset)) continue;
        if (offset || size < PRU0_IRAM + DLD_PRU_IRAM_BYTES || size > 0x100000UL) continue;
        snprintf(device, sizeof device, "/dev/%s", entry->d_name);
        hw->pruss_size = (size_t)size;
        found = 1;
        break;
    }
    closedir(dir);
    if (!found) { errno = ENODEV; return fail(error, cap, "AM335x PRUSS UIO map0 unavailable"); }
    hw->uio_fd = open(device, O_RDWR | O_SYNC | O_CLOEXEC);
    if (hw->uio_fd < 0) return fail(error, cap, "open PRUSS UIO device");
    hw->pruss_map = mmap(NULL, hw->pruss_size, PROT_READ | PROT_WRITE,
                         MAP_SHARED, hw->uio_fd, 0);
    if (hw->pruss_map == MAP_FAILED) {
        hw->pruss_map = NULL;
        fail(error, cap, "map PRUSS UIO memory");
        dld_hw_close(hw);
        return -1;
    }
    hw->command = (volatile struct dld_command *)hw->pruss_map;
    return 0;
}

void dld_hw_close(struct dld_hw *hw)
{
    unsigned bank;
    for (bank = 0; bank < 3; bank++) {
        if (hw->gpio_map[bank]) munmap(hw->gpio_map[bank], MAP_BYTES);
    }
    if (hw->clock_map) munmap(hw->clock_map, MAP_BYTES);
    if (hw->pruss_map) munmap(hw->pruss_map, hw->pruss_size);
    if (hw->uio_fd >= 0) close(hw->uio_fd);
    if (hw->mem_fd >= 0) close(hw->mem_fd);
    memset(hw, 0, sizeof *hw);
    hw->uio_fd = hw->mem_fd = -1;
}

static void disable_notifications(struct dld_hw *hw)
{
    /* Disable only the conventional PRU0/PRU1 ARM notification routes.
     * No INTC mapping reset, event wait, or re-enable is performed. */
    *reg_at(hw->pruss_map, INTC_BASE + INTC_EICR) = PRU0_ARM_EVENT;
    *reg_at(hw->pruss_map, INTC_BASE + INTC_EICR) = PRU1_ARM_EVENT;
    *reg_at(hw->pruss_map, INTC_BASE + INTC_HIDISR) = 2;
    *reg_at(hw->pruss_map, INTC_BASE + INTC_HIDISR) = 3;
    *reg_at(hw->pruss_map, INTC_BASE + INTC_SICR) = PRU0_ARM_EVENT;
    *reg_at(hw->pruss_map, INTC_BASE + INTC_SICR) = PRU1_ARM_EVENT;
    barrier();
}

int dld_hw_take_control(struct dld_hw *hw, char *error, size_t cap)
{
    int result;
    if (!hw->command) { errno = EINVAL; return fail(error, cap, "PRUSS not mapped"); }
    hw->control_taken = 1;
    result = stop_prus(hw, 3, error, cap);
    disable_notifications(hw);
    hw->command->magic = 0;
    barrier();
    return result;
}

static int map_peripherals(struct dld_hw *hw, char *error, size_t cap)
{
    if (hw->mem_fd < 0) {
        hw->mem_fd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
        if (hw->mem_fd < 0) return fail(error, cap, "open /dev/mem for GPIO setup");
    }
    if (!hw->clock_map) {
        hw->clock_map = mmap(NULL, MAP_BYTES, PROT_READ | PROT_WRITE,
                             MAP_SHARED, hw->mem_fd, 0x44e00000);
        if (hw->clock_map == MAP_FAILED) {
            hw->clock_map = NULL;
            return fail(error, cap, "map GPIO clock controls");
        }
    }
    return 0;
}

static int enable_clock(struct dld_hw *hw, unsigned bank, char *error, size_t cap)
{
    volatile uint32_t *clock = reg_at(hw->clock_map, clock_offsets[bank]);
    uint64_t begin = monotonic_ns();
    unsigned attempts = 0;
    struct timespec pause = { 0, 1000000 };
    if (!begin) return fail(error, cap, "read clock-setup deadline");
    *clock = (*clock & ~3U) | 2U;
    barrier();
    while ((*clock & 0x30003U) != 2U) {
        uint64_t now = monotonic_ns();
        if (!now || now - begin >= 20000000ULL || ++attempts > 100) {
            if (cap) snprintf(error, cap, "GPIO%u clock did not become functional", bank);
            return -1;
        }
        nanosleep(&pause, NULL);
    }
    return 0;
}

static int configure_bank(struct dld_hw *hw, unsigned bank, char *error, size_t cap)
{
    unsigned retry;
    if (!hw->gpio_map[bank]) {
        hw->gpio_map[bank] = mmap(NULL, MAP_BYTES, PROT_READ | PROT_WRITE,
                                 MAP_SHARED, hw->mem_fd, (off_t)gpio_bases[bank]);
        if (hw->gpio_map[bank] == MAP_FAILED) {
            hw->gpio_map[bank] = NULL;
            return fail(error, cap, "map LED GPIO bank");
        }
    }
    {
        volatile uint32_t *ctrl, *oe, *data, *clear;
        if (enable_clock(hw, bank, error, cap)) return -1;
        ctrl = reg_at(hw->gpio_map[bank], GPIO_CTRL);
        oe = reg_at(hw->gpio_map[bank], GPIO_OE);
        data = reg_at(hw->gpio_map[bank], GPIO_DATAOUT);
        clear = reg_at(hw->gpio_map[bank], GPIO_CLEAR);
        *ctrl = *ctrl & ~1U;
        /* Clear and drain BEFORE changing OE: never expose a stale high. */
        for (retry = 0; retry < 8; retry++) {
            *clear = gpio_masks[bank];
            barrier();
            if (!(*data & gpio_masks[bank])) break;
        }
        if (retry == 8) {
            if (cap) snprintf(error, cap, "GPIO%u LED output latches failed to clear", bank);
            return -1;
        }
        *oe = *oe & ~gpio_masks[bank];
        barrier();
        if (*oe & gpio_masks[bank]) {
            if (cap) snprintf(error, cap, "GPIO%u LED directions remain inputs", bank);
            return -1;
        }
    }
    return 0;
}

int dld_hw_configure_outputs(struct dld_hw *hw, char *error, size_t cap)
{
    unsigned bank;
    if (map_peripherals(hw, error, cap)) return -1;
    for (bank = 0; bank < 3; bank++) {
        if (configure_bank(hw, bank, error, cap)) return -1;
    }
    return 0;
}

int dld_hw_start(struct dld_hw *hw, const uint32_t *code, size_t bytes,
                 char *error, size_t cap)
{
    size_t word;
    volatile uint32_t *iram;
    if (!hw->control_taken || !code || !bytes || bytes % 4 || bytes > DLD_PRU_IRAM_BYTES) {
        errno = EINVAL;
        return fail(error, cap, "invalid embedded PRU0 image or ownership");
    }
    iram = reg_at(hw->pruss_map, PRU0_IRAM);
    if (stop_prus(hw, 1, error, cap)) return -1;
    for (word = 0; word < bytes / 4; word++) iram[word] = code[word];
    barrier();
    for (word = 0; word < bytes / 4; word++) {
        if (iram[word] != code[word]) {
            if (cap) snprintf(error, cap, "PRU0 instruction RAM verification failed at word %lu", (unsigned long)word);
            return -1;
        }
    }
    *reg_at(hw->pruss_map, PRU0_CONTROL) = 2;
    barrier();
    return 0;
}

void dld_hw_cleanup(struct dld_hw *hw)
{
    char error[192];
    unsigned bank;
    if (!hw->command) return;
    if (stop_prus(hw, 3, error, sizeof error))
        fprintf(stderr, "WARNING: best-effort PRU cleanup incomplete: %s\n", error);
    hw->command->magic = 0;
    barrier();
    disable_notifications(hw);
    if (map_peripherals(hw, error, sizeof error)) {
        fprintf(stderr, "WARNING: best-effort GPIO cleanup incomplete: %s\n", error);
        return;
    }
    /* An inaccessible/failed bank must not prevent clearing the others. */
    for (bank = 0; bank < 3; bank++) {
        if (configure_bank(hw, bank, error, sizeof error))
            fprintf(stderr, "WARNING: best-effort GPIO%u cleanup incomplete: %s\n", bank, error);
    }
}
