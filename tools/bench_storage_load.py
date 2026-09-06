#!/usr/bin/env python3
"""Bounded, test-only storage contention; Python 3.2 compatible.

Create one NEW absolute file in an existing project directory. Write a 64 KiB
deterministic block, fsync, then wait at least 250 ms before the next update.
Offsets wrap in a 1 MiB ring. Never truncate, overwrite, delete, or unlink an
existing file. The created artifact remains for inspection. This does not use
raw devices, change services, or configure hardware. A blocked kernel write or
fsync cannot be interrupted by the software deadline; stop is checked between
updates and during waits.
"""
from __future__ import print_function

import argparse
import calendar
import ctypes
import datetime
import json
import os
import signal
import stat
import sys
import time


BLOCK_BYTES = 64 * 1024
RING_BYTES = 1024 * 1024
INTERVAL_SECONDS = 0.250
PROGRESS_SECONDS = 5.0


def monotonic_clock():
    if hasattr(time, "monotonic"):
        return time.monotonic
    class Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]
    library = ctypes.CDLL("librt.so.1", use_errno=True)
    function = library.clock_gettime
    function.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
    function.restype = ctypes.c_int
    def now():
        value = Timespec()
        if function(1, ctypes.byref(value)):
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        return value.tv_sec + value.tv_nsec / 1000000000.0
    now()
    return now


def seconds_value(text):
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("seconds must be an integer 1..3600")
    if not 1 <= value <= 3600:
        raise argparse.ArgumentTypeError("seconds must be 1..3600")
    return value


def utc_value(text):
    try:
        parsed = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise argparse.ArgumentTypeError("deadline must be YYYY-MM-DDTHH:MM:SSZ")
    return float(calendar.timegm(parsed.timetuple()))


def absolute_path(text):
    if not os.path.isabs(text):
        raise argparse.ArgumentTypeError("path must be absolute")
    if not os.path.basename(text):
        raise argparse.ArgumentTypeError("path must name a file")
    return os.path.abspath(text)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=absolute_path,
                        help="NEW file under an existing project parent directory")
    parser.add_argument("--seconds", required=True, type=seconds_value)
    parser.add_argument("--deadline-utc", required=True, type=utc_value)
    parser.add_argument("--stop-file", required=True, type=absolute_path,
                        help="stop when present; never modify this path")
    args = parser.parse_args(argv)
    if not os.path.isdir(os.path.dirname(args.file)):
        parser.error("--file parent must already exist; directories are never created")
    if args.file == args.stop_file:
        parser.error("load file and stop file must differ")
    return args


def create_new_file(path):
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("this Linux storage test requires O_NOFOLLOW support")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("new descriptor is not a regular file")
    except Exception:
        os.close(fd)
        raise
    return fd


def emit(value):
    print(json.dumps(value, sort_keys=True))
    sys.stdout.flush()


def run_load(args, signal_stop, clock=None, wall_clock=time.time,
             sleep=time.sleep, report=emit):
    if clock is None:
        clock = monotonic_clock()
    start = clock()
    deadline = start + min(args.seconds, max(0.0, args.deadline_utc - wall_clock()))
    stats = {"file": args.file, "file_created": False, "ring_bytes": RING_BYTES,
             "block_bytes": BLOCK_BYTES, "interval_seconds": INTERVAL_SECONDS,
             "requested_seconds": args.seconds, "deadline_unix": args.deadline_utc,
             "writes": 0, "bytes_written": 0, "fsyncs": 0,
             "fsync_seconds_total": 0.0, "fsync_seconds_min": None,
             "fsync_seconds_max": None, "error": None, "file_size": 0}
    fd = None
    reason = "duration"
    next_write = start
    next_report = start + PROGRESS_SECONDS
    offset = 0
    # Reproducible data; each ring slot receives the same complete block.
    payload = bytes(bytearray((i * 73 + (i >> 8) * 19) & 255
                              for i in range(BLOCK_BYTES)))

    def stop_reason():
        signalled = signal_stop()
        if signalled:
            return signalled
        if os.path.exists(args.stop_file):
            return "stop_file"
        if wall_clock() >= args.deadline_utc:
            return "utc_deadline"
        if clock() >= deadline:
            return "duration_or_utc_deadline"
        return None

    def snapshot(event):
        result = dict(stats)
        result["event"] = event
        result["elapsed_seconds"] = max(0.0, clock() - start)
        result["stop_reason"] = reason if event == "finish" else None
        return result

    try:
        reason = stop_reason()
        if reason is None:
            fd = create_new_file(args.file)
            stats["file_created"] = True
            report(snapshot("start"))
        while fd is not None:
            reason = stop_reason()
            if reason is not None:
                break
            now = clock()
            if now >= deadline:
                reason = "duration_or_utc_deadline"
                break
            if now >= next_report:
                report(snapshot("progress"))
                next_report = now + PROGRESS_SECONDS
            if now < next_write:
                sleep(min(next_write - now, deadline - now, 0.05))
                continue
            os.lseek(fd, offset, os.SEEK_SET)
            written = 0
            while written < BLOCK_BYTES:
                count = os.write(fd, payload[written:])
                if count <= 0:
                    raise OSError("storage write made no progress")
                written += count
                stats["bytes_written"] += count
                stats["file_size"] = max(stats["file_size"], offset + written)
            stats["writes"] += 1
            before_sync = clock()
            os.fsync(fd)
            sync_seconds = max(0.0, clock() - before_sync)
            stats["fsyncs"] += 1
            stats["fsync_seconds_total"] += sync_seconds
            minimum = stats["fsync_seconds_min"]
            maximum = stats["fsync_seconds_max"]
            stats["fsync_seconds_min"] = sync_seconds if minimum is None else min(minimum, sync_seconds)
            stats["fsync_seconds_max"] = sync_seconds if maximum is None else max(maximum, sync_seconds)
            offset = (offset + BLOCK_BYTES) % RING_BYTES
            # Never catch up with a burst after descheduling or a slow fsync.
            next_write = clock() + INTERVAL_SECONDS
    except (OSError, RuntimeError) as error:
        stats["error"] = str(error)
        reason = "error"
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as error:
                stats["error"] = str(error)
                reason = "error"
        report(snapshot("finish"))
    return 1 if stats["error"] else 0


def main(argv=None):
    args = arguments(argv)
    caught = [None]
    def handle(number, unused_frame):
        caught[0] = "signal_%d" % number
    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    try:
        return run_load(args, lambda: caught[0])
    except (OSError, RuntimeError) as error:
        emit({"event": "fatal", "error": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
