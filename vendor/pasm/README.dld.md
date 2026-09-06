This is TI PASM 0.84, copied without source changes from the
[`am335x/pasm` directory of bigjosh/LEDscape](https://github.com/bigjosh/LEDscape/tree/b9a0fa22c46722daaabd0cdd44d42750b6c45483/am335x/pasm)
at commit `b9a0fa22c46722daaabd0cdd44d42750b6c45483`.

The original TI license is in [LICENCE.txt](LICENCE.txt) and the source headers.
`pasm.c` SHA-256:
`d3d262f1849f63bbb87e1c2a8bca9cdfff72e6169a57ab5ff5053dbdfce0c6b2`.

The project Makefile builds its own assembler, leaving installed PASM untouched.
The existing 0.84 source produces a non-prototype warning with current Clang;
the target GCC 4.6 build uses its original C99-compatible language behavior.
