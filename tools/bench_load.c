#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Test-only contention generator. No GPIO, PRU, device, service, clock-policy
 * or disk access. The caller may redirect the single final report to its log.
 * Stop checks occur between complete sweeps; elapsed time may exceed the
 * requested duration by one sweep plus Linux scheduling delays.
 * Reported bytes count logical buffer reads/writes, not measured DDR traffic.
 */
#define MIB_BYTES UINT64_C(1048576)
#define CPU_STEPS UINT32_C(1048576)
#define MAX_SECONDS 3600u
#define MAX_MIB 64u

static volatile sig_atomic_t stopped_signal;

static void request_stop(int number)
{
    if (!stopped_signal) stopped_signal = number;
}

static int bounded_integer(const char *text, unsigned maximum, unsigned *result)
{
    unsigned value = 0;
    const unsigned char *position = (const unsigned char *)text;
    if (!*position) return -1;
    while (*position) {
        unsigned digit;
        if (*position < '0' || *position > '9') return -1;
        digit = (unsigned)(*position++ - '0');
        if (value > (maximum - digit) / 10u) return -1;
        value = value * 10u + digit;
        if (value > maximum) return -1;
    }
    if (!value) return -1;
    *result = value;
    return 0;
}

static int monotonic_ns(uint64_t *result)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    *result = (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
    return 0;
}

static void usage(void)
{
    fprintf(stderr, "usage: bench-load --seconds 1..3600 [--mode ddr|cpu] [--mib 1..64]\n"
                    "       DDR defaults to 32 MiB; --mib is only valid in DDR mode.\n");
}

int main(int argc, char **argv)
{
    const char *mode = "ddr";
    unsigned seconds = 0, mib = 32, seen = 0;
    uint64_t started, now, target_ns, sweeps = 0, touched_bytes = 0, steps = 0;
    uint32_t checksum = 0, state;
    volatile uint32_t *buffer = NULL;
    size_t words = 0, i;
    int argument, cpu_mode, failed = 0;

    for (argument = 1; argument < argc; ++argument) {
        const char *name = argv[argument];
        unsigned field;
        if (!strcmp(name, "--seconds")) field = 1u;
        else if (!strcmp(name, "--mode")) field = 2u;
        else if (!strcmp(name, "--mib")) field = 4u;
        else { usage(); return 2; }
        if ((seen & field) || ++argument == argc) { usage(); return 2; }
        seen |= field;
        if (field == 1u) {
            if (bounded_integer(argv[argument], MAX_SECONDS, &seconds)) { usage(); return 2; }
        } else if (field == 4u) {
            if (bounded_integer(argv[argument], MAX_MIB, &mib)) { usage(); return 2; }
        } else {
            mode = argv[argument];
            if (strcmp(mode, "ddr") && strcmp(mode, "cpu")) { usage(); return 2; }
        }
    }
    cpu_mode = !strcmp(mode, "cpu");
    if (!seconds || (cpu_mode && (seen & 4u))) { usage(); return 2; }
    if (signal(SIGINT, request_stop) == SIG_ERR ||
        signal(SIGTERM, request_stop) == SIG_ERR) {
        perror("bench-load: install stop handlers");
        return 1;
    }
    if (!cpu_mode) {
        size_t bytes = (size_t)((uint64_t)mib * MIB_BYTES);
        buffer = (volatile uint32_t *)malloc(bytes);
        if (!buffer) {
            fprintf(stderr, "bench-load: cannot allocate %u MiB: %s\n", mib, strerror(errno));
            return 1;
        }
        words = bytes / sizeof(*buffer);
    }
    if (monotonic_ns(&started)) {
        perror("bench-load: read monotonic start time");
        free((void *)buffer);
        return 1;
    }
    now = started;
    target_ns = (uint64_t)seconds * UINT64_C(1000000000);
    state = (uint32_t)started | 1u;
    while (!stopped_signal && now - started < target_ns) {
        if (cpu_mode) {
            uint32_t iteration;
            /* Scalar working state and a small loop fit in cache. Both
             * dependent integer mixing and its final result remain observable.
             */
            for (iteration = 0; iteration < CPU_STEPS; ++iteration) {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                state = state * UINT32_C(1664525) + UINT32_C(1013904223);
            }
            checksum ^= state;
            steps += CPU_STEPS;
        } else {
            /* Separate whole-buffer write/read sweeps exceed the Cortex-A8
             * cache capacity. Volatile keeps every individual access; there
             * are no cache flushes or privileged tuning operations. The first
             * sweep includes page faults, so it is not a warmed measurement.
             */
            for (i = 0; i < words; ++i) buffer[i] = state + (uint32_t)i;
            for (i = 0; i < words; ++i) {
                checksum = (checksum << 5) | (checksum >> 27);
                checksum ^= buffer[i];
            }
            state += UINT32_C(2654435769);
            touched_bytes += (uint64_t)words * sizeof(*buffer) * 2u;
        }
        ++sweeps;
        if (monotonic_ns(&now)) {
            perror("bench-load: read monotonic stop time");
            failed = 1;
            break;
        }
    }
    /* Include time through the final stop observation in the report. A clock
     * failure is reported as such; the previous sample is never called final.
     */
    if (monotonic_ns(&now)) {
        perror("bench-load: read final monotonic time");
        failed = 1;
    }
    printf("mode=%s mib=%u sweeps=%" PRIu64 " bytes=%" PRIu64
           " cpu_steps=%" PRIu64 " checksum=%08" PRIX32
           " elapsed_seconds=%.6f stop=%s signal=%d\n",
           mode, cpu_mode ? 0u : mib, sweeps, touched_bytes, steps, checksum,
           (double)(now - started) / 1000000000.0,
           failed ? "clock_error" : stopped_signal ? "signal" : "duration",
           (int)stopped_signal);
    free((void *)buffer);
    /* A handled stop signal is a normal requested end to this test workload. */
    return failed ? 1 : 0;
}
