#ifndef DLD_COMMON_H
#define DLD_COMMON_H

#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include "dld_abi.h"
#include "dld_profiles.h"

enum dld_exit_code {
    DLD_OK = 0, DLD_BAD_ARGUMENT = 2, DLD_PREREQUISITE = 3,
    DLD_INIT_FAILURE = 4, DLD_CRITICAL = 5, DLD_BUSY = 6,
    DLD_NOT_INITIALIZED = 7
};

struct dld_config {
    uint32_t profile_id;
    uint32_t string_lengths[DLD_STRING_COUNT];
};

struct dld_budget {
    uint64_t settle_ns;
    uint64_t quiet_ns;
    uint32_t iterations;
};

struct dld_completion {
    uint32_t status;
    uint32_t completion_seq;
    uint32_t error_detail;
};

/* Diagnostic normal-execution allowances; hardware qualification is pending.
 * Cmin=1 is a conservative lower bound for each dependent SUBS/BNE iteration
 * on Cortex-A8. Never replace it with an interrupt-inflated average timing.
 */
#define DLD_CPU_MAX_HZ UINT64_C(1000000000)
#define DLD_SPIN_MIN_CYCLES 1u
#define DLD_ACCEPTANCE_NS UINT64_C(100000)
#define DLD_CONTROL_NS UINT64_C(500000)
#define DLD_HOST_GUARD_NS UINT64_C(1000000)

/* Both commands must be built with the same fixed path. A dedicated test build
 * may set an absolute project-local path without changing deployed defaults.
 * There is deliberately no environment variable or per-command override.
 */
#ifndef DLD_LOCK_PATH
#define DLD_LOCK_PATH "/var/lock/dld.lock"
#endif

int dld_parse_color(const char *text, uint32_t *rgb);
int dld_parse_config(const char *json, size_t length,
                     struct dld_config *config, char *error, size_t cap);
/* File loading returns an exit code: content=2, read/allocation error=3. */
int dld_read_config(const char *path, struct dld_config *config,
                    char *error, size_t cap);
const char *dld_profile_name(uint32_t profile_id);
uint32_t dld_wire_color(uint32_t profile_id, uint32_t rgb);
void dld_bank_info(const struct dld_config *config, uint32_t masks[3],
                   uint32_t lengths[3]);
unsigned dld_group_count(const struct dld_config *config);
int dld_compute_budget(const struct dld_config *config, uint64_t cpu_hz,
                       struct dld_budget *budget, char *error, size_t cap);
/* These checks deliberately do not examine PRU run state or instruction RAM. */
int dld_check_mailbox(const struct dld_command *snapshot,
                      struct dld_config *config, char *error, size_t cap);
void dld_snapshot(volatile const struct dld_command *command,
                   struct dld_command *snapshot);
void dld_read_completion(volatile const struct dld_command *command,
                         struct dld_completion *completion);
int dld_completion_matches(const struct dld_completion *completion,
                            uint32_t request_seq);
void dld_memory_barrier(void);

int dld_lock(char *error, size_t cap); /* fd, -3 prerequisite, -6 busy */
int dld_install_signals(char *error, size_t cap);
extern volatile sig_atomic_t dld_cancelled;
int dld_validate_cpu(uint64_t *cpu_hz, char *error, size_t cap);
const char *dld_error_name(uint32_t detail);
void dld_print_configuration(const struct dld_config *config);

/* Historical userspace countdown, retained only for the dummy-memory benchmark.
 * Neither ABI4 product CLI links or calls it; the kernel owns real grants.
 */
void dld_publish_spin(volatile struct dld_command *command,
                       uint32_t wire_color, uint32_t request_seq,
                       uint32_t iterations);

#endif
