#!/usr/bin/env python3
"""Audit PASM's actual little-endian image (Python >=3.2, no dependencies).

This is an instruction-level functional/nominal-cycle model, not an AM335x
interconnect simulator. It proves issuing schedules; hardware qualification
must measure physical edges. The listing supplies labels only, and every
listing opcode is checked against the binary before execution.
"""
from __future__ import print_function
import argparse
import itertools
import re
import struct
import sys

MASK32 = 0xffffffff
BASES = (0x481ac000, 0x4804c000, 0x44e07000)
FIXED = {BASES[0]: 0x1a, BASES[1]: 0x5000, BASES[2]: 1 << 26}
PINS = ((BASES[0], 3), (BASES[0], 4), (BASES[1], 12),
        (BASES[2], 26), (BASES[1], 14), (BASES[0], 1))


def field(code):
    part = code >> 5
    if part < 4:
        return code & 31, part * 8, 0xff
    if part < 7:
        return code & 31, (part - 4) * 8, 0xffff
    return code & 31, 0, MASK32


def decode(word, pc):
    top = word >> 27
    if top in (9, 10, 11, 12, 13, 14, 15, 25, 26):
        rel = (word & 255) | ((word >> 17) & 0x300)
        if rel & 0x200:
            rel -= 0x400
        return ('branch', top, (word >> 8) & 255,
                bool(word & (1 << 24)), (word >> 16) & 255, pc + rel)
    high = word >> 28
    if high in (8, 9, 14, 15):
        count = ((word >> 21) & 0x70) | ((word >> 12) & 14) | ((word >> 7) & 1)
        assert count < 124, 'Register-count burst unsupported by audit'
        return ('burst', high, (word & 31) * 4 + ((word >> 5) & 3),
                (word >> 8) & 31, bool(word & (1 << 24)),
                (word >> 16) & 255, count + 1)
    op = word >> 25
    if word >> 24 == 0x24:
        return ('ldi', word & 255, (word >> 8) & 0xffff)
    if op in (16, 17):
        immediate = bool(word & (1 << 24))
        value = (word >> 8) & 0xffff if immediate else (word >> 16) & 255
        return ('jump', op, word & 255, immediate, value)
    if op == 21:
        return ('halt',)
    if op in (0, 2, 4, 5, 8, 9, 10, 11, 14, 15):
        return ('alu', op, word & 255, (word >> 8) & 255,
                bool(word & (1 << 24)), (word >> 16) & 255)
    raise AssertionError('Unknown machine instruction %08x at %04x' % (word, pc))


class Image(object):
    def __init__(self, binary, listing):
        with open(binary, 'rb') as stream:
            data = stream.read()
        assert 0 < len(data) <= 8192 and len(data) % 4 == 0, 'PRU IRAM size'
        words = struct.unpack('<%dI' % (len(data) // 4), data)
        self.code = [decode(w, pc) for pc, w in enumerate(words)]
        self.labels = {}
        seen = set()
        with open(listing, 'r') as stream:
            for line in stream:
                match = re.search(r': 0x([0-9a-f]+) = Label\s+: (\w+):', line, re.I)
                if match:
                    self.labels[match.group(2)] = int(match.group(1), 16)
                match = re.search(r': 0x([0-9a-f]+) = 0x([0-9a-f]+)', line, re.I)
                if match:
                    pc, word = int(match.group(1), 16), int(match.group(2), 16)
                    assert words[pc] == word, 'Listing/binary mismatch'
                    seen.add(pc)
        assert len(seen) == len(words), 'Listing does not cover binary'
        self.begin = self.labels['TIMED_BEGIN']
        self.end = self.labels['TIMED_END']
        selects = []
        for pc in range(self.begin, self.end):
            ins = self.code[pc]
            if ins[0] == 'burst':
                assert ins[1:] in ((14, 36, 7, True, 0, 4),
                                  (14, 36, 7, True, 4, 4)), 'Timed memory access'
            else:
                assert ins[0] in ('alu', 'branch', 'jump'), 'Timed non-register instruction'
            if ins[0] == 'branch' and ins[1] == 26 and ins[2] == 0xe4:
                assert ins[3], 'Bit position is not an immediate'
                selects.append(ins[4])
            if ins[0] == 'alu':
                assert ins[2] & 31 < 30, 'Direct pin/interrupt register modified'
        assert selects == list(range(23, -1, -1)), '24 bit positions are not unrolled'
        # No instruction anywhere may publish a PRU-to-ARM event through R31.
        for ins in self.code:
            if ins[0] in ('alu', 'ldi'):
                dest = ins[2] if ins[0] == 'alu' else ins[1]
                assert dest & 31 < 30, 'Unexpected R30/R31 write'
        self.size = len(data)


class Machine(object):
    def __init__(self, image, lengths, profile=1, magic=0x444c4431, abi=4):
        self.image = image
        self.r = [0] * 32
        self.ram = bytearray(8192)
        struct.pack_into('<18I', self.ram, 0, magic, abi, profile,
                         *(list(lengths) + [0] * 9))
        self.lengths = tuple(lengths)
        self.original_config = bytes(self.ram[:36])
        self.gpio = dict((base, (0xa5a5a5a5 | mask) & MASK32)
                         for base, mask in FIXED.items())
        self.unrelated = dict((base, value & ~FIXED[base]) for base, value in self.gpio.items())
        self.oe = dict((base, MASK32 & ~mask) for base, mask in FIXED.items())
        self.stuck = {}
        self.control = {0x26004: 0x10, 0x22020: 0x55}
        self.pc = 0
        self.cycles = 0
        self.halted = False
        self.events = []
        self.reads = []
        self.statuses = []
        self.fast_loops = 0
        self.grants = []
        self.bank_completions = []

    def reg(self, code):
        index, shift, mask = field(code)
        return (self.r[index] >> shift) & mask

    def put(self, code, value):
        index, shift, mask = field(code)
        self.r[index] = (self.r[index] & ~(mask << shift)) | ((value & mask) << shift)

    def word(self, offset):
        return struct.unpack_from('<I', self.ram, offset)[0]

    def read(self, address, count):
        if address < 8192:
            return self.ram[address:address + count]
        if address in self.control:
            value = self.control[address]
        else:
            base = address - (address & 0xfff)
            assert base in FIXED and count == 4, 'Unexpected MMIO read'
            offset = address - base
            if offset == 0x134:
                value = self.oe[base]
            else:
                assert offset == 0x13c, 'Unexpected GPIO register read'
                value = self.gpio[base] | self.stuck.get(base, 0)
                self.reads.append((self.cycles, base))
        return bytearray(struct.pack('<I', value))

    def write(self, address, data):
        if address < 8192:
            assert address in (44, 48, 52, 56, 64, 68) and len(data) == 4, 'Firmware overwrote host config/request/grant'
            self.ram[address:address + len(data)] = data
            if address == 48:
                self.statuses.append((self.cycles, self.word(48)))
            if address == 64 and self.word(64):
                ordinal = self.word(64)
                assert 1 <= ordinal <= 3
                base = BASES[ordinal - 1]
                assert self.gpio[base] & FIXED[base] == 0, 'Bank completion before final low'
                assert self.reads and self.reads[-1][1] == base, 'Bank completion before drain/readback'
                self.bank_completions.append((self.cycles, ordinal))
            return
        value = struct.unpack('<I', data)[0]
        if address in self.control:
            self.control[address] = value
            return
        base = address - (address & 0xfff)
        assert base in FIXED and len(data) == 4, 'Unexpected MMIO write'
        offset = address - base
        assert offset in (0x190, 0x194), 'Unexpected GPIO register write'
        assert value & ~FIXED[base] == 0, 'Modified unrelated GPIO mask'
        if offset == 0x194:
            assert self.grants and self.grants[-1][1] == BASES.index(base) + 1, 'Pixel edge without matching bank grant'
            self.gpio[base] |= value
        else:
            self.gpio[base] &= ~value
        assert self.gpio[base] & ~FIXED[base] == self.unrelated[base]
        self.events.append((self.cycles, base, offset, value, self.pc))

    def step(self):
        code = self.image.code
        assert 0 <= self.pc < len(code), 'PC outside instruction RAM'
        ins = code[self.pc]
        kind = ins[0]
        next_pc = self.pc + 1
        # Accelerate only an actual decoded SUB rN,rN,1 / QBNE -1,rN,0 pair.
        # The final failed branch also costs one cycle, so total is exactly 2N.
        if kind == 'alu' and ins[1] == 2 and ins[2] == ins[3] and ins[4:] == (True, 1):
            branch = code[next_pc]
            if branch == ('branch', 13, ins[2], True, 0, self.pc):
                count = self.reg(ins[2])
                assert count > 0, 'Zero delay count would underflow'
                self.put(ins[2], 0)
                self.cycles += 2 * count
                self.pc += 2
                self.fast_loops += 1
                return
        if kind == 'ldi':
            self.put(ins[1], ins[2])
        elif kind == 'alu':
            op, dst, src, immediate, operand = ins[1:]
            a, b = self.reg(src), operand if immediate else self.reg(operand)
            if op == 0:
                value = a + b
            elif op == 2:
                value = a - b
            elif op == 4:
                value = a << (b & 31)
            elif op == 5:
                value = a >> (b & 31)
            elif op == 8:
                value = a & b
            elif op == 9:
                value = a | b
            elif op == 10:
                value = a ^ b
            elif op == 11:
                value = ~a
            elif op == 14:
                value = a & ~(1 << (b & 31))
            else:
                value = a | (1 << (b & 31))
            self.put(dst, value)
        elif kind == 'branch':
            op, src, immediate, operand, target = ins[1:]
            a, b = self.reg(src), operand if immediate else self.reg(operand)
            condition = {9: b < a, 10: b == a, 11: b <= a, 12: b > a,
                         13: b != a, 14: b >= a, 15: True,
                         25: not bool(a & (1 << (b & 31))),
                         26: bool(a & (1 << (b & 31)))}[op]
            if condition:
                next_pc = target
        elif kind == 'jump':
            op, dst, immediate, operand = ins[1:]
            target = operand if immediate else self.reg(operand)
            if op == 17:
                self.put(dst, next_pc)
            next_pc = target
        elif kind == 'burst':
            op, register_byte, base_reg, immediate, operand, count = ins[1:]
            offset = operand if immediate else self.reg(operand)
            if op in (8, 9):
                assert base_reg in (4, 24), 'Unexpected constant-table entry'
                base = 0x26000 if base_reg == 4 else (self.control[0x22020] & 255) * 256
            else:
                base = self.r[base_reg]
            address = base + offset
            if op in (9, 15):
                data = self.read(address, count)
                for index, value in enumerate(data):
                    byte = register_byte + index
                    self.put((byte // 4) | ((byte % 4) << 5), value)
            else:
                data = bytearray(self.reg(((register_byte + i) // 4) |
                                         (((register_byte + i) % 4) << 5)) for i in range(count))
                self.write(address, data)
        else:
            self.halted = True
        self.cycles += 1
        self.pc = next_pc

    def run_until(self, predicate, auto_grant=False):
        steps = 0
        while not predicate() and not self.halted:
            if auto_grant and self.word(56) and self.word(60) != self.word(56):
                self.grant(self.word(56))
            self.step()
            steps += 1
            assert steps < 2000000, 'Execution did not reach target state'
        assert predicate(), 'Unexpected halt/error %x/%d' % (self.word(48), self.word(52))
        assert bytes(self.ram[:36]) == self.original_config

    def run_status(self, status, auto_grant=False):
        self.run_until(lambda: self.word(48) == status, auto_grant)
        assert self.word(48) == status, 'Unexpected status/error %x/%d' % (self.word(48), self.word(52))

    def grant(self, ordinal):
        struct.pack_into('<I', self.ram, 60, ordinal)
        self.grants.append((self.cycles, ordinal, self.word(40)))

    def hold_gate(self, steps=101):
        before_events, before_reads = len(self.events), len(self.reads)
        before_done, before_complete = self.word(64), self.word(44)
        for ignored in range(steps):
            self.step()
        assert len(self.events) == before_events and len(self.reads) == before_reads, 'GPIO activity before gate'
        assert self.word(64) == before_done and self.word(44) == before_complete, 'Completion advanced without gate'
        assert self.word(48) == 4 and self.word(56) != 0, 'PRU did not remain gate-blocked'

    def ready(self):
        self.run_status(1)
        assert self.control[0x26004] & 0x10 == 0, 'OCP remains in standby'
        assert self.control[0x22020] & 255 == 0, 'C24 does not address local RAM zero'
        assert all(event[2] == 0x190 for event in self.events), 'Init emitted data'
        assert self.cycles - self.reads[-1][0] >= settle(self.lengths), 'Initialization settled too early'
        assert all(self.gpio[base] & mask == 0 for base, mask in FIXED.items())
        assert all(self.word(offset) == 0 for offset in (56, 60, 64, 68)), 'Dirty initial gate handshake'

    def send(self, color, sequence, expected=(70, 140, 240)):
        self.events = []
        self.reads = []
        self.grants = []
        self.bank_completions = []
        old_completion = self.word(44)
        struct.pack_into('<I', self.ram, 60, 0)
        struct.pack_into('<II', self.ram, 36, color, sequence)
        published = self.cycles
        self.run_status(2)
        assert self.word(68) == sequence, 'Frame not accepted before RUNNING'
        enabled = [ordinal for ordinal, base in enumerate(BASES, 1)
                   if any(length and pin[0] == base for length, pin in zip(self.lengths, PINS))]
        for ordinal in enabled:
            self.run_until(lambda: self.word(56) == ordinal and self.word(68) == sequence)
            assert self.word(48) == 4, 'Readiness not paired with WAIT_BANK'
            self.hold_gate()
            self.grant(ordinal)
            self.run_until(lambda: self.word(64) == ordinal)
            assert self.word(56) == 0, 'Granted bank still advertised ready'
            assert self.word(44) == old_completion, 'Frame completion before final settle'
        self.run_status(3)
        assert self.word(44) == sequence and self.word(52) == 0, 'Completion sequence/error'
        assert self.word(68) == sequence and self.word(56) == 0, 'Accepted sequence/final ready'
        assert [done[1] for done in self.bank_completions] == enabled, 'Bank completion ordinal/order'
        assert [grant[1] for grant in self.grants] == enabled, 'Missing/repeated grant'
        origin = self.reads[-1][0] if self.reads else published
        assert self.cycles - origin >= settle(self.lengths), 'DONE before full chain-settle wait'
        check_wave(self, color, expected)


def settle(lengths):
    return 60000 + 20000 + max(max(lengths) - 1, 0) * 200


def check_wave(machine, color, expected):
    h0, h1, period = expected
    events = [e for e in machine.events if machine.image.begin <= e[4] < machine.image.end]
    actual_banks = []
    for event in events:
        if not actual_banks or actual_banks[-1] != event[1]:
            actual_banks.append(event[1])
    want_banks = [base for base in BASES if any(length and pin[0] == base
                  for length, pin in zip(machine.lengths, PINS))]
    assert actual_banks == want_banks, 'Bank order / skipped-bank activity'
    assert len(events) % 2 == 0
    for set_event, clear_event in zip(events[0::2], events[1::2]):
        assert set_event[2] == 0x194 and clear_event[2] == 0x190, 'Two-write symbol order'
        assert set_event[1] == clear_event[1] and set_event[3] == clear_event[3] != 0, 'SET/CLEAR masks differ'
    for index, (base, bit) in enumerate(PINS):
        pin_events = [e for e in events if e[1] == base and e[3] & (1 << bit)]
        assert len(pin_events) == 48 * machine.lengths[index], 'Wrong exact length for pin %d' % index
        last_set = None
        for word_bit, (rise, fall) in enumerate(zip(pin_events[0::2], pin_events[1::2])):
            want = h1 if color & (1 << (23 - word_bit % 24)) else h0
            assert fall[0] - rise[0] == want, 'High width at pin %d bit %d: %d != %d' % (index, word_bit, fall[0] - rise[0], want)
            if last_set is not None:
                assert rise[0] - last_set == period, 'Rising-edge period at pin %d bit %d: %d' % (index, word_bit, rise[0] - last_set)
            last_set = rise[0]
    assert all(machine.gpio[base] & mask == 0 for base, mask in FIXED.items()), 'Stuck high at DONE'
    for base in BASES:
        if base not in want_banks:
            assert not any(e[1] == base for e in machine.events), 'Skipped bank received drain/write'


def audit(image):
    frames = 0
    patterns = ([0] * 6, [1] * 6, [300] * 6,
                [300, 180, 7, 1, 3, 0], [0, 1, 180, 7, 300, 300])
    for profile in (1, 2):
        for lengths in patterns:
            machine = Machine(image, lengths, profile)
            machine.ready()
            for seq, color in enumerate((0, 0xffffff, 0x123456, 0xaaaaaa, 0x555555), 1):
                machine.send(color, seq)
                frames += 1
    # BGR variants take the same firmware path; host tests own byte ordering.
    # Exercise representative variant frames rather than duplicate all long
    # traces, which are expensive on the deployed Python 3.2 interpreter.
    for profile in (3, 4):
        machine = Machine(image, [3, 2, 1, 0, 3, 1], profile)
        machine.ready()
        for sequence, color in enumerate((0, 0xffffff, 0x123456), 1):
            machine.send(color, sequence)
            frames += 1
    # Every enable combination; zero-length and same-bank endpoint removal.
    for mask in range(64):
        lengths = [1 + i % 3 if mask & (1 << i) else 0 for i in range(6)]
        machine = Machine(image, lengths)
        machine.ready()
        machine.send(0x800001, 1)
        frames += 1
    for ends in itertools.permutations((1, 2, 3)):
        machine = Machine(image, [ends[0], ends[1], 0, 0, 0, ends[2]])
        machine.ready()
        machine.send(0x800000, 1)
        frames += 1
    # Execute every parity combination using the same generated kernel.
    # Inject only register-loaded profile parameters, never patched code.
    for odd0, odd1, oddbit in itertools.product((0, 1), repeat=3):
        machine = Machine(image, [3, 2, 1, 2, 0, 1])
        machine.ready()
        h0, h1, bit = 70 + odd0, 140 + odd1, 240 + oddbit
        machine.r[20:26] = [(h0 - 4) // 2, (h1 - 4) // 2,
                            (bit - h0 - 6) // 2, (bit - h1 - 6) // 2,
                            (bit - h0 - 18) // 2, (bit - h1 - 18) // 2]
        machine.r[27] = (h0 & 1) | ((h1 & 1) << 1) | (((bit - h0) & 1) << 2) | (((bit - h1) & 1) << 3)
        machine.send(0x555555, 1, (h0, h1, bit))
        frames += 1
    machine = Machine(image, [1, 2, 0, 3, 0, 0])
    machine.ready()
    for sequence in (0xfffffffe, 0xffffffff, 0, 1):
        machine.send(0xffffff, sequence)
        frames += 1
    # A stale prior-frame grant must fail before ANY edge, including when the
    # same single bank would reuse the same ordinal on successive requests.
    for ordinal in (1, 2, 3, 99):
        machine = Machine(image, [1, 0, 0, 0, 0, 0])
        machine.ready()
        machine.events = []
        machine.grant(ordinal)
        struct.pack_into('<II', machine.ram, 36, 0, 1)
        machine.run_status(0x80000000)
        assert machine.word(52) == 10 and not machine.events, 'Stale gate emitted data'
    machine = Machine(image, [1] * 6)
    machine.ready()
    machine.events = []
    struct.pack_into('<II', machine.ram, 36, 0, 1)
    machine.run_until(lambda: machine.word(56) == 1)
    machine.hold_gate(1001)
    machine.grant(3)
    machine.run_status(0x80000000)
    assert machine.word(52) == 10 and not machine.events, 'Out-of-order gate emitted data'
    # An earlier ordinal cannot release a later bank, even when that earlier
    # bank was empty and skipped. A later valid grant still works.
    machine = Machine(image, [0, 0, 0, 1, 0, 0])
    machine.ready()
    machine.events = []
    struct.pack_into('<II', machine.ram, 36, 0, 1)
    machine.run_until(lambda: machine.word(56) == 3)
    machine.grant(2)
    machine.hold_gate(1001)
    machine.grant(3)
    machine.run_status(3)
    check_wave(machine, 0, (70, 140, 240))
    frames += 1
    # Validation errors are executed from the actual firmware entry point.
    for kwargs, error in (({'magic': 0}, 1), ({'abi': 2}, 2), ({'profile': 99}, 3)):
        bad = Machine(image, [1] * 6, **kwargs)
        bad.run_status(0x80000000)
        assert bad.word(52) == error and not bad.events
    for index in range(6):
        for length in (301, 0xffffffff):
            lengths = [0] * 6
            lengths[index] = length
            bad = Machine(image, lengths)
            bad.run_status(0x80000000)
            assert bad.word(52) == 4
        bad = Machine(image, [1] * 6)
        base, bit = PINS[index]
        bad.oe[base] |= 1 << bit
        bad.run_status(0x80000000)
        assert bad.word(52) == 6
    for base in BASES:
        bad = Machine(image, [1] * 6)
        bad.stuck[base] = FIXED[base]
        bad.run_status(0x80000000)
        assert bad.word(52) == 7
        assert len([e for e in bad.events if e[1] == base]) == 8, 'Unbounded init drain'
        bad = Machine(image, [1] * 6)
        bad.ready()
        bad.events = []
        bad.stuck[base] = FIXED[base]
        struct.pack_into('<II', bad.ram, 36, 0, 1)
        bad.run_status(0x80000000, auto_grant=True)
        assert bad.word(52) == 8
        drain = [e for e in bad.events if e[1] == base and not image.begin <= e[4] < image.end]
        assert len(drain) == 8, 'Unbounded bank drain'
    print('PRU audit passed: %d bytes; %d frames; exact lengths/masks/order; 70/140/240 cycles;' % (image.size, frames))
    print('  profiles 1/2: full traces; profiles 3/4 (BGR): representative traces; 64 enable combinations on profile 1;')
    print('  ABI4 per-bank gates; no pre-grant edges; stale/future grants rejected; exact bank-done ordering;')
    print('  all 5 ns parity paths; sequence wrap; init validation; finite drains; Tsettle; no timed reads/interrupts.')
    print('  Nominal issue-time model only: external bus stalls and physical edges require hardware measurement.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    parser.add_argument('listing', help='PASM raw listing generated with -l')
    args = parser.parse_args()
    audit(Image(args.binary, args.listing))


if __name__ == '__main__':
    main()
