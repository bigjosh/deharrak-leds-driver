#define _POSIX_C_SOURCE 200809L
#include "dld_common.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static uint64_t now_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + now.tv_nsec;
}

int main(void)
{
    const uint32_t counts[] = {100000, 1000000, 28219000};
    struct dld_command dummy;
    uint64_t cpu_hz;
    char error[192];
    unsigned test, repeat;
    memset(&dummy, 0, sizeof dummy);
    if (dld_validate_cpu(&cpu_hz, error, sizeof error)) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    dld_publish_spin(&dummy, 0, 1, 32);
    for (test = 0; test < sizeof counts / sizeof counts[0]; test++) {
        uint64_t minimum = UINT64_MAX, maximum = 0;
        for (repeat = 0; repeat < 7; repeat++) {
            uint64_t start = now_ns(), elapsed;
            if (!start) return 1;
            dld_publish_spin(&dummy, 0, repeat, counts[test]);
            elapsed = now_ns() - start;
            if (elapsed < minimum) minimum = elapsed;
            if (elapsed > maximum) maximum = elapsed;
            /* At the supported1GHz ceiling/Cmin1, N iterations >= N ns. */
            if (elapsed < counts[test]) {
                fprintf(stderr, "FAIL spin ran shorter than its conservative bound\n");
                return 1;
            }
        }
        printf("spin iterations=%" PRIu32 " conservative_min_ns=%" PRIu32
               " observed_min_ns=%" PRIu64 " observed_max_ns=%" PRIu64 " samples=7\n",
               counts[test], counts[test], minimum, maximum);
    }
    puts("PASS observed spin bounds; this benchmark does not prove absence of preemption or worst-case GPIO latency.");
    return 0;
}
