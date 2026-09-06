obj-m += dld_quiet.o
dld_quiet-y := dld_quiet_main.o dld_quiet_spin.o
ccflags-y += -I$(src)/../include -mcpu=cortex-a8
asflags-y += -I$(src)/../include -mcpu=cortex-a8 -marm
CFLAGS_dld_quiet_main.o += -fno-stack-protector
OBJDUMP ?= objdump

.PHONY: audit
audit:
	$(OBJDUMP) -dr dld_quiet.ko > dld_quiet.dis
	python3 audit_quiet.py dld_quiet.dis --self-test
