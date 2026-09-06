#!/usr/bin/env python3
"""Validate the PRU image before embedding. Compatible with Python 3.2."""
import os
import struct
import sys

def main():
    if len(sys.argv) != 3:
        sys.exit("usage: embed_pru.py input.bin output.c")
    with open(sys.argv[1], "rb") as source:
        data = source.read()
    if not data or len(data) % 4 or len(data) > 8192:
        sys.exit("PRU image must contain 1..2048 complete 32-bit instruction words")
    words = struct.unpack("<%dI" % (len(data) // 4), data)
    destination = sys.argv[2] + ".tmp"
    with open(destination, "w") as output:
        output.write('/* Generated from the checked little-endian PRU image. */\n')
        output.write('#include <stdint.h>\n#include <stddef.h>\n#include "dld_abi.h"\n')
        output.write('const uint32_t dld_pru_code[] = {\n')
        for index in range(0, len(words), 6):
            output.write('    ' + ', '.join('0x%08xU' % w for w in words[index:index+6]) + ',\n')
        output.write('};\nconst size_t dld_pru_code_size = sizeof dld_pru_code;\n')
        output.write('typedef char dld_image_fits[(sizeof dld_pru_code <= DLD_PRU_IRAM_BYTES) ? 1 : -1];\n')
    os.rename(destination, sys.argv[2])
    print("PRU image: %d / 8192 bytes (%d / 2048 instruction words)" % (len(data), len(words)))

if __name__ == '__main__':
    main()
