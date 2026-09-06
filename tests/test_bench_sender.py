#!/usr/bin/env python3
"""No-hardware traffic-generator tests; compatible with Python 3.2."""
from __future__ import print_function

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "bench_sender.py")
bench = types.ModuleType("bench_sender")
bench.__file__ = SOURCE
with open(SOURCE, "r") as source:
    exec(compile(source.read(), SOURCE, "exec"), bench.__dict__)


class FakeClock(object):
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="dld-sender-unit-")
        self.clock = FakeClock()
        self.calls = []
        self.responses = []
        self.after_command = None

    def tearDown(self):
        # This path is the unique temporary directory created by setUp.
        shutil.rmtree(self.directory)

    def args(self, *extra):
        return bench.make_parser().parse_args([
            "--build-dir", os.path.join(self.directory, "fake-build"),
            "--output-dir", os.path.join(self.directory, "run"),
            "--profile", "ws2812b-bgr", "--lengths", "300,17,0,1,250,2",
            "--count", "3"] + list(extra))

    def factory(self, monotonic, journal, kill_grace):
        owner = self

        class FakeRunner(object):
            def run(self, argv, timeout, cwd):
                owner.calls.append(list(argv))
                owner.clock.value += 1.0
                result = {"argv": argv, "exit": 0, "stdout": "OK mock\n",
                          "stderr": "", "watchdog_expired": False,
                          "started_unix": 1000.0 + len(owner.calls),
                          "finished_unix": 1000.25 + len(owner.calls)}
                if owner.responses:
                    result.update(owner.responses.pop(0))
                if owner.after_command:
                    owner.after_command(len(owner.calls))
                return result

            def close(self):
                pass

        return FakeRunner()

    def run_fake(self, args):
        return bench.run_session(args, self.factory, self.clock)

    def read_json(self, name):
        with open(os.path.join(self.directory, "run", name), "r") as stream:
            return json.load(stream)

    def test_fixed_session_and_compact_journal(self):
        self.assertEqual(self.run_fake(self.args("--color", "123456")), 0)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.calls[0][0].endswith("dld-init"))
        self.assertEqual([call[1] for call in self.calls[1:]], ["123456"] * 3)
        self.assertEqual(self.read_json("panel.json"),
                         {"pixel_type": "ws2812b-bgr", "string_lengths": [300, 17, 0, 1, 250, 2]})
        summary = self.read_json("summary.json")
        self.assertEqual(summary["successful_sends"], 3)
        self.assertEqual(summary["reason"], "count")
        path = os.path.join(self.directory, "run", "successes.bin")
        with open(path, "rb") as stream:
            self.assertEqual(stream.read(8), b"DLDLOG1\x00")
            data = stream.read()
        self.assertEqual(len(data), 3 * 16)
        self.assertEqual(struct.unpack("<dd", data[:16]), (1002.0, 1002.25))
        self.assertEqual(self.read_json("manifest.json")["success_log"]["record_bytes"], 16)

    def test_cycle_contains_required_patterns_and_repeats(self):
        generator = bench.color_schedule("cycle", 0, 1)
        first = [next(generator) for unused in range(len(bench.CYCLE))]
        self.assertEqual(first[:8], ["000000", "FFFFFF", "AAAAAA", "555555",
                                    "123456", "FF0000", "00FF00", "0000FF"])
        for bit in range(24):
            self.assertIn("%06X" % (1 << bit), first)
            self.assertIn("%06X" % (0xffffff ^ (1 << bit)), first)
        self.assertEqual(next(generator), first[0])

    def test_random_schedule_is_reproducible(self):
        expected = ["042021", "080601", "CCA8C5", "55994F", "F917D1",
                    "6F5BD0", "B2331A", "F91CB2"]
        generator = bench.color_schedule("random", 0, 1)
        self.assertEqual([next(generator) for unused in expected], expected)
        other = bench.color_schedule("random", 0, 2)
        self.assertNotEqual(next(other), expected[0])

    def test_driver_error_is_recorded_and_never_retried(self):
        self.responses = [{}, {}, {"exit": 6, "stdout": "", "stderr": "busy\n"}]
        self.assertEqual(self.run_fake(self.args()), 1)
        self.assertEqual(len(self.calls), 3)
        summary = self.read_json("summary.json")
        self.assertEqual(summary["successful_sends"], 1)
        self.assertEqual(summary["attempted_sends"], 2)
        self.assertEqual(summary["failed_sends"], 1)
        self.assertEqual(summary["failure"]["send_index"], 1)
        self.assertEqual(summary["failure"]["exit"], 6)

    def test_init_failure_prevents_every_send(self):
        self.responses = [{"exit": 4, "stdout": "", "stderr": "load failed\n"}]
        self.assertEqual(self.run_fake(self.args()), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.read_json("summary.json")["attempted_sends"], 0)

    def test_protected_run_stops_on_legacy_success(self):
        self.assertEqual(self.run_fake(self.args("--require-quiet")), 1)
        self.assertEqual(len(self.calls), 2)  # init and only one attempted send
        summary = self.read_json("summary.json")
        self.assertEqual(summary["successful_sends"], 0)
        self.assertIn("quiet-window", summary["failure"]["error"])

    def test_protected_metrics_survive_summary(self):
        self.responses = [{}] + [{"stdout": "OK quiet_cycles=%d,0,20 dma_cycles=2,0,3\n" % n}
                                 for n in (11, 13, 12)]
        self.assertEqual(self.run_fake(self.args("--require-quiet")), 0)
        metrics = self.read_json("summary.json")["quiet_metrics"]
        self.assertEqual(metrics["reported_sends"], 3)
        self.assertEqual(metrics["irq_off_cycles"][0], {"min": 11, "max": 13, "sum": 36})

    def test_skip_init_and_duration_handoff(self):
        self.assertEqual(self.run_fake(self.args("--skip-init", "--duration", "2")), 0)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(call[0].endswith("dld-send") for call in self.calls))
        self.assertEqual(self.read_json("summary.json")["reason"], "duration")
        self.assertFalse(self.read_json("summary.json")["initialized_by_this_run"])

    def test_stop_file_is_checked_between_commands(self):
        stop_path = os.path.join(self.directory, "stop")

        def request_stop(count):
            if count == 2:
                with open(stop_path, "w") as stream:
                    stream.write("stop\n")

        self.after_command = request_stop
        self.assertEqual(self.run_fake(self.args("--stop-file", stop_path)), 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.read_json("summary.json")["reason"], "stop_file")
        self.assertEqual(self.read_json("summary.json")["successful_sends"], 1)

    def test_expired_deadline_does_not_initialize(self):
        self.assertEqual(self.run_fake(self.args("--deadline-utc", "2000-01-01T00:00:00Z")), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.read_json("summary.json")["reason"], "deadline")

    def test_existing_output_directory_is_preserved(self):
        args = self.args()
        os.mkdir(args.output_dir)
        sentinel = os.path.join(args.output_dir, "sentinel")
        with open(sentinel, "w") as stream:
            stream.write("preserve")
        with self.assertRaises(OSError):
            self.run_fake(args)
        self.assertEqual(self.calls, [])
        with open(sentinel, "r") as stream:
            self.assertEqual(stream.read(), "preserve")

    def test_timeout_is_failure_even_if_child_reports_success(self):
        self.responses = [{}, {"watchdog_expired": True}]
        self.assertEqual(self.run_fake(self.args()), 124)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.read_json("summary.json")["successful_sends"], 0)

    def test_child_watchdog_stops_a_hung_program(self):
        journal = bench.Journal(self.directory)
        runner = bench.CommandRunner(bench.make_monotonic(), journal, 0.1)
        try:
            started = time.time()
            result = runner.run([sys.executable, "-c", "import time; time.sleep(30)"],
                                0.1, self.directory)
            self.assertTrue(result["watchdog_expired"])
            self.assertNotEqual(result["exit"], 0)
            self.assertLess(time.time() - started, 5.0)
        finally:
            runner.close()
            journal.close()


if __name__ == "__main__":
    unittest.main()
