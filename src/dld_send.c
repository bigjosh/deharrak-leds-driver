#define _POSIX_C_SOURCE 200809L
#include "dld_sender.h"

#include <inttypes.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    struct dld_sender sender;
    struct dld_send_result result;
    uint32_t rgb;
    char error[512];
    int code;
    if (argc != 2 || dld_parse_color(argv[1], &rgb) < 0) {
        fprintf(stderr, "usage: dld-send RRGGBB (optional 0x/0X prefix; exactly six hex digits)\n");
        return DLD_BAD_ARGUMENT;
    }
    if (dld_install_signals(error, sizeof(error)) < 0) { code = DLD_PREREQUISITE; goto report; }
    code = dld_sender_open(&sender, error, sizeof(error));
    if (code != DLD_OK) goto report;
    code = dld_sender_send(&sender, rgb, &result, error, sizeof(error));
    if (code == DLD_OK) {
        printf("OK color=%06" PRIX32 " quiet_cycles=%" PRIu32 ",%" PRIu32 ",%" PRIu32
               " dma_cycles=%" PRIu32 ",%" PRIu32 ",%" PRIu32 " ", rgb,
               result.quiet.irq_off_cycles[0], result.quiet.irq_off_cycles[1], result.quiet.irq_off_cycles[2],
               result.quiet.dma_drain_cycles[0], result.quiet.dma_drain_cycles[1], result.quiet.dma_drain_cycles[2]);
        dld_print_configuration(&result.config);
    }
    dld_sender_close(&sender);
report:
    if (code != DLD_OK) fprintf(stderr, "dld-send: %s\n", error);
    return code;
}
