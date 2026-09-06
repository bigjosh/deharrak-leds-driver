#!/usr/bin/env python3
"""Offline trial-handover checks; no SSH, board access, or service changes.

Compatible with the target's Python 3.2. System calls are replaced explicitly
rather than depending on unittest.mock, which that interpreter lacks.
"""
from __future__ import print_function

import argparse
import errno
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "trial-remote.py")
trial = types.ModuleType("trial_remote")
trial.__file__ = SOURCE
# Only the import is substituted on Windows. Tests below never call a real
# flock or a real board command on any platform.
try:
    import fcntl
except ImportError:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX, fcntl.LOCK_NB = 2, 4
    sys.modules["fcntl"] = fcntl
    try:
        with open(SOURCE, "r") as stream:
            exec(compile(stream.read(), SOURCE, "exec"), trial.__dict__)
    finally:
        del sys.modules["fcntl"]
else:
    with open(SOURCE, "r") as stream:
        exec(compile(stream.read(), SOURCE, "exec"), trial.__dict__)


class PatchSet(object):
    """Restore every substituted attribute, including absent Linux-only ones."""
    missing = object()

    def __init__(self):
        self.saved = []

    def set(self, owner, name, value):
        self.saved.append((owner, name, getattr(owner, name, self.missing)))
        setattr(owner, name, value)

    def restore(self):
        for owner, name, value in reversed(self.saved):
            if value is self.missing:
                delattr(owner, name)
            else:
                setattr(owner, name, value)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="dld-trial-unit-")
        self.patches = PatchSet()
        self.messages = []
        self.patches.set(trial, "say", self.messages.append)
        self.calls = []

    def path(self, name):
        return os.path.join(self.directory, name)

    def write(self, path, data):
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as stream:
            stream.write(data)

    def bundle_data(self):
        files = dict((name, ("fixture:" + name).encode("ascii")) for name in trial.FILES)
        with open(SOURCE, "rb") as stream:
            files["tools/trial-remote.py"] = stream.read()
        manifest = {"schema": 1, "kernel_release": "3.8.13-bone80",
                    "lock_path": "/run/dld.lock",
                    "files": dict((name, hashlib.sha256(data).hexdigest())
                                  for name, data in files.items())}
        return files, manifest

    def archive(self, files=None, manifest=None, extra=None, omit=None):
        if files is None:
            files, default_manifest = self.bundle_data()
            if manifest is None:
                manifest = default_manifest
        members = dict(files)
        members["manifest.json"] = json.dumps(manifest).encode("utf-8")
        if omit is not None:
            del members[omit]
        path = self.path("bundle.tar.gz")
        with tarfile.open(path, "w:gz") as output:
            for name in sorted(members):
                info = tarfile.TarInfo(name)
                info.size = len(members[name])
                output.addfile(info, io.BytesIO(members[name]))
            if extra is not None:
                info, data = extra
                output.addfile(info, io.BytesIO(data))
        return path

    def extract(self, archive):
        destination = self.path("unpacked")
        os.mkdir(destination)
        return trial.extract_bundle(archive, destination)

    def assert_unpacked_empty(self):
        self.assertEqual(os.listdir(self.path("unpacked")), [])

    def preflight_fixture(self, responses=None, loaded=False, existing=None,
                          kernel="3.8.13-bone80", machine="armv7l"):
        responses = {} if responses is None else responses
        self.patches.set(trial, "check_ram", lambda directory: self.calls.append(["check_ram", directory]))
        self.patches.set(trial.platform, "release", lambda: kernel)
        self.patches.set(trial.platform, "machine", lambda: machine)
        self.patches.set(trial.os.path, "exists", lambda path: loaded if path == "/sys/module/dld_quiet" else False)
        self.patches.set(trial, "running_dld", lambda: [] if existing is None else existing)

        def command(argv):
            self.calls.append(list(argv))
            key = tuple(argv)
            value = responses.get(key)
            if isinstance(value, Exception):
                raise value
            if value is not None:
                return value
            if argv[:2] == ["systemctl", "is-enabled"]:
                return "enabled"
            if argv == ["systemctl", "show", "ledscape.service", "-p", "LoadState"]:
                return "LoadState=loaded"
            if argv[:3] == ["modinfo", "-F", "vermagic"]:
                return "3.8.13-bone80 mod_unload modversions ARMv7 p2v8"
            return "OK"
        self.patches.set(trial, "run", command)
        return {"kernel_release": "3.8.13-bone80"}

    def receiver_fixture(self, lines, statuses=None):
        """Create no process; capture its descriptors and scripted state."""
        owner = self
        self.now = 100.0
        self.spawn = None
        self.terminated = 0
        statuses = [None] if statuses is None else list(statuses)

        class Process(object):
            pid = 43210

            def poll(self):
                return statuses.pop(0) if len(statuses) > 1 else statuses[0]

            def terminate(self):
                owner.terminated += 1

        def popen(argv, **options):
            owner.spawn = (argv, options)
            owner.assertEqual(options["stdin"].read(), b"")
            options["stdout"].write(lines.encode("utf-8"))
            options["stdout"].flush()
            return Process()

        def sleep(seconds):
            self.now += seconds

        self.session_function = lambda: None
        self.patches.set(trial.os, "setsid", self.session_function)
        self.patches.set(trial.subprocess, "Popen", popen)
        self.patches.set(trial.time, "time", lambda: self.now)
        self.patches.set(trial.time, "sleep", sleep)

    def tearDown(self):
        self.patches.restore()
        resolved = os.path.realpath(self.directory)
        self.assertEqual(os.path.dirname(resolved), os.path.realpath(tempfile.gettempdir()))
        self.assertTrue(os.path.basename(resolved).startswith("dld-trial-unit-"))
        shutil.rmtree(resolved)

    def test_valid_bundle_extracts_only_verified_files(self):
        archive = self.archive()
        manifest = self.extract(archive)
        self.assertEqual(manifest["lock_path"], "/run/dld.lock")
        for name in trial.FILES:
            with open(self.path("unpacked/" + name), "rb") as stream:
                self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), manifest["files"][name])

    def test_tampered_payload_rejected_before_extraction(self):
        files, manifest = self.bundle_data()
        files["build/dld-init"] += b"changed"
        self.assertRaises(trial.TrialError, self.extract, self.archive(files, manifest))
        self.assert_unpacked_empty()

    def test_unverified_manifest_lock_rejected_before_extraction(self):
        files, manifest = self.bundle_data()
        manifest["lock_path"] = "/var/lock/dld.lock"
        self.assertRaises(trial.TrialError, self.extract, self.archive(files, manifest))
        self.assert_unpacked_empty()

    def test_missing_required_payload_rejected(self):
        self.assertRaises(trial.TrialError, self.extract, self.archive(omit="build/dld-send"))
        self.assert_unpacked_empty()

    def test_duplicate_member_rejected(self):
        info = tarfile.TarInfo("build/dld-init")
        self.assertRaises(trial.TrialError, self.extract, self.archive(extra=(info, b"")))
        self.assert_unpacked_empty()

    def test_traversal_member_rejected(self):
        info = tarfile.TarInfo("../outside")
        self.assertRaises(trial.TrialError, self.extract, self.archive(extra=(info, b"")))
        self.assert_unpacked_empty()
        self.assertFalse(os.path.exists(self.path("outside")))

    def test_archive_links_and_devices_rejected(self):
        for member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE):
            info = tarfile.TarInfo("build/dld-init")
            info.type = member_type
            info.linkname = "/existing"
            directory = self.path("special-" + str(ord(member_type)))
            os.mkdir(directory)
            archive = self.archive(extra=(info, b""))
            self.assertRaises(trial.TrialError, trial.extract_bundle, archive, directory)
            self.assertEqual(os.listdir(directory), [])

    def test_size_limit_rejects_before_extraction(self):
        self.patches.set(trial, "MAX_BUNDLE_BYTES", 8)
        self.assertRaises(trial.TrialError, self.extract, self.archive())
        self.assert_unpacked_empty()

    def test_bootstrap_must_match_bundle_even_when_manifest_matches(self):
        files, manifest = self.bundle_data()
        files["tools/trial-remote.py"] += b"\n# different helper\n"
        manifest["files"]["tools/trial-remote.py"] = hashlib.sha256(files["tools/trial-remote.py"]).hexdigest()
        self.assertRaises(trial.TrialError, self.extract, self.archive(files, manifest))
        self.assert_unpacked_empty()

    def test_existing_runtime_file_never_overwritten(self):
        archive = self.archive()
        directory = self.path("unpacked")
        self.write(os.path.join(directory, "build/dld-init"), b"keep")
        self.assertRaises(trial.TrialError, trial.extract_bundle, archive, directory)
        with open(os.path.join(directory, "build/dld-init"), "rb") as stream:
            self.assertEqual(stream.read(), b"keep")
        self.assertEqual(os.listdir(directory), ["build"])
        self.assertEqual(os.listdir(os.path.join(directory, "build")), ["dld-init"])

    def ram_fixture(self, uid=0, owner=0, mode=0o40700, realpath=None,
                    mounts=None, swaps="Filename Type Size Used Priority\n"):
        directory = "/run/dld-trial.Abc123"
        info = argparse.Namespace(st_uid=owner, st_mode=mode)
        if mounts is None:
            mounts = ("/dev/root / ext4 rw 0 0\n"
                      "tmpfs /run tmpfs rw,nosuid,nodev 0 0\n")
        texts = {"/proc/mounts": mounts, "/proc/swaps": swaps}
        self.patches.set(trial.os, "geteuid", lambda: uid)
        self.patches.set(trial.os.path, "realpath", lambda path: path if realpath is None else realpath)
        self.patches.set(trial.os, "stat", lambda path: info)
        self.patches.set(trial, "read", lambda path: texts[path])
        return directory

    def test_private_executable_tmpfs_without_swap_is_allowed(self):
        directory = self.ram_fixture()
        trial.check_ram(directory)

    def test_nonroot_refused(self):
        directory = self.ram_fixture(uid=1000)
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_directory_must_be_new_trial_path_under_run(self):
        self.ram_fixture()
        for directory in ("/tmp/dld-trial.Abc123", "/run/dld-trial.Abc123/child",
                          "/run/dld-trial.Abc123/../outside", "/run/dld-trial"):
            self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_symlinked_trial_directory_refused(self):
        directory = self.ram_fixture(realpath="/existing")
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_foreign_owned_trial_directory_refused(self):
        directory = self.ram_fixture(owner=1000)
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_shared_trial_directory_refused(self):
        directory = self.ram_fixture(mode=0o40750)
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_persistent_run_backing_refused(self):
        directory = self.ram_fixture(mounts="/dev/root / ext4 rw 0 0\n")
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_noexec_run_refused(self):
        directory = self.ram_fixture(mounts="tmpfs /run tmpfs rw,noexec 0 0\n")
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_deeper_persistent_mount_overrides_run_tmpfs(self):
        directory = self.ram_fixture(mounts=("tmpfs /run tmpfs rw 0 0\n"
                    "/dev/mmcblk0p2 /run/dld-trial.Abc123 ext4 rw 0 0\n"))
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_swap_refused_even_with_tmpfs(self):
        directory = self.ram_fixture(swaps="Filename Type Size Used Priority\n/swap file 1024 0 -1\n")
        self.assertRaises(trial.TrialError, trial.check_ram, directory)

    def test_preflight_runs_real_config_and_readonly_preparation_check(self):
        manifest = self.preflight_fixture()
        trial.preflight(self.directory, self.path("panel.json"), manifest)
        self.assertEqual(self.calls[0], ["check_ram", self.directory])
        self.assertEqual(self.calls[-2], [self.path("build/dld-config-check"), self.path("panel.json")])
        self.assertEqual(self.calls[-1], [sys.executable, "-B", self.path("tools/bench_prepare.py"), "--check"])
        self.assertFalse(any("--apply" in call or "stop" in call or "insmod" in call for call in self.calls))

    def test_preflight_wrong_kernel_stops_before_commands(self):
        manifest = self.preflight_fixture(kernel="4.1.0")
        self.assertRaises(trial.TrialError, trial.preflight, self.directory, self.path("panel.json"), manifest)
        self.assertEqual(len(self.calls), 1)

    def test_preflight_wrong_architecture_stops_before_commands(self):
        manifest = self.preflight_fixture(machine="x86_64")
        self.assertRaises(trial.TrialError, trial.preflight, self.directory, self.path("panel.json"), manifest)
        self.assertEqual(len(self.calls), 1)

    def test_preflight_loaded_helper_can_be_replaced_after_validation(self):
        manifest = self.preflight_fixture(loaded=True)
        trial.preflight(self.directory, self.path("panel.json"), manifest)
        self.assertEqual(self.calls[-1][-1], "--check")
        self.assertFalse(any("rmmod" in call or "--apply" in call for call in self.calls))

    def test_preflight_existing_sender_can_be_replaced_after_validation(self):
        manifest = self.preflight_fixture(existing=[("314", "/run/prior/build/dld-udp", "42")])
        self.patches.set(trial, "stop_dld", lambda: self.fail("preflight stopped the old sender"))
        trial.preflight(self.directory, self.path("panel.json"), manifest)
        self.assertEqual(self.calls[-1][-1], "--check")

    def test_preflight_disabled_boot_service_rejected(self):
        manifest = self.preflight_fixture({("systemctl", "is-enabled", "ledscape.service"): "disabled"})
        self.assertRaises(trial.TrialError, trial.preflight, self.directory, self.path("panel.json"), manifest)
        self.assertEqual(len(self.calls), 2)

    def test_preflight_wrong_module_vermagic_rejected(self):
        key = ("modinfo", "-F", "vermagic", self.path("kernel/dld_quiet.ko"))
        manifest = self.preflight_fixture({key: "4.4.0 ARMv7"})
        self.assertRaises(trial.TrialError, trial.preflight, self.directory, self.path("panel.json"), manifest)
        self.assertEqual(self.calls[-1], list(key))

    def test_preflight_invalid_panel_never_applies_preparation(self):
        key = (self.path("build/dld-config-check"), self.path("panel.json"))
        manifest = self.preflight_fixture({key: trial.TrialError("bad panel")})
        self.assertRaises(trial.TrialError, trial.preflight, self.directory, self.path("panel.json"), manifest)
        self.assertEqual(self.calls[-1], list(key))

    def swap_fixture(self, failure=None, state="ActiveState=inactive", pid="MainPID=0",
                     loaded=False, stays_loaded=False, new_sender=None, stop_failure=False):
        module_present = [loaded]

        def command(argv):
            self.calls.append(argv)
            if failure is not None and failure(argv):
                raise trial.TrialError("scripted command failure")
            if argv == ["rmmod", "dld_quiet"] and not stays_loaded:
                module_present[0] = False
            if argv[0] == "insmod":
                module_present[0] = True
            if argv[-1] == "ActiveState":
                return state
            if argv[-1] == "MainPID":
                return pid
            return "OK"

        def start(directory, options):
            self.calls.append(["receiver", directory] + options)
            return 123

        def stop():
            self.calls.append(["stop_dld"])
            if stop_failure:
                raise trial.TrialError("old sender did not stop")

        def existing():
            self.calls.append(["running_dld"])
            return [] if new_sender is None else new_sender

        self.patches.set(trial, "run", command)
        self.patches.set(trial, "start_receiver", start)
        self.patches.set(trial, "stop_dld", stop)
        self.patches.set(trial, "running_dld", existing)
        self.patches.set(trial.os.path, "exists",
                         lambda path: module_present[0] if path == "/sys/module/dld_quiet" else False)

    def test_first_swap_stops_owners_before_preparation_and_initialization(self):
        self.swap_fixture()
        self.assertEqual(trial.swap(self.directory, self.path("panel.json"), ["--no-idle-flash"]), 123)
        self.assertEqual(self.calls, [
            ["stop_dld"],
            ["systemctl", "stop", "ledscape.service"],
            ["systemctl", "show", "ledscape.service", "-p", "ActiveState"],
            ["systemctl", "show", "ledscape.service", "-p", "MainPID"],
            [sys.executable, "-B", self.path("tools/bench_prepare.py"), "--apply", self.path("preparation.json")],
            ["modprobe", "uio_pruss"],
            ["insmod", self.path("kernel/dld_quiet.ko")],
            ["running_dld"],
            [self.path("build/dld-init"), self.path("panel.json")],
            ["receiver", self.directory, "--no-idle-flash"]])

    def test_replacement_stops_owners_then_unloads_before_loading_new_helper(self):
        self.swap_fixture(loaded=True)
        self.assertEqual(trial.swap(self.directory, self.path("panel.json"), []), 123)
        self.assertEqual(self.calls[:5], [
            ["stop_dld"],
            ["systemctl", "stop", "ledscape.service"],
            ["systemctl", "show", "ledscape.service", "-p", "ActiveState"],
            ["systemctl", "show", "ledscape.service", "-p", "MainPID"],
            ["rmmod", "dld_quiet"]])
        self.assertEqual(self.calls[5][3], "--apply")
        self.assertEqual(self.calls[7], ["insmod", self.path("kernel/dld_quiet.ko")])

    def test_old_sender_shutdown_failure_prevents_all_runtime_commands(self):
        self.swap_fixture(loaded=True, stop_failure=True)
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls, [["stop_dld"]])

    def test_helper_unload_failure_prevents_preparation_and_new_helper(self):
        self.swap_fixture(loaded=True, failure=lambda argv: argv[0] == "rmmod")
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1], ["rmmod", "dld_quiet"])
        self.assertFalse(any("--apply" in call or "insmod" in call for call in self.calls))

    def test_helper_still_loaded_after_rmmod_prevents_reinitialization(self):
        self.swap_fixture(loaded=True, stays_loaded=True)
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1], ["rmmod", "dld_quiet"])

    def test_sender_reappearing_before_init_prevents_new_receiver(self):
        self.swap_fixture(new_sender=[("314", "/run/prior/build/dld-udp", "99")])
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1], ["running_dld"])
        self.assertFalse(any(call[0] == self.path("build/dld-init") for call in self.calls))

    def test_failed_service_with_no_process_can_be_replaced(self):
        self.swap_fixture(state="ActiveState=failed")
        self.assertEqual(trial.swap(self.directory, self.path("panel.json"), []), 123)

    def test_stop_failure_prevents_init_and_has_no_rollback(self):
        self.swap_fixture(failure=lambda argv: argv[:2] == ["systemctl", "stop"])
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1], ["systemctl", "stop", "ledscape.service"])

    def test_live_service_prevents_init(self):
        self.swap_fixture(state="ActiveState=active")
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1][-1], "MainPID")

    def test_remaining_service_pid_prevents_init(self):
        self.swap_fixture(pid="MainPID=22")
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1][-1], "MainPID")

    def test_init_failure_does_not_launch_or_restart_service(self):
        self.swap_fixture(failure=lambda argv: argv[0] == self.path("build/dld-init"))
        self.assertRaises(trial.TrialError, trial.swap, self.directory, self.path("panel.json"), [])
        self.assertEqual(self.calls[-1], [self.path("build/dld-init"), self.path("panel.json")])

    def test_receiver_detaches_and_records_actual_child_pid(self):
        self.receiver_fixture("dld-udp: listening\ndld-udp: ready\n")
        self.assertEqual(trial.start_receiver(self.directory, ["--no-startup-flash"]), 43210)
        self.assertEqual(self.spawn[0], [self.path("build/dld-udp"), "--no-startup-flash"])
        options = self.spawn[1]
        self.assertIs(options["preexec_fn"], self.session_function)
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["cwd"], self.directory)
        self.assertEqual(options["stderr"], trial.subprocess.STDOUT)
        self.assertEqual(trial.read(self.path("udp.pid")), "43210")
        self.assertEqual(self.terminated, 0)

    def test_listening_without_ready_times_out_and_preserves_evidence(self):
        self.receiver_fixture("dld-udp: listening\n")
        self.assertRaises(trial.TrialError, trial.start_receiver, self.directory, [], 0.2)
        self.assertEqual(self.terminated, 1)
        self.assertEqual(trial.read(self.path("udp.log")), "dld-udp: listening")
        self.assertEqual(trial.read(self.path("udp.pid")), "43210")

    def test_marker_must_be_a_complete_line(self):
        self.receiver_fixture("prefix dld-udp: ready suffix\n")
        self.assertRaises(trial.TrialError, trial.start_receiver, self.directory, [], 0)
        self.assertEqual(self.terminated, 1)

    def test_dead_receiver_never_counts_as_ready(self):
        self.receiver_fixture("dld-udp: ready\n", statuses=[5])
        self.assertRaises(trial.TrialError, trial.start_receiver, self.directory, [], 0)
        self.assertEqual(self.terminated, 0)

    def test_receiver_exit_between_marker_and_alive_check_is_failure(self):
        self.receiver_fixture("dld-udp: ready\n", statuses=[None, 5, 5])
        self.assertRaises(trial.TrialError, trial.start_receiver, self.directory, [], 0)
        self.assertEqual(self.terminated, 0)

    def test_receiver_log_collision_prevents_process_creation(self):
        self.receiver_fixture("dld-udp: ready\n")
        self.write(self.path("udp.log"), b"original")
        self.assertRaises(OSError, trial.start_receiver, self.directory, [], 0)
        self.assertIsNone(self.spawn)
        self.assertEqual(trial.read(self.path("udp.log")), "original")

    def test_pid_collision_stops_only_new_child_and_preserves_old_pid(self):
        self.receiver_fixture("dld-udp: ready\n")
        self.write(self.path("udp.pid"), b"original")
        self.assertRaises(OSError, trial.start_receiver, self.directory, [], 0)
        self.assertEqual(self.terminated, 1)
        self.assertEqual(trial.read(self.path("udp.pid")), "original")

    def test_receiver_wait_is_bounded_if_wall_clock_goes_back(self):
        self.receiver_fixture("dld-udp: listening\n")
        count = [0]
        def sleep(seconds):
            self.now -= 10
            count[0] += 1
        self.patches.set(trial.time, "sleep", sleep)
        self.assertRaises(trial.TrialError, trial.start_receiver, self.directory, [], 0.2)
        self.assertLessEqual(count[0], 3)
        self.assertEqual(self.terminated, 1)

    def main_fixture(self, failed=None, panel=None, flags=None):
        self.events = []
        panel = self.path("panel.json") if panel is None else panel
        flags = [] if flags is None else flags
        self.patches.set(trial.sys, "argv", [SOURCE, "--bundle", self.path("bundle.tar.gz"),
                                            "--panel", panel] + flags)
        self.patches.set(trial.os, "getcwd", lambda: self.directory)
        self.patches.set(trial.os, "O_NOFOLLOW", getattr(trial.os, "O_NOFOLLOW", 0))
        original_open = trial.os.open

        def open_file(path, flags, mode=0o777):
            if path == "/run/dld-trial.lock":
                path = self.path("deployment.lock")
            return original_open(path, flags, mode)

        def operation(name, answer=None):
            def call(*args):
                self.events.append((name, args))
                if failed == name:
                    raise trial.TrialError("scripted " + name + " failure")
                return answer
            return call

        self.patches.set(trial.os, "open", open_file)
        self.patches.set(trial.fcntl, "flock", operation("lock"))
        self.patches.set(trial, "check_ram", operation("ram"))
        self.patches.set(trial, "extract_bundle", operation("extract", {"fixture": True}))
        self.patches.set(trial, "preflight", operation("preflight"))
        self.patches.set(trial, "swap", operation("swap", 42))

    def test_main_completes_all_preflight_before_swap_and_passes_flags(self):
        self.main_fixture(flags=["--no-startup-flash", "--no-idle-flash"])
        self.assertEqual(trial.main(), 0)
        self.assertEqual([event[0] for event in self.events], ["ram", "lock", "extract", "preflight", "swap"])
        self.assertEqual(self.events[-1][1], (self.directory, self.path("panel.json"),
                                            ["--no-startup-flash", "--no-idle-flash"]))
        self.assertIsNone(trial._log)

    def test_main_failed_preflight_never_swaps_and_keeps_handover_log(self):
        self.main_fixture(failed="preflight")
        self.assertEqual(trial.main(), 1)
        self.assertEqual([event[0] for event in self.events], ["ram", "lock", "extract", "preflight"])
        self.assertTrue(os.path.isfile(self.path("handover.log")))
        self.assertTrue(any("no automatic rollback" in message for message in self.messages))
        self.assertIsNone(trial._log)

    def test_main_failed_lock_prevents_extraction(self):
        self.main_fixture(failed="lock")
        self.assertEqual(trial.main(), 1)
        self.assertEqual([event[0] for event in self.events], ["ram", "lock"])

    def test_main_refuses_panel_outside_staged_directory(self):
        self.main_fixture(panel=self.path("some-other-panel.json"))
        self.assertEqual(trial.main(), 1)
        self.assertEqual([event[0] for event in self.events], ["ram", "lock", "extract"])

    def stat_line(self, start="456", comm="dld-udp"):
        # /proc/PID/stat field 2 can contain spaces and parentheses; starttime
        # is field 22. Distinct adjacent values catch an off-by-one parse.
        return "100 (" + comm + ") " + " ".join(
            ["S"] + [str(field) for field in range(4, 22)] + [start, "23", "24"])

    def identity_fixture(self, before=None, after=None, executable=None):
        before = self.stat_line() if before is None else before
        after = before if after is None else after
        executable = "/run/prior/build/dld-udp" if executable is None else executable
        texts = [before, after]

        def read(path):
            self.calls.append(["read", path])
            value = texts.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        def readlink(path):
            self.calls.append(["readlink", path])
            if isinstance(executable, Exception):
                raise executable
            return executable

        self.patches.set(trial, "read", read)
        self.patches.set(trial.os, "readlink", readlink)

    def test_process_identity_handles_comm_spaces_and_parentheses(self):
        self.identity_fixture(before=self.stat_line("98765", "odd (name) tail"))
        self.assertEqual(trial.process_identity("100"),
                         ("100", "/run/prior/build/dld-udp", "98765"))
        self.assertEqual(self.calls, [["read", "/proc/100/stat"],
                                     ["readlink", "/proc/100/exe"],
                                     ["read", "/proc/100/stat"]])

    def test_process_identity_retains_deleted_executable_path(self):
        self.identity_fixture(executable="/run/prior/build/dld-udp (deleted)")
        self.assertEqual(trial.process_identity("100"),
                         ("100", "/run/prior/build/dld-udp (deleted)", "456"))

    def test_process_identity_refuses_pid_reused_during_inspection(self):
        self.identity_fixture(after=self.stat_line("999"))
        self.assertIsNone(trial.process_identity("100"))

    def test_process_identity_tolerates_exit_before_stat(self):
        self.identity_fixture(before=OSError(errno.ENOENT, "exited"))
        self.assertIsNone(trial.process_identity("100"))
        self.assertEqual(self.calls, [["read", "/proc/100/stat"]])

    def test_process_identity_tolerates_exit_before_executable_read(self):
        self.identity_fixture(executable=OSError(errno.ESRCH, "exited"))
        self.assertIsNone(trial.process_identity("100"))

    def test_process_identity_tolerates_exit_after_executable_read(self):
        self.identity_fixture(after=IOError(errno.ENOENT, "exited"))
        self.assertIsNone(trial.process_identity("100"))

    def test_process_identity_does_not_hide_permission_failure(self):
        self.identity_fixture(executable=OSError(errno.EACCES, "denied"))
        self.assertRaises(OSError, trial.process_identity, "100")

    def test_running_dld_finds_all_command_types_and_deleted_executables(self):
        identities = {"100": None,
                      "200": ("200", "/run/prior/build/dld-udp (deleted)", "22"),
                      "300": ("300", "/opt/dld/bin/dld-send", "33"),
                      "400": ("400", "/run/new/build/dld-init", "44")}
        self.patches.set(trial.os, "listdir", lambda path: ["self", "100", "200", "300", "400"])
        self.patches.set(trial, "process_identity", lambda pid: identities[pid])
        self.assertEqual(trial.running_dld(), [identities[pid] for pid in ("200", "300", "400")])

    def test_running_dld_ignores_unrelated_and_similarly_named_executables(self):
        names = ["python3", "dld-udp-backup", "my-dld-send", "dld-init.sh",
                 "dld-udp (deleted)extra", "dld-udp (deleted) (deleted)"]
        identities = dict((str(index), (str(index), "/opt/" + name, "42"))
                          for index, name in enumerate(names))
        self.patches.set(trial.os, "listdir", lambda path: ["self"] + list(identities))
        self.patches.set(trial, "process_identity", lambda pid: identities[pid])
        self.assertEqual(trial.running_dld(), [])

    def stopping_fixture(self, snapshots, identities=None, kill_error=None,
                         reverse_clock=False):
        snapshots = list(snapshots)
        originals = dict((identity[0], identity) for identity in snapshots[0])
        if identities is not None:
            originals.update(identities)
        self.signals = []
        self.sleeps = []
        self.now = 100.0

        def running():
            self.calls.append(["running_dld"])
            return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

        def identity(pid):
            self.calls.append(["identity", pid])
            return originals[pid]

        def kill(pid, sig):
            self.signals.append((pid, sig))
            if kill_error is not None:
                raise kill_error

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now += -10.0 if reverse_clock else seconds

        self.patches.set(trial, "running_dld", running)
        self.patches.set(trial, "process_identity", identity)
        self.patches.set(trial.os, "kill", kill)
        self.patches.set(trial.time, "time", lambda: self.now)
        self.patches.set(trial.time, "sleep", sleep)

    def test_stop_without_dld_sends_no_signals_and_does_not_sleep(self):
        self.stopping_fixture([[]])
        trial.stop_dld()
        self.assertEqual(self.signals, [])
        self.assertEqual(self.sleeps, [])

    def test_stop_waits_for_all_original_commands_to_finish(self):
        udp = ("100", "/run/prior/build/dld-udp", "11")
        send = ("200", "/run/prior/build/dld-send", "22")
        init = ("300", "/run/prior/build/dld-init", "33")
        self.stopping_fixture([[udp, send, init], [udp, send], [send], []])
        trial.stop_dld()
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM),
                                       (200, trial.signal.SIGTERM),
                                       (300, trial.signal.SIGTERM)])
        self.assertEqual(len(self.sleeps), 2)
        self.assertEqual(self.calls[:4], [["running_dld"], ["identity", "100"],
                                         ["identity", "200"], ["identity", "300"]])

    def test_stop_does_not_signal_process_that_exited_before_revalidation(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        self.stopping_fixture([[old], []], identities={"100": None})
        trial.stop_dld()
        self.assertEqual(self.signals, [])

    def test_stop_does_not_signal_unrelated_process_reusing_pid(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        other = ("100", "/usr/bin/python3", "99")
        self.stopping_fixture([[old], []], identities={"100": other})
        trial.stop_dld()
        self.assertEqual(self.signals, [])

    def test_stop_detects_dld_reusing_pid_without_signaling_it(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        replacement = ("100", "/run/prior/build/dld-udp", "99")
        self.stopping_fixture([[old], [replacement]], identities={"100": replacement})
        self.assertRaises(trial.TrialError, trial.stop_dld)
        self.assertEqual(self.signals, [])

    def test_stop_tolerates_exit_between_revalidation_and_sigterm(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        self.stopping_fixture([[old], []], kill_error=OSError(errno.ESRCH, "exited"))
        trial.stop_dld()
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM)])

    def test_stop_does_not_hide_signal_permission_failure(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        self.stopping_fixture([[old]], kill_error=OSError(errno.EPERM, "denied"))
        self.assertRaises(OSError, trial.stop_dld)
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM)])
        self.assertEqual(self.sleeps, [])

    def test_stop_detects_supervisor_respawn_and_does_not_kill_replacement(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        replacement = ("200", "/run/prior/build/dld-udp", "99")
        self.stopping_fixture([[old], [old], [replacement]])
        self.assertRaises(trial.TrialError, trial.stop_dld)
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM)])

    def test_stop_detects_new_dld_after_initially_empty_scan(self):
        replacement = ("200", "/run/prior/build/dld-udp", "99")
        self.stopping_fixture([[], [replacement]])
        self.assertRaises(trial.TrialError, trial.stop_dld)
        self.assertEqual(self.signals, [])

    def test_stop_timeout_leaves_inflight_process_without_sigkill(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        self.stopping_fixture([[old]])
        self.assertRaises(trial.TrialError, trial.stop_dld, 0.2)
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM)])
        self.assertLessEqual(len(self.sleeps), 3)

    def test_stop_timeout_is_bounded_when_wall_clock_moves_backward(self):
        old = ("100", "/run/prior/build/dld-udp", "11")
        self.stopping_fixture([[old]], reverse_clock=True)
        self.assertRaises(trial.TrialError, trial.stop_dld, 0.2)
        self.assertEqual(self.signals, [(100, trial.signal.SIGTERM)])
        self.assertLessEqual(len(self.sleeps), 3)


if __name__ == "__main__":
    unittest.main()
