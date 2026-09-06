#ifndef DLD_OPC_H
#define DLD_OPC_H

#include <stddef.h>
#include <stdint.h>

enum dld_opc_result {
    DLD_OPC_VALID = 0,
    DLD_OPC_MALFORMED = 1,
    DLD_OPC_UNSUPPORTED = 2
};

/* Decode only the first OPC message in one complete UDP datagram. Channel is
 * ignored, as in LEDscape. Require a complete declared payload and at least
 * one RGB pixel; trailing bytes are accepted but never interpreted as another
 * message. On rejection, *rgb is unchanged. No color correction is applied.
 */
enum dld_opc_result dld_opc_color(const void *packet, size_t length,
                                  uint32_t *rgb);

#endif
