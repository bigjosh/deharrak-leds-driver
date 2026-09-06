#!/usr/bin/env python3
"""Explicit Windows soak supervisor; stops at the first bench failure.

One native CLI sender is initialized per phase. Timed Saleae captures sample
that continuous traffic, with acknowledged gaps during export and analysis.
Only this run's remote stop file, load PID, SSH processes, and capture/checker
workers are controlled. No service or driver recovery is attempted.
"""
import argparse
import datetime
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import traceback
import uuid

sys.dont_write_bytecode = True
from bench_sender import CYCLE, PROFILES, color_value


ROOT = pathlib.Path(__file__).resolve().parents[1]
CAPTURE_OVERHEAD = 90.0
ANALYSIS_TIMEOUT = 120.0
IO_TIMEOUT = 20.0
CLEANUP_RESERVE = 180.0
FREE_BYTES = 50 * 1024 ** 3


class BenchFailure(RuntimeError):
    pass


class Cancelled(BenchFailure):
    pass


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def quoted(argv):
    return " ".join(shlex.quote(str(value)) for value in argv)


def parse_deadline(value):
    try:
        stamp = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return stamp.replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        raise argparse.ArgumentTypeError("deadline must be YYYY-MM-DDTHH:MM:SSZ")


def normalized_plan(value):
    if not isinstance(value, list) or not value:
        raise ValueError("plan must be a nonempty list")
    result = []
    names = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each phase must be an object")
        if set(item) - {"name", "duration", "mode", "color", "profile", "lengths", "load", "seed"}:
            raise ValueError("unknown phase field")
        phase = dict(item)
        name = phase.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in names:
            raise ValueError("phase names must be unique safe filenames")
        names.add(name)
        duration = phase.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 1:
            raise ValueError("phase duration must be finite and at least one second")
        if phase.get("mode") not in ("fixed", "cycle", "random"):
            raise ValueError("phase mode must be fixed, cycle, or random")
        if phase.get("profile") not in PROFILES:
            raise ValueError("unknown phase pixel profile")
        lengths = phase.get("lengths")
        if not isinstance(lengths, list) or len(lengths) != 6 or any(type(n) is not int or not 0 <= n <= 300 for n in lengths):
            raise ValueError("phase lengths must contain six integers in 0..300")
        if phase.get("load", "none") not in ("none", "ddr", "cpu", "ethernet"):
            raise ValueError("phase load must be none, ddr, or cpu")
        seed = phase.get("seed", 1)
        if type(seed) is not int or not 1 <= seed <= 0xffffffff:
            raise ValueError("phase seed must be 1..4294967295")
        phase["seed"] = seed
        phase["load"] = phase.get("load", "none")
        color = phase.get("color", "000000")
        if not isinstance(color, str):
            raise ValueError("phase color must be a six-digit hexadecimal string")
        phase["color"] = "%06X" % color_value(color)
        result.append(phase)
    return result


def binary_coverage(directory):
    """Read retained v0 intervals, never substitute the requested/wall time."""
    channels = []
    for index in range(6):
        path = directory / ("digital_%d.bin" % index)
        with path.open("rb") as stream:
            header = stream.read(44)
        if len(header) != 44:
            raise BenchFailure("incomplete capture header: " + str(path))
        magic, version, kind, initial, begin, end, transitions = struct.unpack("<8siiIddQ", header)
        if magic != b"<SALEAE>" or version != 0 or kind != 0 or initial not in (0, 1):
            raise BenchFailure("unsupported/invalid digital capture header: " + str(path))
        if not math.isfinite(begin) or not math.isfinite(end) or end <= begin:
            raise BenchFailure("invalid retained capture interval: " + str(path))
        if path.stat().st_size != 44 + 8 * transitions:
            raise BenchFailure("incomplete digital capture body: " + str(path))
        channels.append(dict(channel=index, begin_s=begin, end_s=end, transitions=transitions))
    begin, end = max(c["begin_s"] for c in channels), min(c["end_s"] for c in channels)
    if end <= begin or any(abs(c["begin_s"] - begin) > 1e-8 or abs(c["end_s"] - end) > 1e-8 for c in channels):
        raise BenchFailure("six digital channels have different retained intervals")
    return dict(begin_s=begin, end_s=end, duration_s=end - begin, channels=channels)


class Supervisor:
    def __init__(self, args, plan):
        self.args = args
        self.plan = plan
        self.base = pathlib.Path(args.output).resolve()
        self.base.mkdir(parents=True, exist_ok=False)
        self.events = (self.base / "events.jsonl").open("w", encoding="utf-8")
        self.cancel_signal = 0
        self.started_mono = time.monotonic()
        self.deadline_mono = self.started_mono + max(0, args.deadline_utc - time.time())
        self.run_id = "soak-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                        "-o", "UserKnownHostsFile=" + str(pathlib.Path(args.known_hosts).resolve()),
                        "-o", "StrictHostKeyChecking=yes"]
        self.ssh = ["ssh"] + self.options + ["root@beaglebone"]
        self.scp = ["scp", "-O"] + self.options
        self.state = dict(schema_version=1, run_id=self.run_id, status="starting", started_utc=utc(),
                          deadline_utc=args.deadline_utc_text, current_phase=None, current_chunk=None,
                          captured_seconds=0.0, validated_seconds=0.0, complete_pulses=0,
                          checked_seconds=0.0, complete_channel_frames=0, chunks=0, phases=[], failures=[],
                          violation_count=0, violation_counts={}, affected_chunks=[],
                          coverage_note="Timed samples have gaps during export/analysis. Boundary fragments are expected. Coverage uses binary intervals, not wall time.",
                          random_validation="Uniform 24-bit pixels and cross-string agreement; random schedule identity is retained for later comparison, not checked by --allow-any-color.")
        atomic_json(self.base / "plan.json", plan)
        atomic_json(self.base / "manifest.json", dict(parameters=vars(args), run_id=self.run_id,
                    capture_overhead_seconds=CAPTURE_OVERHEAD, analysis_timeout_seconds=ANALYSIS_TIMEOUT,
                    io_timeout_seconds=IO_TIMEOUT, cleanup_reserve_seconds=CLEANUP_RESERVE,
                    free_space_floor_bytes=FREE_BYTES, cycle=["%06X" % color for color in CYCLE]))
        self.checkpoint()

    def remaining(self):
        return min(self.deadline_mono - time.monotonic(), self.args.deadline_utc - time.time())

    def checkpoint(self):
        self.state["updated_utc"] = utc()
        atomic_json(self.base / "status.json", self.state)

    def event(self, item):
        item = dict(item, utc=utc())
        self.events.write(json.dumps(item, sort_keys=True, allow_nan=False) + "\n")
        self.events.flush()
        os.fsync(self.events.fileno())

    def check_cancel(self):
        if self.cancel_signal or (self.base / "STOP").exists():
            raise Cancelled("operator stop requested")

    def spawn(self, argv, log_path, optional_log=False):
        if not optional_log:
            self.check_cancel()
            if self.remaining() <= 0:
                raise BenchFailure("global deadline reached before launching a process")
        try:
            log = log_path.open("w", encoding="utf-8")
        except OSError:
            if not optional_log:
                raise
            # Disk/logging failure must not prevent the owned remote workload
            # from receiving its stop request during failure cleanup.
            log = open(os.devnull, "w")
        try:
            process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL)
        except BaseException:
            log.close()
            raise
        return dict(process=process, log=log, argv=list(argv), log_path=str(log_path))

    def reap(self, child, timeout, cancellation=True):
        limit = time.monotonic() + timeout
        while True:
            if cancellation:
                self.check_cancel()
            try:
                return child["process"].wait(timeout=min(0.5, max(0.01, limit - time.monotonic())))
            except subprocess.TimeoutExpired:
                if time.monotonic() >= limit:
                    raise

    def terminate_local(self, child):
        """Only the owned local process; does not claim remote/device shutdown."""
        process = child["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        child["log"].close()

    def bounded(self, argv, log_path, timeout, cleanup=False, accepted_codes=(0,)):
        if not cleanup:
            self.check_cancel()
            if self.remaining() <= 0:
                raise BenchFailure("global deadline reached before launching a worker")
        child = self.spawn(argv, log_path, optional_log=cleanup)
        try:
            code = self.reap(child, timeout, cancellation=not cleanup)
            if code not in accepted_codes:
                raise BenchFailure("worker exit %d; see %s" % (code, log_path))
            return code
        except BaseException:
            self.terminate_local(child)
            raise
        finally:
            child["log"].close()

    def remote(self, command, log_path, cleanup=False):
        return self.bounded(self.ssh + [command], log_path, IO_TIMEOUT, cleanup)

    def stop_load_command(self, pid_file):
        executable = self.args.remote_dir + "/build/bench-load"
        # No name-based scans and no signal to an unverified PID. A missing
        # process is already stopped; another executable is never signalled.
        # A short read-only readiness wait covers cancellation during SSH/shell
        # startup, when the owned PID file or exec may not exist yet.
        code = """import errno, os, signal, sys, time
pidfile, expected = sys.argv[1:]
limit = time.time() + 3.0
while True:
    try:
        with open(pidfile) as stream:
            text = stream.read().strip()
        if not text.isdigit() or int(text) <= 1:
            raise RuntimeError('invalid owned PID file')
        pid = int(text)
        try:
            actual = os.readlink('/proc/%d/exe' % pid)
        except OSError as error:
            if not os.path.exists('/proc/%d' % pid):
                print('owned load already exited')
                break
            raise
        if actual == expected:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as error:
                if error.errno != errno.ESRCH:
                    raise
            print('terminated owned load pid=%d' % pid)
            break
    except IOError as error:
        if error.errno != errno.ENOENT:
            raise
    if time.time() >= limit:
        raise RuntimeError('load PID/executable not verified; no signal sent')
    time.sleep(0.02)
"""
        return quoted(["python3", "-c", code, pid_file, executable])

    def stop_phase(self, phase_state, directory, sender, load, remote_run, stop_file, pid_file):
        errors = []
        # Stop owned host UDP traffic before relying on SSH for remote cleanup.
        if load is not None and load.get("local_stop_file"):
            try:
                pathlib.Path(load["local_stop_file"]).write_text(utc() + "\n")
            except BaseException as error:
                errors.append("local load stop-file: " + repr(error))
                self.terminate_local(load)
        # Attempt both remote stops even when the first fails. Cleanup may run
        # after a clock jump/deadline; no new test traffic is launched here.
        if sender is not None:
            try:
                self.remote("umask 077; : > " + shlex.quote(stop_file), directory / "stop-sender.log", True)
            except BaseException as error:
                errors.append("sender stop-file: " + repr(error))
        if load is not None and not load.get("local_stop_file"):
            try:
                if load["process"].poll() is None:
                    self.remote(self.stop_load_command(pid_file), directory / "stop-load.log", True)
            except BaseException as error:
                errors.append("load stop: " + repr(error))
        for label, child in (("sender", sender), ("load", load)):
            if child is None:
                continue
            try:
                code = self.reap(child, 15, cancellation=False)
                phase_state[label + "_exit"] = code
                if code:
                    errors.append("%s process exit %d" % (label, code))
            except BaseException as error:
                errors.append(label + " did not finish: " + repr(error))
                try:
                    self.terminate_local(child)
                except BaseException as kill_error:
                    errors.append(label + " local reap: " + repr(kill_error))
            finally:
                child["log"].close()
        if sender is not None:
            try:
                destination = directory / "remote-sender"
                self.bounded(self.scp + ["-r", "root@beaglebone:" + remote_run, str(destination)],
                             directory / "retrieve-sender.log", 60, cleanup=True)
                summary = json.loads((destination / "summary.json").read_text())
                manifest = json.loads((destination / "manifest.json").read_text())
                success_path = destination / "successes.bin"
                with success_path.open("rb") as stream:
                    header = stream.read(8)
                size = success_path.stat().st_size
                confirmed = summary.get("successful_sends")
                if (header != b"DLDLOG1\0" or type(confirmed) is not int or confirmed <= 0 or
                        size != 8 + 16 * confirmed or summary.get("exit") != 0 or
                        summary.get("failed_sends") != 0 or summary.get("failure") is not None):
                    raise BenchFailure("sender summary/journal is failed, empty, or incomplete")
                expected = phase_state["parameters"]
                parameters = manifest.get("parameters", {})
                if any(parameters.get(k) != expected[k] for k in ("profile", "lengths", "mode", "seed")):
                    raise BenchFailure("retrieved sender manifest does not match phase")
                if expected["mode"] == "fixed" and parameters.get("color") != int(expected["color"], 16):
                    raise BenchFailure("retrieved fixed color does not match phase")
                phase_state["sender_summary"] = summary
                phase_state["successful_sends"] = confirmed
            except BaseException as error:
                errors.append("sender retrieval/validation: " + repr(error))
        phase_state["cleanup_errors"] = errors
        self.checkpoint()
        if errors:
            raise BenchFailure("; ".join(errors))

    def run_phase(self, number, phase):
        directory = self.base / ("%03d-%s" % (number, phase["name"]))
        directory.mkdir()
        panel = directory / "panel.json"
        atomic_json(panel, dict(pixel_type=phase["profile"], string_lengths=phase["lengths"]))
        remote_run = self.args.remote_dir + "/" + self.run_id + "-%03d" % number
        stop_file, pid_file = remote_run + ".STOP", remote_run + ".load.pid"
        phase_state = dict(name=phase["name"], parameters=phase, status="starting", started_utc=utc(),
                           remote_run=remote_run, remote_stop_file=stop_file, remote_load_pid_file=pid_file,
                           chunks=0, captured_seconds=0.0, validated_seconds=0.0, checked_seconds=0.0,
                           complete_pulses=0, complete_channel_frames=0,
                           violation_count=0, violation_counts={}, affected_chunks=[])
        self.state["phases"].append(phase_state)
        self.state["current_phase"] = phase["name"]
        self.checkpoint()
        sender = load = None
        error = None
        phase_started = time.monotonic()
        phase_end = phase_started + phase["duration"]
        try:
            self.check_cancel()
            if self.remaining() <= CLEANUP_RESERVE:
                raise BenchFailure("insufficient deadline reserve to start phase")
            sender_argv = ["python3", self.args.remote_dir + "/tools/bench_sender.py",
                           "--require-quiet",
                           "--build-dir", self.args.remote_dir + "/build", "--output-dir", remote_run,
                           "--profile", phase["profile"], "--lengths", ",".join(map(str, phase["lengths"])),
                           "--mode", phase["mode"], "--seed", str(phase["seed"]),
                           "--duration", str(phase["duration"] + 120),
                           "--deadline-utc", self.args.deadline_utc_text, "--stop-file", stop_file]
            if phase["mode"] == "fixed":
                sender_argv += ["--color", phase["color"]]
            phase_state["sender_argv"] = sender_argv
            sender = self.spawn(self.ssh + ["exec " + quoted(sender_argv)], directory / "sender.log")
            if phase["load"] != "none":
                seconds = min(3600, int(math.ceil(phase["duration"] + 60)), max(1, int(self.remaining() - 30)))
                if phase["load"] == "ethernet":
                    local_stop = directory / "STOP-UDP"
                    load_argv = [sys.executable, str(ROOT / "tools/bench_udp_load.py"),
                                 "--target", "192.168.68.71", "--seconds", str(seconds),
                                 "--pps", "2000", "--payload-bytes", "128", "--stop-file", str(local_stop)]
                    phase_state["load_argv"] = load_argv
                    load = self.spawn(load_argv, directory / "load.log")
                    load["local_stop_file"] = str(local_stop)
                else:
                    load_argv = [self.args.remote_dir + "/build/bench-load", "--mode", phase["load"], "--seconds", str(seconds)]
                    if phase["load"] == "ddr":
                        load_argv += ["--mib", "32"]
                    phase_state["load_argv"] = load_argv
                    load_command = "set -eu; umask 077; set -C; printf '%s\\n' \"$$\" > " + shlex.quote(pid_file) + "; exec " + quoted(load_argv)
                    load = self.spawn(self.ssh + [load_command], directory / "load.log")
            phase_state["status"] = "running"
            while time.monotonic() < phase_end:
                self.check_cancel()
                available = self.remaining() - CLEANUP_RESERVE - CAPTURE_OVERHEAD - ANALYSIS_TIMEOUT
                seconds = min(self.args.chunk_seconds, phase_end - time.monotonic(), available)
                if seconds < 1:
                    phase_state["stop_reason"] = "deadline_reserve" if available < 1 else "phase_duration"
                    break
                for label, child in (("sender", sender), ("load", load)):
                    if child is not None and child["process"].poll() is not None:
                        raise BenchFailure("%s exited before phase completion; see %s" % (label, child["log_path"]))
                free = shutil.disk_usage("D:\\").free
                if free < FREE_BYTES:
                    raise BenchFailure("D: free space is below the 50 GiB capture floor")
                chunk = directory / ("chunk-%04d" % phase_state["chunks"])
                chunk.mkdir()
                self.state["current_chunk"] = str(chunk.relative_to(self.base))
                self.checkpoint()
                capture_dir = chunk / "capture"
                self.bounded([sys.executable, str(ROOT / "tools/bench_capture.py"), str(capture_dir),
                              "--seconds", str(seconds), "--label", phase["name"]],
                             chunk / "capture.log", seconds + CAPTURE_OVERHEAD)
                metadata = json.loads((capture_dir / "capture.json").read_text())
                if metadata.get("status") != "complete" or metadata.get("mode") != "timed":
                    raise BenchFailure("capture worker did not report a complete timed acquisition")
                if not (capture_dir / "waveform.sal").is_file() or (capture_dir / "waveform.sal").stat().st_size == 0:
                    raise BenchFailure("native capture was not preserved before analysis")
                retained = binary_coverage(capture_dir)
                atomic_json(chunk / "retained-coverage.json", retained)
                for state in (phase_state, self.state):
                    state["captured_seconds"] += retained["duration_s"]
                # Inspect the persistent sender/load only between acquisitions.
                for label, child in (("sender", sender), ("load", load)):
                    if child is not None and child["process"].poll() is not None and time.monotonic() < phase_end:
                        raise BenchFailure("%s exited during the captured interval" % label)
                analysis_path = chunk / "analysis.json"
                command = [sys.executable, str(ROOT / "tools/analyze_capture.py"), str(capture_dir),
                           "--mode", "dld", "--config", str(panel), "--bank-order", "--json", str(analysis_path)]
                if phase["mode"] == "fixed":
                    command += ["--colors", phase["color"]]
                elif phase["mode"] == "cycle":
                    command += ["--sequence", ",".join("%06X" % value for value in CYCLE)]
                else:
                    command += ["--allow-any-color"]
                analysis_code = self.bounded(command, chunk / "analysis.log", ANALYSIS_TIMEOUT,
                                             accepted_codes=(0, 1))
                report = json.loads(analysis_path.read_text())
                counts = report.get("violation_counts")
                count = report.get("violation_count")
                if (report.get("mode") != "dld" or not isinstance(counts, dict) or
                        type(count) is not int or count < 0 or
                        any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in counts.items()) or
                        sum(counts.values()) != count or
                        (analysis_code == 0 and (report.get("pass") is not True or count != 0 or report.get("status") != "pass")) or
                        (analysis_code == 1 and (report.get("pass") is not False or count <= 0 or report.get("status") != "fail"))):
                    raise BenchFailure("invalid/inconsistent waveform analysis report")
                duration = report.get("duration_s")
                if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
                        not math.isfinite(duration) or abs(duration - retained["duration_s"]) > 1e-8):
                    raise BenchFailure("analysis duration differs from binary retained interval")
                coverage = report["coverage"]
                if any(type(coverage.get(key)) is not int or coverage[key] < 0
                       for key in ("complete_pulses", "complete_channel_frames")):
                    raise BenchFailure("invalid waveform coverage counts")
                for state in (phase_state, self.state):
                    state["chunks"] += 1
                    state["checked_seconds"] += retained["duration_s"]
                    if not count:
                        state["validated_seconds"] += retained["duration_s"]
                    state["complete_pulses"] += coverage["complete_pulses"]
                    state["complete_channel_frames"] += coverage["complete_channel_frames"]
                    state["violation_count"] += count
                    for kind, occurrences in counts.items():
                        state["violation_counts"][kind] = state["violation_counts"].get(kind, 0) + occurrences
                    if count:
                        state["affected_chunks"].append(str(chunk.relative_to(self.base)))
                self.event(dict(event="chunk_violations" if count else "chunk_pass", phase=phase["name"],
                                chunk=str(chunk.relative_to(self.base)), retained=retained, coverage=coverage,
                                violation_count=count, violation_counts=counts))
                self.checkpoint()
                print("%s phase=%s chunks=%d captured=%.3fs pulses=%d violations=%d" %
                      ("VIOLATIONS" if count else "PASS", phase["name"], phase_state["chunks"],
                       self.state["captured_seconds"], self.state["complete_pulses"], count), flush=True)
                if count and not self.args.continue_waveform_failures:
                    raise BenchFailure("waveform violation: " + str(analysis_path))
            phase_state.setdefault("stop_reason", "phase_duration")
        except BaseException as exc:
            error = exc
            phase_state["error"] = repr(exc)
            phase_state["traceback"] = traceback.format_exc()
        finally:
            phase_state["status"] = "stopping"
            try:
                self.checkpoint()
            except BaseException as exc:
                if error is None:
                    error = exc
            try:
                self.stop_phase(phase_state, directory, sender, load, remote_run, stop_file, pid_file)
            except BaseException as exc:
                phase_state["cleanup_failure"] = repr(exc)
                if error is None:
                    error = exc
            phase_state["finished_utc"] = utc()
            phase_state["wall_seconds"] = time.monotonic() - phase_started
            phase_state["status"] = "fail" if error else ("completed_with_violations" if phase_state["violation_count"] else "pass")
            atomic_json(directory / "phase-summary.json", phase_state)
            self.event(dict(event="phase_finished", phase=phase_state))
            self.checkpoint()
        if error is not None:
            raise error

    def run(self):
        previous = {}
        def cancel(number, frame):
            if not self.cancel_signal:
                self.cancel_signal = number
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, cancel)
        code = 0
        try:
            self.state["status"] = "running"
            for number, phase in enumerate(self.plan):
                self.check_cancel()
                if self.remaining() < CLEANUP_RESERVE + CAPTURE_OVERHEAD + ANALYSIS_TIMEOUT + 1:
                    self.state["stop_reason"] = "deadline_reserve"
                    break
                self.run_phase(number, phase)
            else:
                self.state["stop_reason"] = "plan_complete"
            self.state["status"] = "completed_with_violations" if self.state["violation_count"] else "pass"
        except BaseException as exc:
            code = 130 if isinstance(exc, Cancelled) else 1
            self.state["status"] = "stopped" if isinstance(exc, Cancelled) else "fail"
            self.state["failures"].append(dict(error=repr(exc), traceback=traceback.format_exc(), utc=utc()))
            print("STOP: %s; artifacts=%s" % (exc, self.base), file=sys.stderr, flush=True)
        finally:
            self.state["finished_utc"] = utc()
            self.state["wall_seconds"] = time.monotonic() - self.started_mono
            self.state["current_chunk"] = None
            self.checkpoint()
            self.event(dict(event="finished", status=self.state["status"], exit=code,
                            captured_seconds=self.state["captured_seconds"],
                            validated_seconds=self.state["validated_seconds"]))
            self.events.close()
            for number, handler in previous.items():
                signal.signal(number, handler)
        return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--remote-dir", required=True)
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--deadline-utc", required=True)
    parser.add_argument("--chunk-seconds", type=float, default=30.0)
    parser.add_argument("--continue-waveform-failures", action="store_true",
                        help="characterize waveform violations with unchanged criteria; operational/format errors still stop")
    args = parser.parse_args(argv)
    args.deadline_utc_text = args.deadline_utc
    try:
        args.deadline_utc = parse_deadline(args.deadline_utc)
        if not re.fullmatch(r"/(root|run)/dld-[A-Za-z0-9_.-]+", args.remote_dir):
            raise ValueError("remote directory must be an isolated /root/dld-NAME or /run/dld-NAME")
        if not math.isfinite(args.chunk_seconds) or not 1 <= args.chunk_seconds <= 300:
            raise ValueError("chunk seconds must be 1..300")
        if not pathlib.Path(args.known_hosts).is_file():
            raise ValueError("known-hosts file is missing")
        plan = normalized_plan(json.loads(pathlib.Path(args.plan).read_text()))
        if args.deadline_utc <= time.time():
            raise ValueError("global deadline has already elapsed")
    except (ValueError, OSError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    return Supervisor(args, plan).run()


if __name__ == "__main__":
    sys.exit(main())
