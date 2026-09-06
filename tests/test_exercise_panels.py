"""Exercise runner checks using fake time and localhost-only UDP sockets."""

import importlib.util
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SCRIPT = TOOLS / "exercise_panels.py"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("exercise_panels", str(SCRIPT))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class FakeClock:
    def __init__(self, first_sleep_delay=0):
        self.now = 0.0
        self.first_sleep_delay = first_sleep_delay
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if seconds <= 0 or not math.isfinite(seconds):
            raise AssertionError("invalid requested sleep")
        self.sleeps.append(seconds)
        self.now += seconds + self.first_sleep_delay
        self.first_sleep_delay = 0


class PacketTests(unittest.TestCase):
    def test_wire_format_is_one_rgb_pixel_on_opc_channel_zero(self):
        self.assertEqual(runner.encode_color((85, 170, 0)), b"\x00\x00\x00\x03\x55\xaa\x00")
        for color in ((0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255), (85, 85, 85)):
            packet = runner.encode_color(color)
            self.assertEqual(struct.unpack("!BBHBBB", packet), (0, 0, 3) + color)

    def test_final_packet_guard_rejects_bad_values_and_excess_total(self):
        for color in ((255, 1, 0), (86, 85, 85), (-1, 0, 0), (256, 0, 0),
                      (1.0, 0, 0), (True, 0, 0), ("1", 0, 0),
                      (math.nan, 0, 0), (math.inf, 0, 0), (), (0, 0), (0, 0, 0, 0)):
            with self.subTest(color=color), self.assertRaises(ValueError):
                runner.encode_color(color)

    def test_scene_selection_wraps_at_complete_playlist_boundary(self):
        duration = sum(scene.duration for scene in runner.SCENES)
        cycle, index, scene, position = runner.select_scene(duration)
        self.assertEqual((cycle, index, scene, position), (1, 0, runner.SCENES[0], 0))
        cycle, index, scene, position = runner.select_scene(duration - 0.01)
        self.assertEqual(index, len(runner.SCENES) - 1)
        self.assertAlmostEqual(position, scene.duration - 0.01)


class PacingTests(unittest.TestCase):
    def exercise(self, duration=0.35, targets=("panel-a", "panel-b"), fps=10,
                 clock=None, send_hook=None, stop_hook=None):
        clock = clock or FakeClock()
        calls = []
        reports = []

        def send(target, packet):
            calls.append((clock(), target, packet))
            if send_hook:
                send_hook(clock, calls, target, packet)

        def stopped():
            return stop_hook(clock, calls) if stop_hook else None

        result = runner.run_exercise(targets, fps, 1, duration, stopped, send,
                                     reports.append, clock=clock, sleep=clock.sleep)
        return result, calls, reports, clock

    def assert_paced(self, calls, fps=10):
        for target in {call[1] for call in calls}:
            times = [stamp for stamp, host, _ in calls if host == target]
            for earlier, later in zip(times, times[1:]):
                self.assertGreaterEqual(later - earlier, 1 / fps - 1e-9, (target, times))
        for _, _, packet in calls:
            self.assertEqual(packet[:4], b"\x00\x00\x00\x03")
            self.assertEqual(len(packet), 7)
            self.assertLessEqual(sum(packet[4:]), 255)

    def test_duration_and_final_black_obey_rate_per_panel(self):
        result, calls, reports, _ = self.exercise()
        self.assertEqual(result["stop_reason"], "duration")
        self.assert_paced(calls)
        for target in ("panel-a", "panel-b"):
            packets = [packet for _, host, packet in calls if host == target]
            self.assertEqual(len(packets), 5)  # Four active frames, one paced black.
            self.assertEqual(packets[-1], runner.encode_color((0, 0, 0)))
            self.assertEqual(result["targets"][target]["sent"], len(packets))
        self.assertEqual(reports[0]["event"], "start")
        self.assertEqual(reports[-1]["event"], "finish")

    def test_scheduler_delay_drops_missed_slots_without_catchup(self):
        result, calls, _, _ = self.exercise(duration=3.36, clock=FakeClock(first_sleep_delay=3))
        self.assert_paced(calls)
        stamps = [stamp for stamp, host, _ in calls if host == "panel-a"]
        self.assertLessEqual(len(stamps), 6)
        self.assertGreater(stamps[1] - stamps[0], 3)
        self.assertEqual(result["stop_reason"], "duration")

    def test_slow_transport_keeps_every_host_below_requested_rate(self):
        def delay(clock, calls, target, packet):
            clock.now += 0.08 if target == "panel-a" else 0.13

        _, calls, _, _ = self.exercise(duration=1.01, send_hook=delay)
        self.assert_paced(calls)
        self.assertLess(len(calls), 14)

    def test_lower_rate_also_applies_to_final_black(self):
        _, calls, _, _ = self.exercise(duration=1.01, fps=2)
        self.assert_paced(calls, fps=2)
        self.assertEqual(len(calls), 8)

    def test_socket_errors_do_not_starve_other_panels_and_recovery_is_reported(self):
        failed_attempts = [0]

        def fail_first_three(clock, calls, target, packet):
            if target == "panel-a":
                failed_attempts[0] += 1
                if failed_attempts[0] <= 3:
                    raise OSError("simulated network unavailable")

        result, calls, reports, _ = self.exercise(duration=0.45, send_hook=fail_first_three)
        self.assert_paced(calls)
        self.assertEqual(result["targets"]["panel-a"]["errors"], 3)
        self.assertEqual(result["targets"]["panel-a"]["consecutive_errors"], 0)
        self.assertIsNone(result["targets"]["panel-a"]["last_error"])
        self.assertEqual(result["targets"]["panel-b"]["errors"], 0)
        self.assertEqual(result["targets"]["panel-b"]["sent"], 6)
        self.assertEqual(len([event for event in reports if event["event"] == "send_error"]), 1)
        recovery = [event for event in reports if event["event"] == "send_recovered"]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["target"], "panel-a")
        self.assertEqual(recovery[0]["previous_consecutive_errors"], 3)

    def test_invalid_second_panel_color_prevents_entire_frame_reaching_wire(self):
        calls = []
        reports = []
        clock = FakeClock()

        def unsafe_color(scene, elapsed, panel_index, panel_count, cycle, seed):
            return (255, 1, 0) if panel_index else (1, 0, 0)

        with mock.patch.object(runner, "color_at", side_effect=unsafe_color):
            with self.assertRaises(ValueError):
                runner.run_exercise(("panel-a", "panel-b"), 10, 1, 1,
                                     lambda: None, lambda *args: calls.append(args),
                                     reports.append, clock=clock, sleep=clock.sleep)
        self.assertEqual(calls, [])
        self.assertEqual(reports[-1]["event"], "finish")
        self.assertEqual(reports[-1]["stop_reason"], "error")

    def test_invalid_later_frame_still_finishes_with_paced_black(self):
        original = runner.color_at
        calls = []
        clock = FakeClock()

        def later_bad(scene, elapsed, panel_index, panel_count, cycle, seed):
            return ((255, 255, 255) if elapsed > 0.05 else
                    original(scene, elapsed, panel_index, panel_count, cycle, seed))

        with mock.patch.object(runner, "color_at", side_effect=later_bad):
            with self.assertRaises(ValueError):
                runner.run_exercise(("panel-a", "panel-b"), 10, 1, 1,
                                     lambda: None,
                                     lambda host, packet: calls.append((clock(), host, packet)),
                                     lambda event: None, clock=clock, sleep=clock.sleep)
        self.assertEqual(len(calls), 4)
        self.assert_paced(calls)
        self.assertTrue(all(packet[4:] == b"\0\0\0" for _, _, packet in calls[-2:]))

    def test_stop_before_start_emits_no_datagrams(self):
        result, calls, _, _ = self.exercise(stop_hook=lambda clock, calls: "stop_file")
        self.assertEqual(calls, [])
        self.assertEqual(result["stop_reason"], "stop_file")

    def test_stop_after_frames_sends_one_paced_black_to_each_panel(self):
        result, calls, _, _ = self.exercise(duration=None,
            stop_hook=lambda clock, calls: "stop_file" if len(calls) >= 6 else None)
        self.assertEqual(result["stop_reason"], "stop_file")
        self.assertEqual(len(calls), 8)
        self.assert_paced(calls)
        self.assertTrue(all(packet[4:] == b"\0\0\0" for _, _, packet in calls[-2:]))

    def test_runner_rejects_unsafe_rate_duration_or_duplicate_target_without_io(self):
        cases = [(("panel-a",), rate, 1) for rate in (0, -1, 10.1, math.nan, math.inf)]
        cases += [(("panel-a",), 10, duration) for duration in (0, -1, math.nan, math.inf)]
        cases += [((), 10, 1), (("panel-a", "panel-a"), 10, 1)]
        for targets, fps, duration in cases:
            with self.subTest(targets=targets, fps=fps, duration=duration):
                sender = mock.Mock()
                with self.assertRaises(ValueError):
                    runner.run_exercise(targets, fps, 1, duration, lambda: None, sender, lambda x: None)
                sender.assert_not_called()


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT)] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=5)

    def test_invalid_cli_args_rejected_before_run_directory_created(self):
        bad_options = (("--fps", "nan"), ("--fps", "inf"), ("--fps", "10.01"),
                       ("--fps", "0"), ("--duration", "nan"), ("--duration", "-1"),
                       ("--port", "0"), ("--targets", "127.0.0.1", "127.0.0.1"))
        with tempfile.TemporaryDirectory(prefix="dld-exercise-cli-") as base:
            for index, options in enumerate(bad_options):
                directory = Path(base) / str(index)
                result = self.invoke("--targets", "127.0.0.1", "--run-dir", str(directory), *options)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(directory.exists())

    def test_existing_run_directory_is_preserved_and_refused(self):
        with tempfile.TemporaryDirectory(prefix="dld-exercise-existing-") as base:
            directory = Path(base) / "existing"
            directory.mkdir()
            marker = directory / "keep.txt"
            marker.write_text("untouched", encoding="utf-8")
            result = self.invoke("--targets", "127.0.0.1", "--run-dir", str(directory), "--duration", "0.1")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
            self.assertEqual(list(directory.iterdir()), [marker])

    def test_stop_requires_recognized_run_and_can_be_requested_twice(self):
        with tempfile.TemporaryDirectory(prefix="dld-exercise-stop-") as base:
            directory = Path(base)
            manifest = directory / "run.json"
            manifest.write_text(json.dumps({"program": "unrelated"}), encoding="utf-8")
            self.assertEqual(self.invoke("--stop", str(directory)).returncode, 1)
            self.assertFalse((directory / "STOP").exists())
            manifest.write_text(json.dumps({"program": runner.PROGRAM}), encoding="utf-8")
            for _ in range(2):
                result = self.invoke("--stop", str(directory))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((directory / "STOP").is_file())

    def local_udp_run(self, use_stop):
        # Bind only loopback. No test resolves or sends to a real panel address.
        with tempfile.TemporaryDirectory(prefix="dld-exercise-udp-") as base:
            directory = Path(base) / "run"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.settimeout(0.05)
                args = [sys.executable, "-B", str(SCRIPT), "--targets", "127.0.0.1",
                        "--port", str(listener.getsockname()[1]), "--run-dir", str(directory)]
                if not use_stop:
                    args += ["--duration", "0.35"]
                process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           universal_newlines=True)
                packets = []
                stop_requested = False
                deadline = time.monotonic() + 5
                try:
                    while time.monotonic() < deadline:
                        try:
                            packet, address = listener.recvfrom(65535)
                            self.assertEqual(address[0], "127.0.0.1")
                            packets.append(packet)
                        except socket.timeout:
                            if process.poll() is not None:
                                break
                        if use_stop and len(packets) >= 2 and not stop_requested:
                            result = self.invoke("--stop", str(directory))
                            self.assertEqual(result.returncode, 0, result.stderr)
                            stop_requested = True
                    stdout, stderr = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 0, (stdout, stderr))
                    self.assertGreaterEqual(len(packets), 3)
                    self.assertEqual(packets[-1], runner.encode_color((0, 0, 0)))
                    for packet in packets:
                        self.assertEqual(packet[:4], b"\x00\x00\x00\x03")
                        self.assertEqual(len(packet), 7)
                        self.assertLessEqual(sum(packet[4:]), 255)
                    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
                    status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
                    self.assertEqual(manifest["rgb_sum_limit"], 255)
                    self.assertEqual(manifest["fps_limit"], 10)
                    self.assertEqual(status["event"], "finish")
                    self.assertEqual(status["stop_reason"], "stop_file" if use_stop else "duration")
                    self.assertEqual(status["targets"]["127.0.0.1"]["sent"], len(packets))
                    self.assertEqual(status["targets"]["127.0.0.1"]["last_color"], [0, 0, 0])
                    self.assertGreater((directory / "events.log").stat().st_size, 0)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()

    def test_real_localhost_udp_finite_run(self):
        self.local_udp_run(use_stop=False)

    def test_real_localhost_udp_indefinite_run_and_stop_command(self):
        self.local_udp_run(use_stop=True)


if __name__ == "__main__":
    unittest.main()
