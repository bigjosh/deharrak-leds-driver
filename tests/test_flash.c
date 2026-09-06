#include "dld_flash.h"

#include <stdio.h>

static unsigned checks;
static unsigned failures;

#define CHECK(expression) do { \
    ++checks; \
    if (!(expression)) { \
        fprintf(stderr, "flash check %u failed at line %u: %s\n", \
                checks, (unsigned)__LINE__, #expression); \
        ++failures; \
    } \
} while (0)

static void sample(struct dld_flash *flash, uint64_t now,
                   uint32_t expected, int final)
{
    uint32_t rgb = UINT32_C(0xdeadbeef);
    int result = dld_flash_next(flash, now, &rgb);
    CHECK(rgb == expected);
    CHECK(result == final);
}

static void regular_samples(void)
{
    struct dld_flash flash;
    const uint64_t start = UINT64_C(1234567890);
    dld_flash_start(&flash, UINT32_C(0xffffff), start);
    CHECK(flash.started_ns == start && flash.color == UINT32_C(0xffffff));
    CHECK(!flash.initial_sent && !flash.peak_sent);
    sample(&flash, start, 0, 0);
    CHECK(flash.initial_sent && !flash.peak_sent);
    sample(&flash, start + UINT64_C(100000000), UINT32_C(0x333333), 0);
    sample(&flash, start + UINT64_C(250000000), UINT32_C(0x808080), 0);
    sample(&flash, start + UINT64_C(400000000), UINT32_C(0xcccccc), 0);
    sample(&flash, start + DLD_FLASH_HALF_NS, UINT32_C(0xffffff), 0);
    CHECK(flash.peak_sent);
    sample(&flash, start + UINT64_C(600000000), UINT32_C(0xcccccc), 0);
    sample(&flash, start + UINT64_C(750000000), UINT32_C(0x808080), 0);
    sample(&flash, start + UINT64_C(900000000), UINT32_C(0x333333), 0);
    sample(&flash, start + DLD_FLASH_DURATION_NS - 1, 0, 0);
    sample(&flash, start + DLD_FLASH_DURATION_NS, 0, 1);
    sample(&flash, start + DLD_FLASH_DURATION_NS + 1, 0, 1);
}

static void blocking_and_deadlines(void)
{
    struct dld_flash flash;
    /* Variable completed-send durations advance selection on wall time,
     * without advancing through a nominal frame-count sequence.
     */
    dld_flash_start(&flash, UINT32_C(0xff8040), 0);
    sample(&flash, 0, 0, 0);
    sample(&flash, UINT64_C(100000000), UINT32_C(0x331a0d), 0);
    sample(&flash, UINT64_C(400000000), UINT32_C(0xcc6633), 0);
    sample(&flash, UINT64_C(650000000), UINT32_C(0xff8040), 0);
    sample(&flash, UINT64_C(750000000), UINT32_C(0x804020), 0);
    sample(&flash, UINT64_C(1400000000), 0, 1);

    /* A first call delayed past both deadlines still starts with black,
     * forces one full-color frame, then ends black at the same timestamp.
     */
    dld_flash_start(&flash, UINT32_C(0x123456), 10);
    sample(&flash, UINT64_C(9000000010), 0, 0);
    sample(&flash, UINT64_C(9000000010), UINT32_C(0x123456), 0);
    sample(&flash, UINT64_C(9000000010), 0, 1);

    /* Crossing the end immediately after the initial frame cannot omit peak. */
    dld_flash_start(&flash, UINT32_C(0x010203), 0);
    sample(&flash, 0, 0, 0);
    sample(&flash, DLD_FLASH_DURATION_NS, UINT32_C(0x010203), 0);
    sample(&flash, DLD_FLASH_DURATION_NS, 0, 1);
}

static void rounding_and_colors(void)
{
    struct dld_flash flash;
    unsigned channel;
    dld_flash_start(&flash, UINT32_C(0x123456), 0);
    sample(&flash, 0, 0, 0);
    sample(&flash, DLD_FLASH_HALF_NS / 2, UINT32_C(0x091a2b), 0);
    sample(&flash, DLD_FLASH_HALF_NS, UINT32_C(0x123456), 0);
    sample(&flash, DLD_FLASH_HALF_NS + DLD_FLASH_HALF_NS / 2,
           UINT32_C(0x091a2b), 0);

    /* Half-integer channel results round upward, independently per byte. */
    dld_flash_start(&flash, UINT32_C(0x010305), 0);
    sample(&flash, 0, 0, 0);
    sample(&flash, DLD_FLASH_HALF_NS / 2 - 1, UINT32_C(0x000102), 0);
    sample(&flash, DLD_FLASH_HALF_NS / 2, UINT32_C(0x010203), 0);
    sample(&flash, DLD_FLASH_HALF_NS / 2 + 1, UINT32_C(0x010203), 0);
    sample(&flash, DLD_FLASH_HALF_NS, UINT32_C(0x010305), 0);
    sample(&flash, DLD_FLASH_HALF_NS + DLD_FLASH_HALF_NS / 2,
           UINT32_C(0x010203), 0);

    /* Cover every channel value and byte position with exact half intensity. */
    for (channel = 0; channel <= 255; ++channel) {
        uint32_t rgb = (uint32_t)channel * UINT32_C(0x010101);
        uint32_t half = (uint32_t)((channel + 1) / 2) * UINT32_C(0x010101);
        dld_flash_start(&flash, rgb, 0);
        sample(&flash, 0, 0, 0);
        sample(&flash, DLD_FLASH_HALF_NS / 2, half, 0);
        sample(&flash, DLD_FLASH_HALF_NS, rgb, 0);
        sample(&flash, DLD_FLASH_DURATION_NS, 0, 1);
    }
}

static void clock_limits_and_restart(void)
{
    struct dld_flash flash;
    uint64_t near_end = UINT64_MAX - DLD_FLASH_DURATION_NS;
    dld_flash_start(&flash, UINT32_C(0xff0000), near_end);
    sample(&flash, near_end, 0, 0);
    sample(&flash, near_end - 1, 0, 0);
    sample(&flash, 0, 0, 0);
    sample(&flash, near_end + DLD_FLASH_HALF_NS, UINT32_C(0xff0000), 0);
    sample(&flash, near_end - 1, 0, 0);
    sample(&flash, UINT64_MAX, 0, 1);

    dld_flash_start(&flash, UINT32_C(0x00ff00), 0);
    CHECK(!flash.initial_sent && !flash.peak_sent && flash.color == UINT32_C(0x00ff00));
    sample(&flash, UINT64_MAX, 0, 0);
    sample(&flash, UINT64_MAX, UINT32_C(0x00ff00), 0);
    sample(&flash, UINT64_MAX, 0, 1);

    dld_flash_start(&flash, 0, 42);
    sample(&flash, 42, 0, 0);
    sample(&flash, 42 + DLD_FLASH_HALF_NS, 0, 0);
    CHECK(flash.peak_sent);
    sample(&flash, 42 + DLD_FLASH_DURATION_NS, 0, 1);
}

static void idle_limits(void)
{
    uint64_t baseline = UINT64_C(5000000000);
    CHECK(!dld_flash_idle_due(0, 0));
    CHECK(!dld_flash_idle_due(baseline - 1, baseline));
    CHECK(!dld_flash_idle_due(baseline + DLD_FLASH_IDLE_NS - 1, baseline));
    CHECK(!dld_flash_idle_due(baseline + DLD_FLASH_IDLE_NS, baseline));
    CHECK(dld_flash_idle_due(baseline + DLD_FLASH_IDLE_NS + 1, baseline));
    /* A newly reset baseline starts a fresh full idle interval. */
    baseline += DLD_FLASH_IDLE_NS + 1;
    CHECK(!dld_flash_idle_due(baseline, baseline));
    CHECK(!dld_flash_idle_due(baseline + DLD_FLASH_IDLE_NS, baseline));
    CHECK(dld_flash_idle_due(baseline + DLD_FLASH_IDLE_NS + 1, baseline));
    CHECK(dld_flash_idle_due(UINT64_MAX, 0));
    CHECK(!dld_flash_idle_due(0, UINT64_MAX));
    CHECK(!dld_flash_idle_due(UINT64_MAX, UINT64_MAX - DLD_FLASH_IDLE_NS));
    CHECK(dld_flash_idle_due(UINT64_MAX, UINT64_MAX - DLD_FLASH_IDLE_NS - 1));
}

int main(void)
{
    CHECK(DLD_FLASH_HALF_NS * 2 == DLD_FLASH_DURATION_NS);
    regular_samples();
    blocking_and_deadlines();
    rounding_and_colors();
    clock_limits_and_restart();
    idle_limits();
    if (failures) return 1;
    printf("Flash timing: %u checks passed\n", checks);
    return 0;
}
