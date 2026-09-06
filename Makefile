CC = gcc
PYTHON = python3
CPPFLAGS += -Iinclude -Isrc -D_POSIX_C_SOURCE=200809L
ifdef LOCK_PATH
CPPFLAGS += -DDLD_LOCK_PATH='"$(LOCK_PATH)"'
endif
CFLAGS = -std=c99 -O2 -Wall -Wextra -Werror -mcpu=cortex-a8 -marm -mfpu=neon -mfloat-abi=hard
LDFLAGS += -Wl,-z,now
LDLIBS += -lrt

PASM_SOURCES = $(addprefix vendor/pasm/,pasm.c pasmpp.c pasmexp.c pasmop.c pasmdot.c pasmstruct.c pasmmacro.c)
COMMON_OBJECTS = build/dld_common.o build/dld_hw.o
SENDER_OBJECTS = build/dld_sender.o $(COMMON_OBJECTS)

.PHONY: all clean test test-native audit report kernel-module
all: build/dld-init build/dld-send build/dld-udp kernel-module

KDIR ?= /lib/modules/$(shell uname -r)/build
kernel-module:
	$(MAKE) -C $(KDIR) M=$(CURDIR)/kernel modules

build:
	mkdir -p build

build/pasm: $(PASM_SOURCES) vendor/pasm/pasm.h vendor/pasm/pasmdbg.h vendor/pasm/pru_ins.h | build
	$(CC) -std=c99 -O2 -D_UNIX_ -include strings.h -o $@ $(PASM_SOURCES)

build/pru.bin: pru/ws2812_uniform.p include/dld_abi.h include/dld_profiles.h build/pasm
	build/pasm -V3 -b -L -l pru/ws2812_uniform.p build/pru
	$(PYTHON) tools/embed_pru.py build/pru.bin build/pru_blob.c

build/pru_blob.c: build/pru.bin
	@test -f $@ || $(PYTHON) tools/embed_pru.py build/pru.bin $@

build/pru_blob.o: build/pru_blob.c include/dld_abi.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

build/%.o: src/%.c include/dld_abi.h include/dld_profiles.h include/dld_quiet.h src/dld_common.h src/dld_hw.h src/dld_sender.h src/dld_opc.h src/dld_flash.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

build/dld_spin.o: src/dld_spin.S include/dld_abi.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

build/dld-init: build/dld_init.o $(COMMON_OBJECTS) build/pru_blob.o
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/dld-send: build/dld_send.o $(SENDER_OBJECTS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/dld-config-check: build/dld_config_check.o build/dld_common.o
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/dld-udp: build/dld_udp.o build/dld_opc.o build/dld_flash.o $(SENDER_OBJECTS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/test-common: tests/test_common.c build/dld_common.o build/dld_spin.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/test-send-syscall: tests/test_send_syscall.c src/dld_send.c src/dld_sender.c src/dld_sender.h build/dld_common.o include/dld_quiet.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_send_syscall.c build/dld_common.o $(LDLIBS)

build/test-opc: tests/test_opc.c src/dld_opc.c src/dld_opc.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_opc.c src/dld_opc.c

build/test-flash: tests/test_flash.c src/dld_flash.c src/dld_flash.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_flash.c src/dld_flash.c

build/test-udp: src/dld_udp.c src/dld_opc.c src/dld_opc.h src/dld_flash.c src/dld_flash.h src/dld_sender.h tests/fake_udp_sender.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -Wl,--wrap=clock_gettime -Wl,--wrap=poll -o $@ src/dld_udp.c src/dld_opc.c src/dld_flash.c tests/fake_udp_sender.c $(LDLIBS)

build/test-admission: tests/test_admission.c kernel/dld_admission.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/test_admission.c $(LDLIBS)

build/bench-spin: tests/bench_spin.c build/dld_common.o build/dld_spin.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

build/hw-probe: tests/hw_probe.c build/dld_hw.o include/dld_abi.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/hw_probe.c build/dld_hw.o $(LDLIBS)

build/quiet-kernel-probe: tests/quiet_kernel_probe.c build/dld_hw.o include/dld_quiet.h include/dld_abi.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ tests/quiet_kernel_probe.c build/dld_hw.o $(LDLIBS)

test-native: all build/test-common build/test-send-syscall build/test-admission build/test-opc build/test-flash build/test-udp
	build/test-common
	build/test-send-syscall
	build/test-admission
	build/test-opc
	build/test-flash
	$(PYTHON) tests/test_udp.py "$(CURDIR)/build/test-udp"
	sh tests/test_cli.sh "$(CURDIR)/build"

audit: all
	objdump -d build/dld-send > build/dld-send.dis
	objdump -d build/dld-init > build/dld-init.dis
	objdump -d build/dld-udp > build/dld-udp.dis
	$(PYTHON) tests/pru_audit.py build/pru.bin build/pru.lst
	$(MAKE) -C kernel audit

test: test-native audit

report: all
	DLD_REPORT_CFLAGS='$(CFLAGS)' DLD_REPORT_LOCK='$(if $(LOCK_PATH),$(LOCK_PATH),/var/lock/dld.lock)' sh tools/build-report.sh > build/build-report.txt
	cat build/build-report.txt

clean:
	rm -f build/*.o build/dld-init build/dld-send build/dld-udp build/dld-config-check build/test-common build/test-send-syscall build/test-admission build/test-opc build/test-flash build/test-udp build/bench-spin build/hw-probe build/quiet-kernel-probe build/pasm build/pru.bin build/pru.txt build/pru.lst build/pru_blob.c build/*.dis build/build-report.txt
