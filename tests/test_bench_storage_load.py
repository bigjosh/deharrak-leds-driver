#!/usr/bin/env python3
"""Offline helper checks; two actual fsyncs cover 128 KiB, never a board call.

On Windows only, O_NOFOLLOW=0 is a test stand-in: its Linux symlink protection
cannot be verified there. The product helper still rejects unsupported hosts.
"""
from __future__ import print_function
import argparse
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import bench_storage_load as load


class StorageLoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="dld-storage-test-")
        self.original_nofollow = getattr(os, "O_NOFOLLOW", None)
        if self.original_nofollow is None:
            os.O_NOFOLLOW = 0  # Test only; no symlink-safety claim on Windows.
        self.args = argparse.Namespace(file=os.path.join(self.directory, "new.bin"),
                                      seconds=1, deadline_utc=time.time() + 3600,
                                      stop_file=os.path.join(self.directory, "STOP"))

    def tearDown(self):
        if self.original_nofollow is None:
            del os.O_NOFOLLOW
        # Remove only the fresh test directory, after verifying its exact parent.
        resolved = os.path.realpath(self.directory)
        self.assertEqual(os.path.dirname(resolved), os.path.realpath(tempfile.gettempdir()))
        self.assertTrue(os.path.basename(resolved).startswith("dld-storage-test-"))
        shutil.rmtree(resolved)

    def test_two_updates_fsync_and_leave_artifact(self):
        original = os.fsync
        count = [0]
        def sync(fd):
            original(fd)
            count[0] += 1
        os.fsync = sync
        events = []
        try:
            code = load.run_load(self.args,
                                 lambda: "test_limit" if count[0] == 2 else None,
                                 report=events.append)
        finally:
            os.fsync = original
        final = events[-1]
        self.assertEqual(code, 0)
        self.assertEqual(count[0], 2)
        self.assertEqual(final["bytes_written"], 128 * 1024)
        self.assertEqual(final["writes"], 2)
        self.assertEqual(final["fsyncs"], 2)
        self.assertEqual(os.path.getsize(self.args.file), 128 * 1024)
        self.assertGreaterEqual(final["fsync_seconds_total"], 0)
        with open(self.args.file, "rb") as stream:
            first = stream.read(load.BLOCK_BYTES)
            self.assertEqual(first, stream.read(load.BLOCK_BYTES))

    def test_existing_file_is_untouched(self):
        with open(self.args.file, "wb") as stream:
            stream.write(b"keep")
        events = []
        self.assertEqual(load.run_load(self.args, lambda: None, report=events.append), 1)
        self.assertFalse(events[-1]["file_created"])
        with open(self.args.file, "rb") as stream:
            self.assertEqual(stream.read(), b"keep")

    def test_preexisting_stop_creates_nothing(self):
        with open(self.args.stop_file, "wb"):
            pass
        events = []
        self.assertEqual(load.run_load(self.args, lambda: None, report=events.append), 0)
        self.assertEqual(events[-1]["stop_reason"], "stop_file")
        self.assertFalse(os.path.exists(self.args.file))

    def test_expired_deadline_creates_nothing(self):
        self.args.deadline_utc = time.time() - 1
        events = []
        self.assertEqual(load.run_load(self.args, lambda: None, report=events.append), 0)
        self.assertEqual(events[-1]["stop_reason"], "utc_deadline")
        self.assertFalse(os.path.exists(self.args.file))

    def test_ring_wrap_and_duration_without_storage_writes(self):
        original_write, original_sync = os.write, os.fsync
        positions = []
        now = [0.0]
        def write(fd, data):
            positions.append(os.lseek(fd, 0, os.SEEK_CUR))
            return len(data)
        def sleep(seconds):
            now[0] += max(seconds, 1e-9)
        os.write, os.fsync = write, lambda fd: None
        self.args.seconds = 5
        events = []
        try:
            code = load.run_load(self.args, lambda: None, clock=lambda: now[0],
                                 sleep=sleep, report=events.append)
        finally:
            os.write, os.fsync = original_write, original_sync
        self.assertEqual(code, 0)
        self.assertGreater(len(positions), 16)
        self.assertEqual(positions[16], 0)
        self.assertTrue(all(0 <= x <= load.RING_BYTES - load.BLOCK_BYTES for x in positions))
        self.assertEqual(events[-1]["file_size"], load.RING_BYTES)
        self.assertLessEqual(now[0], 5.0000001)

    def test_invalid_bounds_and_required_arguments(self):
        for value in ("0", "3601", "nan", "1.5"):
            self.assertRaises(argparse.ArgumentTypeError, load.seconds_value, value)
        self.assertRaises(argparse.ArgumentTypeError, load.absolute_path, "relative.bin")
        self.assertRaises(argparse.ArgumentTypeError, load.utc_value, "tomorrow")
        base = ["--file", self.args.file, "--seconds", "1"]
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            self.assertRaises(SystemExit, load.arguments, base)
            self.assertRaises(SystemExit, load.arguments,
                              base + ["--deadline-utc", "2026-09-06T13:17:00Z"])
        finally:
            sys.stderr = old_stderr


if __name__ == "__main__":
    unittest.main()
