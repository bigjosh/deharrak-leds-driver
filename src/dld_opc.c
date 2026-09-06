#include "dld_opc.h"

enum dld_opc_result dld_opc_color(const void *packet, size_t length,
                                  uint32_t *rgb)
{
    const unsigned char *bytes = packet;
    size_t payload;
    if (bytes == NULL || rgb == NULL || length < 4u)
        return DLD_OPC_MALFORMED;
    payload = ((size_t)bytes[2] << 8) | bytes[3];
    if (payload < 3u || payload > length - 4u)
        return DLD_OPC_MALFORMED;
    if (bytes[1] != 0u) return DLD_OPC_UNSUPPORTED;
    *rgb = ((uint32_t)bytes[4] << 16) |
           ((uint32_t)bytes[5] << 8) | bytes[6];
    return DLD_OPC_VALID;
}
