/* Packaging preflight: validate with the same parser as dld-init, without
 * acquiring the command lock or opening a hardware device.
 */
#include "dld_common.h"
#include <stdio.h>

int main(int argc, char **argv)
{
    struct dld_config config;
    char error[512];
    int code;
    if (argc != 2) {
        fprintf(stderr, "usage: dld-config-check CONFIG_FILE\n");
        return DLD_BAD_ARGUMENT;
    }
    code = dld_read_config(argv[1], &config, error, sizeof(error));
    if (code != DLD_OK) fprintf(stderr, "%s\n", error);
    else dld_print_configuration(&config);
    return code;
}
