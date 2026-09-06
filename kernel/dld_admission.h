#ifndef DLD_ADMISSION_H
#define DLD_ADMISSION_H

#ifdef __KERNEL__
#include <linux/errno.h>
#else
#include <errno.h>
#endif

/* Admission may wait for ordinary storage work, with CPU/DMA state restored
 * between attempts. These limits never extend an already-granted bank. */
#define DLD_ADMISSION_TIMEOUT_NS 1000000000LL
#define DLD_ADMISSION_MAX_ATTEMPTS 10000U

struct dld_admission_ops {
    long long (*now_ns)(void *context);
    int (*attempt)(void *context, int *granted);
    void (*pause)(void *context);
};

/* idle_only requests a final deadline check for a read-only idle observation.
 * A bank attempt may finish its protected stream after the admission deadline;
 * its own single completion observation remains authoritative. */
static inline int dld_admission_wait(const struct dld_admission_ops *ops,
                                     void *context, int idle_only)
{
    long long deadline = ops->now_ns(context) + DLD_ADMISSION_TIMEOUT_NS;
    unsigned attempts;
    for (attempts = 0; attempts < DLD_ADMISSION_MAX_ATTEMPTS; attempts++) {
        int result, granted = 0;
        if (ops->now_ns(context) >= deadline) return -ETIMEDOUT;
        result = ops->attempt(context, &granted);
        if (result == -EAGAIN && !granted) {
            ops->pause(context);
            continue;
        }
        if (!result && idle_only && ops->now_ns(context) >= deadline)
            return -ETIMEDOUT;
        return result; /* Any granted result, including failure, is terminal. */
    }
    return -ETIMEDOUT;
}

#endif
