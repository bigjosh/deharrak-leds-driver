#ifndef DLD_FLASH_H
#define DLD_FLASH_H

#include <stdint.h>

#define DLD_FLASH_HALF_NS UINT64_C(500000000)
#define DLD_FLASH_DURATION_NS UINT64_C(1000000000)
#define DLD_FLASH_IDLE_NS UINT64_C(60000000000)

struct dld_flash {
    uint64_t started_ns;
    uint32_t color;
    unsigned initial_sent;
    unsigned peak_sent;
};

/* Pure frame selection for a one-second triangular flash of a 24-bit RGB
 * color. Timestamps use a caller-provided monotonic nanosecond clock. The
 * initial black, full-color peak, and final black are always selected, even
 * when blocking transmission skips a deadline. State advances on selection;
 * the caller must send synchronously and stop after a send failure.
 */
void dld_flash_start(struct dld_flash *flash, uint32_t color, uint64_t now);

/* Store the next RGB sample. Return 1 only for the final black frame, after
 * which the caller should stop selecting. Backwards timestamps clamp elapsed
 * time to zero. A completed state continues to return final black unless its
 * clock moves backwards; normal callers stop on the first return of 1.
 */
int dld_flash_next(struct dld_flash *flash, uint64_t now, uint32_t *rgb);

/* Strictly more than 60 seconds since baseline; backwards time is not due. */
int dld_flash_idle_due(uint64_t now, uint64_t baseline);

#endif
