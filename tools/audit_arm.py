#!/usr/bin/env python3
"""Check the actual final-linked publication and spin instructions (Python 3.2)."""
import re
import sys

def main():
    if len(sys.argv) != 2:
        sys.exit('usage: audit_arm.py final-linked-objdump.dis')
    symbols, words = {}, {}
    with open(sys.argv[1]) as source:
        for line in source:
            symbol = re.match(r'^([0-9a-f]+) <([^>]+)>:', line)
            instruction = re.match(r'^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s', line)
            if symbol:
                symbols[symbol.group(2)] = int(symbol.group(1), 16)
            if instruction:
                words[int(instruction.group(1), 16)] = int(instruction.group(2), 16)
    for name in ('dld_publish_spin', 'dld_spin_begin', 'dld_spin_end'):
        if name not in symbols:
            sys.exit('missing linked symbol ' + name)
    start = symbols['dld_publish_spin']
    if start % 64 or symbols['dld_spin_begin'] != start + 16 or symbols['dld_spin_end'] != start + 24:
        sys.exit('publication/spin layout or alignment changed; review required')
    # AAPCS r0=mailbox r1=color r2=seq r3=count. Offsets are ABI3's36/40.
    expected = [0xe5801024, 0xf57ff05f, 0xe5802028, 0xf57ff04f,
                0xe2533001, 0x1afffffd, 0xe12fff1e]
    for index, word in enumerate(expected):
        address = start + 4 * index
        if words.get(address) != word:
            sys.exit('unexpected instruction at 0x%x; publication/spin audit failed' % address)
    print('ARM audit PASS: aligned 28-byte STR/DMB/STR/DSB/SUBS/BNE/BX routine;')
    print('  repeated loop is SUBS+BNE only, branch targets SUBS, no data/stack accesses.')
    print('  Cmin=1 dependent counter cycle, supported Cortex-A8 <=1 GHz; physical budget testing remains separate.')

if __name__ == '__main__':
    main()
