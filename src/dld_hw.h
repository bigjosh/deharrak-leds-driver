#ifndef DLD_HW_H
#define DLD_HW_H
#include <stddef.h>
#include <stdint.h>
#include "dld_abi.h"

struct dld_hw {
    volatile struct dld_command *command;
    void *pruss_map;
    size_t pruss_size;
    int uio_fd;
    int mem_fd;
    void *gpio_map[3];
    void *clock_map;
    int control_taken;
};

/* Open/close attach and release mappings only, never reset PRUs or GPIOs. */
int dld_hw_open(struct dld_hw *hw, int for_init, char *error, size_t cap);
void dld_hw_close(struct dld_hw *hw);
int dld_hw_take_control(struct dld_hw *hw, char *error, size_t cap);
int dld_hw_configure_outputs(struct dld_hw *hw, char *error, size_t cap);
int dld_hw_start(struct dld_hw *hw, const uint32_t *code, size_t bytes,
                 char *error, size_t cap);
/* After ownership/submission only: stops PRUs, clears pins, invalidates RAM. */
void dld_hw_cleanup(struct dld_hw *hw);

extern const uint32_t dld_pru_code[];
extern const size_t dld_pru_code_size;
#endif
