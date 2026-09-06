#!/usr/bin/env python3
"""Audit native objdump -dr output; Python 3.2 compatible, no hardware access."""
from __future__ import print_function

import argparse
import copy
import json
import re
import sys


class AuditError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def parse(text):
    labels, words, relocations, halfwords, relocation_types = {}, {}, [], {}, []
    active = False
    for line in text.splitlines():
        typed = re.search(r"R_ARM_(\w+)\s+(\S+)", line)
        if typed:
            relocation_types.append((typed.group(1), typed.group(2)))
        section = re.match(r"Disassembly of section ([^:]+):", line)
        if section:
            active = section.group(1) == ".text"
            continue
        if not active:
            continue
        label = re.match(r"^([0-9a-fA-F]+) <([^>]+)>:$", line)
        if label:
            labels[label.group(2)] = int(label.group(1), 16)
            continue
        instruction = re.match(r"^\s*([0-9a-fA-F]+):\s+([0-9a-fA-F]{8})\s", line)
        if instruction:
            words[int(instruction.group(1), 16)] = int(instruction.group(2), 16)
        halfword = re.match(r"^\s*([0-9a-fA-F]+):\s+([0-9a-fA-F]{4})\s", line)
        if halfword:
            halfwords[int(halfword.group(1), 16)] = int(halfword.group(2), 16)
        relocation = re.match(r"^\s*([0-9a-fA-F]+):\s+R_ARM_", line)
        if relocation:
            relocations.append(int(relocation.group(1), 16))
    return labels, words, relocations, halfwords, relocation_types


def branch_word(address, target, condition):
    displacement = target - address - 8
    require(displacement % 4 == 0, "unaligned branch target")
    return (condition << 28) | 0x0a000000 | ((displacement // 4) & 0xffffff)


def audit(parsed):
    labels, words, relocations, halfwords, relocation_types = parsed
    require(not any(kind in ("CALL", "JUMP24", "PC24") for kind, target in relocation_types),
            "ARM call relocation is incompatible with the Thumb2 kernel loader")
    require(("THM_CALL", "dld_quiet_bank_entry") in relocation_types,
            "C must call the Thumb entry veneer using THM_CALL")
    names = ["bank_entry", "bank", "grant_begin", "timer_start", "loop_begin", "loop_end", "observe"]
    for name in names:
        require("dld_quiet_" + name in labels, "missing symbol dld_quiet_" + name)
    entry = labels["dld_quiet_bank"]
    veneer = labels["dld_quiet_bank_entry"]
    grant = labels["dld_quiet_grant_begin"]
    require(veneer % 64 == 0 and entry == veneer + 64, "unexpected Thumb veneer layout")
    require(halfwords.get(veneer) == 0x4778 and halfwords.get(veneer + 2) == 0xbf00,
            "Thumb veneer must be BX PC followed by NOP")
    require(words.get(veneer + 4) == branch_word(veneer + 4, entry, 14),
            "veneer must branch locally to ARM body")
    require(grant % 64 == 0, "grant cache line is not 64-byte aligned")
    require(grant == entry + 64, "unexpected warmup layout")
    for name, offset in [("timer_start", 12), ("loop_begin", 16), ("loop_end", 44), ("observe", 76)]:
        require(labels["dld_quiet_" + name] == grant + offset, "unexpected " + name + " position")
    require(not any(veneer <= x <= grant + 100 for x in relocations),
            "loop/warmup has an unresolved relocation")

    expected_entry = [0xe92d41f0, 0xe1a06000, 0xe1a07002, 0xe1a08003,
                      0xe3a00000, 0xe3a02010, 0xe3a03040,
                      branch_word(entry + 28, grant + 12, 14)]
    expected_entry += [0xe320f000] * 8
    # Exact ARM words make changes to memory addresses, PMU registers, stack
    # traffic, conditional branches or the independent iteration cap fail.
    expected_grant = [
        0xf57ff04f, 0xe586103c, 0xf57ff04f, 0xee194f1d,
        0xee195f1d, 0xe0455004, 0xe1550002,
        branch_word(grant + 28, grant + 44, 2),
        0xe2533001, branch_word(grant + 36, grant + 16, 1), 0xe3855102,
        0xe3500000, branch_word(grant + 48, grant + 76, 1), 0xe3150102,
        branch_word(grant + 56, grant + 92, 1), 0xe3a00001,
        0xe1a02007, 0xe1a03008, branch_word(grant + 72, grant, 14),
        0xf57ff05f, 0xe5960040, 0xe1a01005, 0xe8bd81f0,
        0xe3a00000, 0xe3851101, 0xe8bd81f0]
    for base, expected in [(entry, expected_entry), (grant, expected_grant)]:
        for index, word in enumerate(expected):
            address = base + index * 4
            require(words.get(address) == word,
                    "unexpected ARM instruction at 0x%x: expected %08x, got %s" %
                    (address, word, "%08x" % words[address] if address in words else "missing"))
    return {"status": "pass", "grant_address": grant, "cache_line_bytes": 64,
            "timing_loop_instructions": 7, "quiet_loop_data_accesses": 0,
            "grant_stores": 1, "completion_loads": 1,
            "pmu_register": "CP15 c9,c13,0", "independent_iteration_guard": True,
            "same_loop_warmup": True, "quiet_region_relocations": 0,
            "thumb2_kernel_entry": True, "arm_call_relocations": 0}


def self_test(parsed):
    audit(parsed)
    grant = parsed[0]["dld_quiet_grant_begin"]
    entry = parsed[0]["dld_quiet_bank"]
    rejected = 0
    mutations = [(grant + 16, 0xe5960040), (grant + 32, 0xe320f000),
                 (grant + 4, 0xe5861040), (grant + 80, 0xe596003c),
                 (grant + 16, 0xee195f3d), (entry + 28, 0xe320f000),
                 (grant + 36, branch_word(grant + 36, grant + 36, 1)),
                 (grant + 20, 0xe92d4000)]
    for address, word in mutations:
        altered = copy.deepcopy(parsed)
        altered[1][address] = word
        try:
            audit(altered)
        except AuditError:
            rejected += 1
    altered = copy.deepcopy(parsed)
    altered[2].append(grant + 36)
    try:
        audit(altered)
    except AuditError:
        rejected += 1
    altered = copy.deepcopy(parsed)
    altered[3][parsed[0]["dld_quiet_bank_entry"]] = 0x4700
    try:
        audit(altered)
    except AuditError:
        rejected += 1
    altered = copy.deepcopy(parsed)
    altered[4].append(("CALL", "__sw_hweight32"))
    try:
        audit(altered)
    except AuditError:
        rejected += 1
    require(rejected == 11, "audit failed to reject a dangerous mutation")
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disassembly")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        with open(args.disassembly, "r") as stream:
            parsed = parse(stream.read())
        result = audit(parsed)
        if args.self_test:
            result["rejected_dangerous_mutations"] = self_test(parsed)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AuditError, IOError, ValueError) as error:
        print("quiet-window assembly audit failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
