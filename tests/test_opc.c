#include "dld_opc.h"

#include <stdio.h>
#include <string.h>

static unsigned checks;

static int expect(const unsigned char *packet, size_t length,
                    enum dld_opc_result wanted, uint32_t color)
{
    uint32_t actual = UINT32_C(0xdeadbeef);
    enum dld_opc_result result = dld_opc_color(packet, length, &actual);
    ++checks;
    if (result != wanted || actual !=
        (wanted == DLD_OPC_VALID ? color : UINT32_C(0xdeadbeef))) {
        fprintf(stderr, "OPC check %u failed: result=%d color=%08lx\n",
                checks, (int)result, (unsigned long)actual);
        return 1;
    }
    return 0;
}

int main(void)
{
    unsigned char packet[65507];
    unsigned i, length, payload;
    int failures = 0;
    memset(packet, 0, sizeof(packet));
    packet[3] = 3;
    packet[4] = 0x12; packet[5] = 0x34; packet[6] = 0x56;
    for (i = 0; i < 7; ++i)
        failures += expect(packet, i, DLD_OPC_MALFORMED, 0);
    failures += expect(NULL, 7, DLD_OPC_MALFORMED, 0);
    ++checks;
    if (dld_opc_color(packet, 7, NULL) != DLD_OPC_MALFORMED) ++failures;
    failures += expect(packet, 7, DLD_OPC_VALID, 0x123456);
    for (i = 0; i <= 255; ++i) {
        packet[0] = (unsigned char)i;
        failures += expect(packet, 7, DLD_OPC_VALID, 0x123456);
    }
    for (i = 1; i <= 255; ++i) {
        packet[1] = (unsigned char)i;
        failures += expect(packet, 7, DLD_OPC_UNSUPPORTED, 0);
    }
    packet[1] = 0;
    for (length = 0; length <= 32; ++length) {
        for (payload = 0; payload <= 32; ++payload) {
            packet[3] = (unsigned char)payload;
            failures += expect(packet, length,
                length >= 4 && payload >= 3 && payload <= length - 4 ?
                    DLD_OPC_VALID : DLD_OPC_MALFORMED, 0x123456);
        }
    }
    /* Big-endian length, complete maximum IPv4 UDP payload, truncated declared
     * maximum, and a second message which must not replace the first pixel.
     */
    packet[2] = 1; packet[3] = 0;
    failures += expect(packet, 259, DLD_OPC_MALFORMED, 0);
    failures += expect(packet, 260, DLD_OPC_VALID, 0x123456);
    packet[2] = (unsigned char)((sizeof(packet) - 4) >> 8);
    packet[3] = (unsigned char)(sizeof(packet) - 4);
    failures += expect(packet, sizeof(packet), DLD_OPC_VALID, 0x123456);
    packet[2] = 255; packet[3] = 255;
    failures += expect(packet, sizeof(packet), DLD_OPC_MALFORMED, 0);
    packet[2] = 0; packet[3] = 3;
    memcpy(packet + 7, "\0\0\0\3\xAA\xBB\xCC", 7);
    failures += expect(packet, 14, DLD_OPC_VALID, 0x123456);
    packet[4] = packet[5] = packet[6] = 0;
    failures += expect(packet, 7, DLD_OPC_VALID, 0);
    packet[4] = packet[5] = packet[6] = 255;
    failures += expect(packet, 7, DLD_OPC_VALID, 0xffffff);
    if (failures) return 1;
    printf("OPC parser: %u checks passed\n", checks);
    return 0;
}
