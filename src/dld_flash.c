#include "dld_flash.h"

static uint32_t scale_color(uint32_t color, uint64_t weight)
{
    uint32_t rgb = 0;
    unsigned shift;
    for (shift = 0; shift < 24; shift += 8) {
        uint64_t channel = (color >> shift) & UINT32_C(0xff);
        uint32_t scaled = (uint32_t)((channel * weight +
            DLD_FLASH_HALF_NS / 2) / DLD_FLASH_HALF_NS);
        rgb |= scaled << shift;
    }
    return rgb;
}

void dld_flash_start(struct dld_flash *flash, uint32_t color, uint64_t now)
{
    flash->started_ns = now;
    flash->color = color;
    flash->initial_sent = 0;
    flash->peak_sent = 0;
}

int dld_flash_next(struct dld_flash *flash, uint64_t now, uint32_t *rgb)
{
    uint64_t elapsed = now >= flash->started_ns ? now - flash->started_ns : 0;
    uint64_t weight;
    if (!flash->initial_sent) {
        flash->initial_sent = 1;
        *rgb = 0;
        return 0;
    }
    if (!flash->peak_sent && elapsed >= DLD_FLASH_HALF_NS) {
        flash->peak_sent = 1;
        *rgb = flash->color;
        return 0;
    }
    if (flash->peak_sent && elapsed >= DLD_FLASH_DURATION_NS) {
        *rgb = 0;
        return 1;
    }
    weight = elapsed <= DLD_FLASH_HALF_NS ? elapsed :
        DLD_FLASH_DURATION_NS - elapsed;
    *rgb = scale_color(flash->color, weight);
    return 0;
}

int dld_flash_idle_due(uint64_t now, uint64_t baseline)
{
    return now >= baseline && now - baseline > DLD_FLASH_IDLE_NS;
}
