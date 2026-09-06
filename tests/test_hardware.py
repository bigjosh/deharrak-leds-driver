#!/usr/bin/env python3
"""Explicit live integration checks; compatible with Python 3.2.

The operator owns LEDscape shutdown/restart. This script does not manage services
or edit existing system files. Do not invoke it during normal display operation.
"""
from __future__ import print_function

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time


MAGIC = 0x444c4431
ABI = 4
READY, RUNNING, DONE, WAIT_BANK = 1, 2, 3, 4
PROFILE_IDS = {"ws2812b": 1, "ws2811-hs": 2, "ws2812b-bgr": 3, "ws2811-hs-bgr": 4}
MASKS = [1 << 26, (1 << 12) | (1 << 14), (1 << 1) | (1 << 3) | (1 << 4)]
KERNEL_USER_LED_MASKS = [0, (1 << 21) | (1 << 22) | (1 << 23) | (1 << 24), 0]
MAILBOX_FIELDS = ["magic", "abi_version", "profile_id", "string_lengths",
                  "wire_color", "request_seq", "completion_seq", "status", "error_detail",
                  "bank_ready", "bank_grant", "bank_done", "accepted_seq"]


class TestFailure(Exception):
    pass


class LiveTests(object):
    def __init__(self, build_dir, lock_path, allow_kernel_user_leds=False):
        self.build_dir = build_dir
        self.lock_path = lock_path
        self.directory = tempfile.mkdtemp(prefix="hardware-test-", dir=build_dir)
        self.log = open(os.path.join(self.directory, "results.jsonl"), "w")
        self.checks = 0
        self.commands = 0
        self.init = os.path.join(build_dir, "dld-init")
        self.send = os.path.join(build_dir, "dld-send")
        self.probe = os.path.join(build_dir, "hw-probe")
        self.excluded_dataout = KERNEL_USER_LED_MASKS[:] if allow_kernel_user_leds else [0, 0, 0]
        self.record({"allow_kernel_user_leds": allow_kernel_user_leds,
                     "excluded_gpio_dataout_masks": self.excluded_dataout,
                     "exclusion_scope": "No hardware preservation claim for excluded DATAOUT bits; all OE bits remain checked"})

    def record(self, item):
        self.log.write(json.dumps(item, sort_keys=True) + "\n")
        self.log.flush()

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.record({"failure": message, "check": self.checks})
            raise TestFailure(message)

    def run(self, label, argv, expected=0):
        self.commands += 1
        prefix = os.path.join(self.directory, "%03d" % self.commands)
        started = time.time()
        expired = [False]
        with open(prefix + ".stdout", "wb") as output, open(prefix + ".stderr", "wb") as error:
            process = subprocess.Popen(argv, cwd=self.directory, stdout=output,
                                       stderr=error, close_fds=True)

            def expire():
                # A harness watchdog, not a product completion grace period or
                # a cancellation test. Stop the suite after any such timeout.
                if process.poll() is not None:
                    return
                expired[0] = True
                try:
                    process.terminate()
                    time.sleep(1)
                    if process.poll() is None:
                        process.kill()
                except OSError:
                    pass

            watchdog = threading.Timer(15.0, expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                status = process.wait()
            except BaseException:
                # Keep the existing watchdog armed while a caught interrupt
                # allows the current child to perform its normal cleanup.
                if process.poll() is None:
                    process.terminate()
                    process.wait()
                raise
            finally:
                watchdog.cancel()
                watchdog.join()
        with open(prefix + ".stdout", "rb") as stream:
            output_text = stream.read().decode("utf-8", "replace")
        with open(prefix + ".stderr", "rb") as stream:
            error_text = stream.read().decode("utf-8", "replace")
        self.record({"label": label, "argv": argv, "exit": status,
                     "elapsed_seconds": time.time() - started,
                     "watchdog_expired": expired[0], "stdout": output_text,
                     "stderr": error_text})
        self.check(not expired[0], label + ": test command exceeded 15 seconds")
        self.check(status == expected, "%s: expected exit %d, got %d: %s" %
                   (label, expected, status, error_text.strip()))
        if expected == 0:
            self.check(not error_text, label + ": unexpected stderr: " + error_text.strip())
        else:
            self.check(bool(error_text.strip()), label + ": missing diagnostic")
            self.check(not output_text, label + ": rejection printed a success/output line")
        return output_text

    def snapshot(self, label):
        return json.loads(self.run(label, [self.probe, "snapshot"]))

    def fault(self, action):
        return json.loads(self.run(action, [self.probe, "--run-live", action]))

    def configuration(self, name, profile, lengths):
        path = os.path.join(self.directory, name + ".json")
        with open(path, "w") as stream:
            json.dump({"pixel_type": profile, "string_lengths": lengths}, stream)
            stream.write("\n")
        return path

    def gpio_preserved(self, before, after, label):
        self.check(before["gpio_led_masks"] == MASKS and after["gpio_led_masks"] == MASKS,
                   label + ": probe GPIO mask mismatch")
        for bank in range(3):
            unrelated = (~MASKS[bank]) & 0xffffffff
            changed = (before["gpio_oe"][bank] ^ after["gpio_oe"][bank]) & unrelated
            self.check(changed == 0,
                       "%s: unrelated GPIO%d OE changed by 0x%08x; investigate external writers" %
                       (label, bank, changed))
            self.dataout_preserved(before, after, bank, unrelated, label)

    def dataout_preserved(self, before, after, bank, checked_mask, label):
        changed = before["gpio_dataout"][bank] ^ after["gpio_dataout"][bank]
        excluded = self.excluded_dataout[bank]
        observed_excluded = changed & excluded
        if observed_excluded:
            self.record({"observation": "excluded kernel user-LED DATAOUT difference",
                         "label": label, "gpio_bank": bank,
                         "excluded_mask": excluded, "observed_difference": observed_excluded,
                         "preservation_claim": False})
        unexpected = changed & checked_mask & (~excluded & 0xffffffff)
        self.check(unexpected == 0,
                   "%s: GPIO%d DATAOUT changed by 0x%08x outside the explicit exclusion; investigate external writers" %
                   (label, bank, unexpected))

    def outputs_low(self, state, label):
        for bank in range(3):
            self.check((state["gpio_oe"][bank] & MASKS[bank]) == 0,
                       "%s: GPIO%d LED direction is input" % (label, bank))
            self.check((state["gpio_dataout"][bank] & MASKS[bank]) == 0,
                       "%s: GPIO%d LED output latch is high" % (label, bank))

    def initialized(self, state, profile, lengths, status, sequence, label):
        profile_id = PROFILE_IDS[profile]
        self.check(state["magic"] == MAGIC and state["abi_version"] == ABI,
                   label + ": incorrect initialized ABI")
        self.check(state["profile_id"] == profile_id and state["string_lengths"] == lengths,
                   label + ": initialized configuration differs")
        self.check(state["status"] == status and state["error_detail"] == 0,
                   label + ": unexpected status or error")
        self.check(state["request_seq"] == sequence and state["completion_seq"] == sequence,
                   label + ": request/completion sequence mismatch")
        last_bank = 0
        if status == DONE:
            for ordinal, pins in ((1, (0, 1, 5)), (2, (2, 4)), (3, (3,))):
                if any(lengths[pin] for pin in pins):
                    last_bank = ordinal
        self.check(state["bank_ready"] == 0 and state["accepted_seq"] == sequence and
                   state["bank_grant"] == last_bank and state["bank_done"] == last_bank,
                   label + ": inconsistent completed bank gates")
        self.check((state["pru_control"][0] & 2) != 0 and (state["pru_control"][1] & 2) == 0,
                   label + ": expected enabled PRU0 and disabled PRU1")
        self.outputs_low(state, label)

    def unchanged(self, before, after, label):
        for field in MAILBOX_FIELDS:
            self.check(before[field] == after[field], label + ": changed mailbox " + field)
        self.check([value & 2 for value in before["pru_control"]] ==
                   [value & 2 for value in after["pru_control"]],
                   label + ": changed PRU enable state")
        self.check(before["gpio_oe"] == after["gpio_oe"], label + ": changed GPIO direction")
        for bank in range(3):
            self.dataout_preserved(before, after, bank, 0xffffffff, label)

    def initialize(self, path, profile, lengths):
        before = self.snapshot("before init")
        output = self.run("initialize " + os.path.basename(path), [self.init, path])
        self.check(output.startswith("OK "), "init: missing success line")
        after = self.snapshot("after init")
        self.initialized(after, profile, lengths, READY, 0, "init")
        self.check(after["wire_color"] == 0, "init: wire color was not cleared")
        self.gpio_preserved(before, after, "init")
        return after

    def successful_send(self, before, profile, lengths, color):
        output = self.run("send " + color, [self.send, color])
        self.check(output.startswith("OK "), "send: missing success line")
        after = self.snapshot("after send " + color)
        sequence = (before["request_seq"] + 1) & 0xffffffff
        self.initialized(after, profile, lengths, DONE, sequence, "send")
        rgb = int(color, 16)
        if profile == "ws2812b":
            wire = ((rgb & 0xff00) << 8) | ((rgb & 0xff0000) >> 8) | (rgb & 0xff)
        elif profile.endswith("-bgr"):
            wire = ((rgb & 0xff) << 16) | (rgb & 0xff00) | ((rgb >> 16) & 0xff)
        else:
            wire = rgb
        self.check(after["wire_color"] == wire, "send: incorrect wire color")
        self.gpio_preserved(before, after, "send")
        return after

    def rejected_unchanged(self, label, argv, code):
        before = self.snapshot("before " + label)
        self.run(label, argv, code)
        after = self.snapshot("after " + label)
        self.unchanged(before, after, label)
        return after

    def suite(self):
        zero = [0] * 6
        profiles = [
            ("zero", "ws2812b", zero),
            ("unequal", "ws2812b", [1, 2, 0, 3, 4, 5]),
            ("maximum", "ws2812b", [300] * 6),
            ("ws2811", "ws2811-hs", [1, 0, 2, 3, 4, 5]),
            ("ws2812b-bgr", "ws2812b-bgr", [1, 0, 0, 0, 0, 0]),
            ("ws2811-bgr", "ws2811-hs-bgr", [1, 0, 0, 0, 0, 0])]
        paths = {}
        for name, profile, lengths in profiles:
            path = self.configuration(name, profile, lengths)
            paths[name] = path
            state = self.initialize(path, profile, lengths)
            for color in ["010203", "0x030201", "000000"]:
                state = self.successful_send(state, profile, lengths, color)
            print("PASS lifecycle: " + name)

        invalid = os.path.join(self.directory, "invalid.json")
        with open(invalid, "w") as stream:
            stream.write('{"pixel_type":"ws2812b","string_lengths":[301,0,0,0,0,0]}\n')
        self.rejected_unchanged("malformed color", [self.send, "12345g"], 2)
        self.rejected_unchanged("malformed init", [self.init, invalid], 2)
        before = self.snapshot("before command lock contention")
        with open(self.lock_path, "a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.run("locked send", [self.send, "010203"], 6)
                self.run("locked init", [self.init, paths["zero"]], 6)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self.unchanged(before, self.snapshot("after command lock contention"), "lock contention")
        print("PASS malformed-input and lock rejection preserve state")

        # Stop an idle firmware instance without changing retained READY/DONE.
        # The product has deliberately no liveness probe: publication proceeds
        # and its one completion check must fail, clean up, and require init.
        for idle_status in [READY, DONE]:
            state = self.initialize(paths["zero"], "ws2812b", zero)
            if idle_status == DONE:
                state = self.successful_send(state, "ws2812b", zero, "010203")
            stopped = self.fault("stop-pru0")
            for field in MAILBOX_FIELDS:
                self.check(stopped[field] == state[field], "stop-pru0 changed mailbox " + field)
            self.check((stopped["pru_control"][0] & 2) == 0, "PRU0 did not stop")
            self.gpio_preserved(state, stopped, "stop idle PRU0")
            self.run("stopped idle send is critical", [self.send, "020304"], 5)
            failed = self.snapshot("after critical send cleanup")
            self.check(failed["magic"] == 0, "critical cleanup did not invalidate magic")
            self.check(all((value & 2) == 0 for value in failed["pru_control"]),
                       "critical cleanup did not disable both PRUs")
            self.check(failed["request_seq"] == ((state["request_seq"] + 1) & 0xffffffff),
                       "stopped idle send did not publish a new request")
            self.check(failed["completion_seq"] == state["completion_seq"],
                       "stopped PRU changed completion sequence")
            self.outputs_low(failed, "critical cleanup")
            self.gpio_preserved(stopped, failed, "critical cleanup")
            self.rejected_unchanged("send requires reinitialization", [self.send, "010203"], 7)
            self.initialize(paths["zero"], "ws2812b", zero)
        print("PASS stopped READY/DONE causes critical cleanup and explicit recovery")

        # Keep the stopped PRU and synthetic outstanding request untouched.
        # A retry must remain busy; only the explicitly issued init recovers it.
        cases = [(READY, "inject-outstanding"), (DONE, "inject-outstanding"),
                 (DONE, "inject-running")]
        for idle_status, injection in cases:
            state = self.initialize(paths["zero"], "ws2812b", zero)
            if idle_status == DONE:
                state = self.successful_send(state, "ws2812b", zero, "010203")
            self.fault("stop-pru0")
            busy = self.fault(injection)
            self.check(busy["request_seq"] != busy["completion_seq"], "injection is not outstanding")
            self.check(busy["status"] == (RUNNING if injection == "inject-running" else idle_status),
                       "incorrect injected status")
            self.rejected_unchanged("outstanding stopped request", [self.send, "010203"], 6)
            self.rejected_unchanged("outstanding stopped request remains busy", [self.send, "010203"], 6)
            self.initialize(paths["zero"], "ws2812b", zero)
        print("PASS stopped outstanding requests stay busy without cleanup")

        # Publish a real ABI4 request while deliberately withholding its first
        # bank grant. No kernel ioctl or quiet window is entered. The PRU must
        # remain at the gate; a later sender must reject busy without cleanup.
        for ordinal, lengths in ((1, [1, 0, 0, 0, 0, 0]),
                                 (2, [0, 0, 1, 0, 0, 0]),
                                 (3, [0, 0, 0, 1, 0, 0])):
            path = self.configuration("withheld-gate-%d" % ordinal, "ws2812b", lengths)
            state = self.initialize(path, "ws2812b", lengths)
            waiting = self.fault("withhold-first-grant")
            sequence = (state["request_seq"] + 1) & 0xffffffff
            self.check(waiting["status"] == WAIT_BANK and waiting["bank_ready"] == ordinal,
                       "withheld request did not reach its fixed bank ordinal")
            self.check(waiting["accepted_seq"] == sequence and waiting["request_seq"] == sequence and
                       waiting["completion_seq"] == state["completion_seq"], "withheld request sequence mismatch")
            self.check(waiting["bank_grant"] == 0 and waiting["bank_done"] == 0,
                       "withheld request advanced its gate")
            self.check((waiting["pru_control"][0] & 2) != 0, "withheld request stopped PRU0")
            self.outputs_low(waiting, "withheld first gate")
            self.gpio_preserved(state, waiting, "withheld first gate")
            time.sleep(0.02)
            self.unchanged(waiting, self.snapshot("withheld gate remains idle"), "withheld gate remains idle")
            self.rejected_unchanged("withheld gate send stays busy", [self.send, "010203"], 6)
            self.initialize(paths["zero"], "ws2812b", zero)
        print("PASS all three withheld first-bank grants remain low and busy until explicit init")
        self.record({"result": "PASS", "checks": self.checks, "commands": self.commands,
                     "final_state": "READY, ws2812b, six zero lengths; both latches and directions verified"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true",
                        help="perform the explicitly authorized live hardware tests")
    parser.add_argument("--allow-kernel-user-leds", action="store_true",
                        help="exclude only GPIO1 DATAOUT bits 21-24 from observational comparisons")
    parser.add_argument("build_directory", help="absolute path to the isolated project's build directory")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required; no hardware was accessed")
    if not os.path.isabs(args.build_directory):
        parser.error("build_directory must be absolute")
    build_dir = os.path.realpath(args.build_directory)
    if not os.path.isdir(build_dir) or os.geteuid() != 0:
        parser.error("an existing isolated build directory and root are required")
    for name in ["dld-init", "dld-send", "hw-probe"]:
        if not os.access(os.path.join(build_dir, name), os.X_OK):
            parser.error("missing executable " + os.path.join(build_dir, name))
    # This helper option prints a compile-time string and opens no devices.
    lock_path = subprocess.check_output([os.path.join(build_dir, "hw-probe"), "--lock-path"]).decode("utf-8").strip()
    project = os.path.dirname(build_dir)
    if not os.path.isabs(lock_path) or not os.path.realpath(lock_path).startswith(project + os.sep):
        parser.error("rebuild all three commands with the same absolute project-local DLD_LOCK_PATH")
    tests = LiveTests(build_dir, lock_path, args.allow_kernel_user_leds)
    print("Live test artifacts: " + tests.directory)
    if args.allow_kernel_user_leds:
        print("Observation excludes kernel user-LED GPIO1 DATAOUT bits 21-24; changes are logged")
    try:
        tests.suite()
    except (TestFailure, OSError, ValueError, KeyboardInterrupt) as error:
        tests.record({"result": "FAIL", "error": str(error), "checks": tests.checks,
                      "commands": tests.commands})
        print("FAIL: %s\nStopped; hardware state and logs are left for operator recovery.\nArtifacts: %s" %
              (error, tests.directory), file=sys.stderr)
        return 1
    finally:
        tests.log.close()
    print("PASS live hardware: %d checks, %d commands; final configuration has six zero lengths" %
          (tests.checks, tests.commands))
    return 0


if __name__ == "__main__":
    sys.exit(main())
