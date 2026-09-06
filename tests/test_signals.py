#!/usr/bin/env python3
"""Explicit, bounded live SIGKILL/SIGTERM tests for a published dld-send.

Python 3.2 compatible. The operator must establish exclusive DLD hardware use
first. All sends are black; service management is outside this script.
"""
from __future__ import print_function

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

# Importing shared test helpers must not add artifacts outside the build folder.
sys.dont_write_bytecode = True
from test_hardware import LiveTests, TestFailure, DONE


class SignalTests(LiveTests):
    def interrupted_send(self, signal_number, delay_ms, expected_exit):
        self.commands += 1
        label = "SIGKILL sender" if signal_number == signal.SIGKILL else "SIGTERM sender"
        prefix = os.path.join(self.directory, "%03d" % self.commands)
        argv = [self.send, "000000"]
        started = time.time()
        expired = [False]
        sent = False
        before_signal = None
        sent_at = None
        with open(prefix + ".stdout", "wb") as output, open(prefix + ".stderr", "wb") as error:
            process = subprocess.Popen(argv, cwd=self.directory, stdout=output,
                                       stderr=error, close_fds=True)

            def expire():
                if process.poll() is None:
                    expired[0] = True
                    try:
                        process.kill()
                    except OSError:
                        pass

            watchdog = threading.Timer(15.0, expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                # Popen has returned after exec. This delay is a scheduling
                # heuristic, not evidence of publication; the mailbox below
                # must independently prove request sequence advancement.
                time.sleep(delay_ms / 1000.0)
                before_signal = process.poll()
                if before_signal is None:
                    sent_at = time.time()
                    process.send_signal(signal_number)
                    sent = True
                status = process.wait()
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                raise
            finally:
                watchdog.cancel()
                watchdog.join()
        with open(prefix + ".stdout", "rb") as stream:
            output_text = stream.read().decode("utf-8", "replace")
        with open(prefix + ".stderr", "rb") as stream:
            error_text = stream.read().decode("utf-8", "replace")
        self.record({"label": label, "argv": argv, "signal": signal_number,
                     "requested_delay_ms": delay_ms, "exit_before_signal": before_signal,
                     "signal_sent": sent, "signal_elapsed_seconds":
                     None if sent_at is None else sent_at - started,
                     "exit": status, "elapsed_seconds": time.time() - started,
                     "watchdog_expired": expired[0], "stdout": output_text,
                     "stderr": error_text})
        self.check(not expired[0], label + ": child exceeded the 15-second harness deadline")
        self.check(sent and before_signal is None,
                   label + ": child exited before the signal; scheduling race, no signal test established")
        self.check(status == expected_exit,
                   "%s: expected exit %d, got %d: %s" %
                   (label, expected_exit, status, error_text.strip()))
        self.check(not output_text, label + ": child printed success before interruption")
        if signal_number == signal.SIGTERM:
            self.check("CRITICAL" in error_text and "cancelled" in error_text,
                       "SIGTERM: missing critical cancellation diagnostic")
        else:
            self.check(not error_text, "SIGKILL: unexpected pre-exit diagnostic: " + error_text.strip())

    def signal_suite(self, delay_ms):
        profile = "ws2812b"
        lengths = [300] * 6
        maximum = self.configuration("signals-maximum", profile, lengths)
        zero = self.configuration("signals-zero", profile, [0] * 6)

        before = self.initialize(maximum, profile, lengths)
        sequence = (before["request_seq"] + 1) & 0xffffffff
        self.interrupted_send(signal.SIGKILL, delay_ms, -signal.SIGKILL)
        # The sender has exited and released its lock. Give its autonomous PRU
        # request one fixed observation interval, without invoking another send
        # or init. This test wait is not a product retry/completion grace period.
        time.sleep(0.100)
        after = self.snapshot("SIGKILL independent completion")
        self.check(after["request_seq"] == sequence,
                   "SIGKILL: request was not published before death; scheduling race, no orphan test established")
        self.initialized(after, profile, lengths, DONE, sequence, "SIGKILL independent completion")
        self.check(after["wire_color"] == 0, "SIGKILL request was not black")
        self.gpio_preserved(before, after, "SIGKILL independent completion")
        self.successful_send(after, profile, lengths, "000000")
        print("PASS SIGKILL: published request completed; next send succeeded without init")

        before = self.initialize(maximum, profile, lengths)
        sequence = (before["request_seq"] + 1) & 0xffffffff
        self.interrupted_send(signal.SIGTERM, delay_ms, 5)
        after = self.snapshot("SIGTERM cleanup")
        self.check(after["request_seq"] == sequence,
                   "SIGTERM: request was not published; scheduling race, no submitted-cancellation test established")
        self.check(after["magic"] == 0, "SIGTERM cleanup did not invalidate the mailbox")
        self.check(all((value & (2 | 0x8000)) == 0 for value in after["pru_control"]),
                   "SIGTERM cleanup did not leave both PRUs stopped and disabled")
        self.check(after["profile_id"] == before["profile_id"] and after["string_lengths"] == lengths,
                   "SIGTERM cleanup corrupted retained configuration fields")
        self.outputs_low(after, "SIGTERM cleanup")
        self.gpio_preserved(before, after, "SIGTERM cleanup")
        self.rejected_unchanged("SIGTERM requires explicit init", [self.send, "000000"], 7)
        recovered = self.initialize(maximum, profile, lengths)
        self.successful_send(recovered, profile, lengths, "000000")
        self.initialize(zero, profile, [0] * 6)
        print("PASS SIGTERM: critical cleanup, invalidation, and explicit init recovery")
        self.record({"result": "PASS", "suite": "sender signals", "checks": self.checks,
                     "commands": self.commands, "signal_delay_ms": delay_ms,
                     "final_state": "READY, ws2812b, six zero lengths",
                     "limits": "Publication and host interruption verified; exact PRU phase at signal and waveform timing not established"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--allow-kernel-user-leds", action="store_true",
                        help="exclude only GPIO1 DATAOUT bits 21-24 from observations, with logging")
    parser.add_argument("--signal-delay-ms", type=int, default=20,
                        help="delay after child exec before signalling, 1..50 ms (default: 20)")
    parser.add_argument("build_directory", help="absolute isolated project build directory")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required; no hardware was accessed")
    if not 1 <= args.signal_delay_ms <= 50:
        parser.error("--signal-delay-ms must be 1..50")
    if not os.path.isabs(args.build_directory):
        parser.error("build_directory must be absolute")
    build_dir = os.path.realpath(args.build_directory)
    if not os.path.isdir(build_dir) or os.geteuid() != 0:
        parser.error("an existing isolated build directory and root are required")
    for name in ["dld-init", "dld-send", "hw-probe"]:
        if not os.access(os.path.join(build_dir, name), os.X_OK):
            parser.error("missing executable " + os.path.join(build_dir, name))
    lock_path = subprocess.check_output([os.path.join(build_dir, "hw-probe"), "--lock-path"]).decode("utf-8").strip()
    if not os.path.isabs(lock_path) or not os.path.realpath(lock_path).startswith(os.path.dirname(build_dir) + os.sep):
        parser.error("all commands must use the same absolute project-local DLD_LOCK_PATH")
    tests = SignalTests(build_dir, lock_path, args.allow_kernel_user_leds)
    print("Signal test artifacts: " + tests.directory)
    try:
        tests.signal_suite(args.signal_delay_ms)
    except (TestFailure, OSError, ValueError, KeyboardInterrupt) as error:
        tests.record({"result": "FAIL", "suite": "sender signals", "error": str(error),
                      "checks": tests.checks, "commands": tests.commands})
        print("FAIL: %s\nStopped; hardware state is left for operator recovery.\nArtifacts: %s" %
              (error, tests.directory), file=sys.stderr)
        return 1
    finally:
        tests.log.close()
    print("PASS sender signals: %d checks, %d commands" % (tests.checks, tests.commands))
    return 0


if __name__ == "__main__":
    sys.exit(main())
