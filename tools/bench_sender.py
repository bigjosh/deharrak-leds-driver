#!/usr/bin/env python3
"""Bounded test traffic through the existing dld-init/dld-send CLIs.

Python 3.2 compatible. The operator establishes exclusive hardware ownership.
This script never manages services or accesses driver memory. Each run writes
only to a NEW explicit output directory. Stop-file and caught signals stop
between commands, allowing an in-flight send to finish normally. A command
timeout is a failure: terminate, then kill if necessary, and never retry.

successes.bin starts with the eight bytes DLDLOG1\\0, followed by one <dd
record per successful send: Unix start/end timestamps in seconds. Its zero-based
record index selects the color from manifest.json. These host timestamps are
bookkeeping, not physical edge measurements. Records are flushed each send and
fsynced at progress checkpoints/finalization; abrupt power loss can lose the
uncommitted tail. Partial trailing records must be ignored by readers.
"""
from __future__ import print_function

import argparse
import calendar
import ctypes
import datetime
import errno
import json
import math
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time


PROFILES = ("ws2812b", "ws2811-hs", "ws2812b-bgr", "ws2811-hs-bgr")
FIXED_CYCLE = [0x000000, 0xffffff, 0xaaaaaa, 0x555555, 0x123456,
               0xff0000, 0x00ff00, 0x0000ff, 0xffff00, 0xff00ff, 0x00ffff]
CYCLE = FIXED_CYCLE + [1 << bit for bit in range(24)] + [
    0xffffff ^ (1 << bit) for bit in range(24)]
SUCCESS_HEADER = b"DLDLOG1\x00"


def make_monotonic():
    if hasattr(time, "monotonic"):
        return time.monotonic
    # Python 3.2 on the target predates time.monotonic. Linux CLOCK_MONOTONIC
    # comes from the already-installed librt; no package/tool installation.
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Python 3.2 requires Linux clock_gettime")

    class Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    library = ctypes.CDLL("librt.so.1", use_errno=True)
    clock_gettime = library.clock_gettime
    clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
    clock_gettime.restype = ctypes.c_int

    def read_clock():
        value = Timespec()
        if clock_gettime(1, ctypes.byref(value)):
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        return value.tv_sec + value.tv_nsec / 1000000000.0

    read_clock()
    return read_clock


def color_value(value):
    original = value
    if value.startswith(("0x", "0X")):
        value = value[2:]
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise argparse.ArgumentTypeError("invalid six-digit color: " + original)
    return int(value, 16)


def length_values(value):
    parts = value.split(",")
    if len(parts) != 6 or any(not part or any(ch not in "0123456789" for ch in part)
                              for part in parts):
        raise argparse.ArgumentTypeError("lengths must be six comma-separated integers")
    result = [int(part) for part in parts]
    if any(length > 300 for length in result):
        raise argparse.ArgumentTypeError("each length must be 0..300")
    return result


def positive_float(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    if not result > 0 or math.isinf(result) or math.isnan(result):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


def utc_deadline(value):
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return float(calendar.timegm(parsed.timetuple()))
    except ValueError:
        raise argparse.ArgumentTypeError("UTC deadline must be YYYY-MM-DDTHH:MM:SSZ")


def color_schedule(mode, color, seed):
    state = seed
    index = 0
    while True:
        if mode == "fixed":
            value = color
        elif mode == "cycle":
            value = CYCLE[index % len(CYCLE)]
        else:
            # Explicit xorshift32 keeps the schedule identical across Python
            # versions. The seed must be nonzero; output is the low 24 bits.
            state ^= (state << 13) & 0xffffffff
            state ^= state >> 17
            state ^= (state << 5) & 0xffffffff
            state &= 0xffffffff
            value = state & 0xffffff
        yield "%06X" % value
        index += 1


class Journal(object):
    def __init__(self, directory):
        self.lock = threading.Lock()
        self.events = open(os.path.join(directory, "events.jsonl"), "w")
        self.successes = open(os.path.join(directory, "successes.bin"), "wb")
        self.successes.write(SUCCESS_HEADER)
        self.successes.flush()

    def event(self, value, sync=False):
        with self.lock:
            self.events.write(json.dumps(value, sort_keys=True) + "\n")
            self.events.flush()
            if sync:
                self.successes.flush()
                os.fsync(self.successes.fileno())
                os.fsync(self.events.fileno())

    def success(self, started, finished):
        with self.lock:
            self.successes.write(struct.pack("<dd", started, finished))
            self.successes.flush()

    def close(self):
        self.events.close()
        self.successes.close()


class CommandRunner(object):
    """One sleeping watchdog thread for the whole run, no per-frame polling.

    The normal parent blocks in communicate while dld-send runs. If termination
    and SIGKILL both fail to make the process exit, log that uncertainty and exit
    the harness; never claim that an unkillable child or its PRU has stopped.
    """
    def __init__(self, monotonic, journal, kill_grace):
        self.monotonic = monotonic
        self.journal = journal
        self.kill_grace = kill_grace
        self.condition = threading.Condition()
        self.active = None
        self.closed = False
        self.thread = threading.Thread(target=self._watch)
        self.thread.daemon = True
        self.thread.start()

    def _watch(self):
        try:
            self._watch_loop()
        except BaseException as error:
            # A failed clock/log operation must not silently kill the sole
            # watchdog and leave communicate blocked on a hung command.
            item = self.active
            if item is not None:
                try:
                    item["process"].kill()
                except BaseException:
                    pass
            message = "Watchdog failed: %s; hardware state is unknown" % error
            try:
                self.journal.event({"event": "fatal_watchdog_error", "exit": 125,
                                    "unix_time": time.time(), "error": message}, True)
            except BaseException:
                try:
                    os.write(2, (message + "\n").encode("utf-8", "replace"))
                except BaseException:
                    pass
            os._exit(125)

    def _watch_loop(self):
        while True:
            with self.condition:
                while self.active is None and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                item = self.active
                remaining = item["deadline"] - self.monotonic()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                if item["process"].poll() is not None:
                    self.condition.wait(0.01)
                    continue
                stage = item["stage"]
                item["stage"] += 1
                item["expired"] = True
                item["deadline"] = self.monotonic() + self.kill_grace
                # Keep the lock through the signal to prevent a completed
                # process from being confused with the next command.
                if stage < 2:
                    self.journal.event({"event": "watchdog", "argv": item["argv"],
                                        "unix_time": time.time(),
                                        "action": "terminate" if stage == 0 else "kill"}, True)
                    try:
                        if stage == 0:
                            item["process"].terminate()
                        else:
                            item["process"].kill()
                    except OSError as error:
                        if error.errno != errno.ESRCH:
                            self.journal.event({"event": "watchdog_signal_error",
                                                "error": str(error)}, True)
                else:
                    self.journal.event({"event": "fatal_hang", "argv": item["argv"],
                                        "unix_time": time.time(), "exit": 124,
                                        "error": "Child did not exit after terminate and kill; hardware state is unknown"}, True)
                    os._exit(124)

    def run(self, argv, timeout, cwd):
        started = time.time()
        item = {"argv": argv, "expired": False, "stage": 0}
        try:
            # Python 3.2 supports this POSIX option. Terminal process-group
            # signals then reach the harness only, so it can finish the current
            # CLI before stopping. The watchdog still signals its child by PID.
            child_options = {"start_new_session": True} if os.name == "posix" else {}
            process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, close_fds=True,
                                       **child_options)
        except OSError as error:
            return {"argv": argv, "exit": None, "started_unix": started,
                    "finished_unix": time.time(), "watchdog_expired": False,
                    "stdout": "", "stderr": str(error)}
        item["process"] = process
        item["deadline"] = self.monotonic() + timeout
        with self.condition:
            self.active = item
            self.condition.notify_all()
        try:
            output, error = process.communicate()
        finally:
            with self.condition:
                self.active = None
                self.condition.notify_all()
        return {"argv": argv, "exit": process.returncode,
                "started_unix": started, "finished_unix": time.time(),
                "watchdog_expired": item["expired"],
                "stdout": output.decode("utf-8", "replace"),
                "stderr": error.decode("utf-8", "replace")}

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join()


def command_ok(result):
    lines = result["stdout"].splitlines()
    return (result["exit"] == 0 and not result["watchdog_expired"] and
            not result["stderr"] and len(lines) == 1 and lines[0].startswith("OK "))


def record_quiet_metrics(metrics, output):
    match = re.search(r"\bquiet_cycles=(\d+),(\d+),(\d+) dma_cycles=(\d+),(\d+),(\d+)\b", output)
    if not match:
        return False
    metrics["reported_sends"] += 1
    values = [int(value) for value in match.groups()]
    for name, offset in (("irq_off_cycles", 0), ("dma_drain_cycles", 3)):
        for bank in range(3):
            value = values[offset + bank]
            entry = metrics[name][bank]
            entry["min"] = value if entry["min"] is None else min(entry["min"], value)
            entry["max"] = max(entry["max"], value)
            entry["sum"] += value
    return True


def write_json(path, value):
    with open(path, "w") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run_session(args, runner_factory=CommandRunner, monotonic=None):
    monotonic = monotonic or make_monotonic()
    started_wall, started_mono = time.time(), monotonic()
    # Convert the UTC limit once to a monotonic cap as well. A backwards wall
    # clock adjustment must not extend the allocated bench window.
    deadline_mono = (None if args.deadline_utc is None else
                     started_mono + max(0.0, args.deadline_utc - started_wall))
    init_path = os.path.join(args.build_dir, "dld-init")
    send_path = os.path.join(args.build_dir, "dld-send")
    os.mkdir(args.output_dir)  # refuse existing paths; never overwrite a run
    config_path = os.path.join(args.output_dir, "panel.json")
    write_json(config_path, {"pixel_type": args.profile, "string_lengths": args.lengths})
    manifest = {"format": "dld-bench-sender-v1", "started_unix": started_wall,
                "argv": sys.argv, "parameters": vars(args),
                "init_argv": None if args.skip_init else [init_path, config_path],
                "send_executable": send_path,
                "schedule": {"mode": args.mode, "fixed_color": "%06X" % args.color,
                             "cycle": ["%06X" % value for value in CYCLE],
                             "seed": args.seed,
                             "random_algorithm": "xorshift32: xor left13, xor right17, xor left5, mask32; low24 after each step"},
                "success_log": {"file": "successes.bin", "header_hex": SUCCESS_HEADER.hex()
                                if hasattr(SUCCESS_HEADER, "hex") else "444c444c4f473100",
                                "record_struct": "<dd", "record_bytes": 16,
                                "fields": ["started_unix", "finished_unix"],
                                "color_index": "zero-based record index into the deterministic schedule",
                                "flush": "every successful send; fsync at progress checkpoints and finalization"},
                "limit_semantics": "duration/count stop between commands; UTC deadline reserves command-timeout plus two kill-grace intervals before starting a command",
                "state_after_stop": "No automatic blackout, cleanup, reinitialization, or service handover",
                "skip_init_contract": "Operator must have initialized the exact recorded profile and lengths"}
    write_json(os.path.join(args.output_dir, "manifest.json"), manifest)
    journal = Journal(args.output_dir)
    runner = runner_factory(monotonic, journal, args.kill_grace)
    signals = [0]
    old_handlers = {}

    def stop_signal(number, frame):
        if not signals[0]:
            signals[0] = number

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None:
            old_handlers[number] = signal.signal(number, stop_signal)

    successful, attempted = 0, 0
    quiet_metrics = {"reported_sends": 0, "bank_order": ["GPIO2", "GPIO1", "GPIO0"],
                     "irq_off_cycles": [{"min": None, "max": 0, "sum": 0} for _ in range(3)],
                     "dma_drain_cycles": [{"min": None, "max": 0, "sum": 0} for _ in range(3)]}
    reason, exit_code, failure = "unknown", 0, None
    initialized = False
    last_progress = started_mono
    colors = color_schedule(args.mode, args.color, args.seed)

    def stop_reason():
        now = monotonic()
        if signals[0]:
            return "signal_%d" % signals[0]
        if args.stop_file and os.path.exists(args.stop_file):
            return "stop_file"
        if args.count is not None and successful >= args.count:
            return "count"
        if args.duration is not None and now - started_mono >= args.duration:
            return "duration"
        reserve = args.command_timeout + 2 * args.kill_grace
        if deadline_mono is not None and (
                now + reserve >= deadline_mono or time.time() + reserve >= args.deadline_utc):
            return "deadline"
        return None

    def checkpoint(event):
        journal.event({"event": event, "unix_time": time.time(),
                       "elapsed_seconds": monotonic() - started_mono,
                       "attempted_sends": attempted, "successful_sends": successful,
                       "quiet_metrics": quiet_metrics}, True)

    try:
        checkpoint("started")
        reason = stop_reason()
        if not reason and not args.skip_init:
            result = runner.run([init_path, config_path], args.command_timeout, args.output_dir)
            journal.event(dict(result, event="initialization"), True)
            if not command_ok(result):
                failure = result
                reason = "initialization_error"
                exit_code = 124 if result["watchdog_expired"] else 1
            else:
                initialized = True
        while not reason:
            reason = stop_reason()
            if reason:
                break
            color = next(colors)
            index = successful
            attempted += 1
            result = runner.run([send_path, color], args.command_timeout, args.output_dir)
            if not command_ok(result):
                failure = dict(result, color=color, send_index=index)
                journal.event(dict(failure, event="send_error"), True)
                reason = "send_error"
                exit_code = 124 if result["watchdog_expired"] else 1
                break
            protected = record_quiet_metrics(quiet_metrics, result["stdout"])
            if getattr(args, "require_quiet", False) and not protected:
                failure = dict(result, color=color, send_index=index,
                               error="protected bench requires kernel quiet-window diagnostics")
                journal.event(dict(failure, event="send_error"), True)
                reason, exit_code = "send_error", 1
                break
            journal.success(result["started_unix"], result["finished_unix"])
            successful += 1
            if monotonic() - last_progress >= args.progress_seconds:
                checkpoint("progress")
                last_progress = monotonic()
                print("successful_sends=%d elapsed_seconds=%.3f" %
                      (successful, last_progress - started_mono))
                sys.stdout.flush()
    except (OSError, ValueError, RuntimeError) as error:
        reason, exit_code = "harness_error", 1
        failure = {"error": str(error)}
        journal.event(dict(failure, event="harness_error"), True)
    finally:
        runner.close()
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
        if signals[0] and exit_code == 0:
            exit_code = 128 + signals[0]
        summary = {"result": "FAIL" if exit_code else "STOPPED",
                   "reason": reason, "exit": exit_code,
                   "started_unix": started_wall, "finished_unix": time.time(),
                   "elapsed_seconds": monotonic() - started_mono,
                   "initialized_by_this_run": initialized,
                   "attempted_sends": attempted, "successful_sends": successful,
                   "failed_sends": attempted - successful,
                   "quiet_metrics": quiet_metrics,
                   "failure": failure,
                   "hardware_state": "left as reported by the last CLI; no automatic recovery"}
        journal.event(dict(summary, event="finished"), True)
        write_json(os.path.join(args.output_dir, "summary.json"), summary)
        journal.close()
    print("%s reason=%s sends=%d artifacts=%s" %
          (summary["result"], reason, successful, args.output_dir))
    return exit_code


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, help="absolute directory containing both driver executables")
    parser.add_argument("--output-dir", required=True, help="NEW absolute run directory; parent must exist")
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--lengths", type=length_values, required=True, metavar="N,N,N,N,N,N")
    parser.add_argument("--mode", choices=("fixed", "cycle", "random"), default="fixed")
    parser.add_argument("--require-quiet", action="store_true",
                        help="reject any send without protected kernel-window diagnostics")
    parser.add_argument("--color", type=color_value, default=0, help="fixed mode color, default 000000")
    parser.add_argument("--seed", type=int, default=1, help="xorshift32 seed, 1..4294967295")
    parser.add_argument("--count", type=int, help="maximum successful sends")
    parser.add_argument("--duration", type=positive_float, help="seconds from run start, checked between commands")
    parser.add_argument("--deadline-utc", type=utc_deadline, help="absolute UTC limit, YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--stop-file", help="absolute stop-file path; existence stops before the next command")
    parser.add_argument("--skip-init", action="store_true", help="continue an operator-initialized matching session")
    parser.add_argument("--command-timeout", type=positive_float, default=7.0,
                        help="CLI watchdog seconds; allows bounded kernel DMA admission, default 7")
    parser.add_argument("--kill-grace", type=positive_float, default=1.0,
                        help="seconds after terminate, then kill; default 1")
    parser.add_argument("--progress-seconds", type=positive_float, default=10.0)
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.count is None and args.duration is None and args.deadline_utc is None:
        parser.error("at least one of --count, --duration, or --deadline-utc is required")
    if args.count is not None and args.count <= 0:
        parser.error("--count must be positive")
    if not 1 <= args.seed <= 0xffffffff:
        parser.error("--seed must be 1..4294967295")
    if args.mode != "fixed" and args.color != 0:
        parser.error("--color is only used with --mode fixed")
    for name in ("build_dir", "output_dir", "stop_file"):
        value = getattr(args, name)
        if value is not None and not os.path.isabs(value):
            parser.error("--%s must be absolute" % name.replace("_", "-"))
    args.build_dir = os.path.realpath(args.build_dir)
    args.output_dir = os.path.abspath(args.output_dir)
    if os.path.lexists(args.output_dir) or not os.path.isdir(os.path.dirname(args.output_dir)):
        parser.error("--output-dir must be new and its parent must exist")
    for name in ("dld-send",) if args.skip_init else ("dld-init", "dld-send"):
        if not os.access(os.path.join(args.build_dir, name), os.X_OK):
            parser.error("missing executable: " + os.path.join(args.build_dir, name))
    try:
        return run_session(args)
    except (OSError, ValueError, RuntimeError) as error:
        print("bench_sender: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
