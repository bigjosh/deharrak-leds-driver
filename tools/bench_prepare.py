#!/usr/bin/env python3
"""Record and apply reversible BBG runtime settings for the quiet-window bench.

Python 3.2 compatible. Does not alter boot files, services, or installed software.
Run with --apply NEW_STATE.json, then --restore STATE.json after the experiment.
DMA draining and CPU interrupt masking belong to the kernel helper, not this tool.
"""
from __future__ import print_function
import argparse
import json
import os
import platform
import re
import sys
import time

CPU = "/sys/devices/system/cpu/cpu0/cpufreq/"


def read(path):
    with open(path) as stream:
        return stream.read().strip()


def write(path, value):
    with open(path, "w") as stream:
        stream.write(value + "\n")


def save(path, state):
    temporary = path + ".tmp"
    with open(temporary, "w") as stream:
        json.dump(state, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)


def trigger(path):
    matches = re.findall(r"\[([^]]+)\]", read(path))
    if len(matches) != 1:
        raise RuntimeError("cannot identify selected LED trigger: " + path)
    return matches[0]


def apply(path):
    if os.path.exists(path):
        raise RuntimeError("state path already exists; never overwrite saved settings")
    if platform.release() != "3.8.13-bone80" or read("/sys/devices/system/cpu/online") != "0":
        raise RuntimeError("expected single-online-CPU reference BBG kernel")
    if len(read("/proc/swaps").splitlines()) != 1:
        raise RuntimeError("swap must be absent before this bench")
    if "1000000" not in read(CPU + "scaling_available_frequencies").split():
        raise RuntimeError("reference 1 GHz frequency unavailable")
    state = {"schema": 1, "created_unix": time.time(), "kernel": platform.release(),
             "cpu": {}, "leds": [], "applied": False, "restored": False}
    for name in ("scaling_governor", "scaling_min_freq", "scaling_max_freq"):
        state["cpu"][name] = read(CPU + name)
    for name in sorted(os.listdir("/sys/class/leds")):
        if not re.match(r"^beaglebone:green:usr[0-3]$", name):
            continue
        base = "/sys/class/leds/" + name + "/"
        state["leds"].append({"name": name, "trigger": trigger(base + "trigger"),
                              "brightness": read(base + "brightness")})
    if len(state["leds"]) != 4:
        raise RuntimeError("expected exactly four BBG user LEDs")
    save(path, state)  # Recovery information is durable before the first write.
    write(CPU + "scaling_max_freq", "1000000")
    write(CPU + "scaling_min_freq", "1000000")
    write(CPU + "scaling_governor", "performance")
    for led in state["leds"]:
        base = "/sys/class/leds/" + led["name"] + "/"
        write(base + "trigger", "none")
        write(base + "brightness", "0")
    for name in ("scaling_min_freq", "scaling_max_freq", "scaling_cur_freq"):
        if read(CPU + name) != "1000000":
            raise RuntimeError("fixed 1 GHz verification failed: " + name)
    state["applied"] = True
    state["applied_unix"] = time.time()
    save(path, state)
    print("Prepared: fixed 1 GHz, four user LED triggers off, no swap; state=" + path)


def restore(path):
    with open(path) as stream:
        state = json.load(stream)
    if state.get("schema") != 1 or state.get("kernel") != platform.release():
        raise RuntimeError("invalid or wrong-kernel state file")
    if state.get("restored"):
        print("Settings already restored")
        return
    cpu = state["cpu"]
    # Lower minimum first so restoring the prior maximum never conflicts.
    write(CPU + "scaling_min_freq", cpu["scaling_min_freq"])
    write(CPU + "scaling_max_freq", cpu["scaling_max_freq"])
    write(CPU + "scaling_governor", cpu["scaling_governor"])
    for led in state["leds"]:
        if not re.match(r"^beaglebone:green:usr[0-3]$", led["name"]):
            raise RuntimeError("unexpected LED name in state")
        base = "/sys/class/leds/" + led["name"] + "/"
        write(base + "trigger", "none")
        write(base + "brightness", led["brightness"])
        write(base + "trigger", led["trigger"])
    state["restored"] = True
    state["restored_unix"] = time.time()
    save(path, state)
    print("Restored recorded CPU policy and user LED settings")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply")
    group.add_argument("--restore")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("requires root on the BBG")
    try:
        if args.apply:
            apply(os.path.abspath(args.apply))
        else:
            restore(os.path.abspath(args.restore))
    except Exception as error:
        print("bench preparation failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
