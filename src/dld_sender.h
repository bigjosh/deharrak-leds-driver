#ifndef DLD_SENDER_H
#define DLD_SENDER_H

#include "dld_common.h"
#include "dld_hw.h"
#include "dld_quiet.h"

/* One process-local, single-threaded attachment. It owns no configuration
 * file and never initializes hardware. Device mappings and descriptors remain
 * open between sends; the cooperating command lock is held only while attaching
 * or sending. Stop/close resident clients before replacing UIO or the module.
 */
struct dld_sender {
    struct dld_hw hw;
    int lock_fd;
    int quiet_fd;
    int mapped;
    int faulted;
};

struct dld_send_result {
    struct dld_config config;
    struct dld_quiet_send quiet;
};

/* Return dld_exit_code values; error receives a diagnostic on failure.
 * Open initializes the context even on failure. Close is then always valid.
 * Do not open an already-open context or use it concurrently/from a fork.
 */
int dld_sender_open(struct dld_sender *sender, char *error, size_t cap);
int dld_sender_send(struct dld_sender *sender, uint32_t rgb,
                    struct dld_send_result *result, char *error, size_t cap);
/* Close releases attachment only. It does not clear the displayed color or
 * stop the PRU. Critical send failures already performed cleanup under lock.
 */
void dld_sender_close(struct dld_sender *sender);

#endif
