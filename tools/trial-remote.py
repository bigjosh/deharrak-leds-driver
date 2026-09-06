#!/usr/bin/env python3
"""Temporary BBG handover, invoked by deploy-trial.sh/.ps1. Python 3.2+."""
from __future__ import print_function

import argparse
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time

LOCK_PATH = "/run/dld.lock"
MODULE_NAME = "dld_quiet"
READY_MARKER = "dld-udp: ready"
FILES = (
    "build/dld-init", "build/dld-send", "build/dld-udp",
    "build/dld-config-check", "build/build-report.txt",
    "kernel/dld_quiet.ko", "tools/bench_prepare.py", "tools/trial-remote.py",
)
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_log = None


class TrialError(RuntimeError):
    pass


def read(path):
    with open(path, "r") as stream:
        return stream.read().strip()


def say(message):
    print(message)
    sys.stdout.flush()
    if _log is not None:
        _log.write(message + "\n")
        _log.flush()


def run(command):
    say("+ " + " ".join(command))
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True,
                               close_fds=True)
    output = process.communicate()[0].strip()
    if output:
        say(output)
    if process.returncode:
        raise TrialError("command failed (exit {0}): {1}".format(
            process.returncode, " ".join(command)))
    return output


def extract_bundle(archive, directory):
    """Accept only this package's bounded regular files, all hash-verified.

    Never call tar.extractall: paths, links and special files in a selected
    archive must not be able to write outside this new runtime directory.
    Validate the entire archive before writing any extracted file.
    """
    expected = set(FILES) | set(["manifest.json"])
    contents = {}
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            if (member.name not in expected or member.name in contents or
                    not member.isfile() or member.size < 0):
                raise TrialError("unexpected archive member: " + member.name)
            total += member.size
            if total > MAX_BUNDLE_BYTES:
                raise TrialError("bundle exceeds 8 MiB unpacked limit")
            data = source.extractfile(member).read(member.size + 1)
            if len(data) != member.size:
                raise TrialError("incomplete archive member: " + member.name)
            contents[member.name] = data
    if set(contents) != expected:
        raise TrialError("bundle file list does not match this launcher version")
    manifest = json.loads(contents["manifest.json"].decode("utf-8"))
    if (manifest.get("schema") != 1 or manifest.get("lock_path") != LOCK_PATH or
            set(manifest.get("files", {})) != set(FILES)):
        raise TrialError("invalid bundle manifest or non-RAM command lock")
    for name in FILES:
        if hashlib.sha256(contents[name]).hexdigest() != manifest["files"][name]:
            raise TrialError("bundle checksum mismatch: " + name)
    with open(os.path.abspath(__file__), "rb") as bootstrap:
        if bootstrap.read() != contents["tools/trial-remote.py"]:
            raise TrialError("bundle and launcher helper differ; rebuild the bundle")
    parents = set(os.path.dirname(name) for name in expected) - set([""])
    for name in expected | parents:
        if os.path.lexists(os.path.join(directory, name)):
            raise TrialError("refusing to replace an existing runtime path: " + name)
    for name in parents:
        os.makedirs(os.path.join(directory, name))
    for name in expected:
        target = os.path.join(directory, name)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(contents[name])
        if name.startswith("build/dld-"):
            os.chmod(target, 0o700)
    return manifest


def check_ram(directory):
    if os.geteuid() != 0:
        raise TrialError("root SSH access is required")
    if not re.match(r"^/run/dld-trial\.[A-Za-z0-9]+$", directory):
        raise TrialError("expected a fresh /run/dld-trial.XXXXXX directory")
    if os.path.realpath(directory) != directory:
        raise TrialError("runtime directory must not be a symlink")
    info = os.stat(directory)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        raise TrialError("runtime directory must be private to root")
    mounts = [line.split() for line in read("/proc/mounts").splitlines()]
    backing = [entry for entry in mounts if entry[1] == "/run" or
               directory == entry[1] or directory.startswith(entry[1].rstrip("/") + "/")]
    backing.sort(key=lambda entry: len(entry[1]))
    if not backing or backing[-1][2] != "tmpfs" or "noexec" in backing[-1][3].split(","):
        raise TrialError("runtime must be on executable tmpfs")
    if len(read("/proc/swaps").splitlines()) != 1:
        raise TrialError("swap must be absent for this RAM-only trial")


def process_identity(pid):
    """Check executable and start time, not a potentially stale udp.pid file."""
    base = "/proc/" + pid
    try:
        before = read(base + "/stat").rsplit(")", 1)[1].split()[19]
        target = os.readlink(base + "/exe")
        after = read(base + "/stat").rsplit(")", 1)[1].split()[19]
    except (IOError, OSError) as error:
        if error.errno in (errno.ENOENT, errno.ESRCH):
            return None  # Exited processes (including zombies) have no exe.
        raise
    if before != after:
        return None  # PID was reused while inspecting it.
    return (pid, target, before)


def running_dld():
    names = set(["dld-init", "dld-send", "dld-udp"])
    processes = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        identity = process_identity(entry)
        if identity is not None:
            name = os.path.basename(identity[1])
            if name.endswith(" (deleted)"):
                name = name[:-10]
            if name in names:
                processes.append(identity)
    return processes


def stop_dld(timeout=20.0):
    """Let in-flight sends finish before unloading the helper; never force kill."""
    existing = running_dld()
    for identity in existing:
        pid, executable, unused_start = identity
        if process_identity(pid) != identity:
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise
        # Log after signaling: a blocked output pipe must not delay the signal
        # after its final identity check on this pre-pidfd kernel.
        say("Stopping existing DLD: PID {0}; {1}".format(pid, executable))
    deadline = time.time() + timeout
    # Python 3.2 has no time.monotonic; bound iterations as well as wall time.
    for unused in range(max(1, int(timeout / 0.1) + 1)):
        remaining = running_dld()
        if not remaining:
            if existing:
                say("Existing DLD processes stopped.")
            return
        if any(identity not in existing for identity in remaining):
            raise TrialError("another DLD process started during handover; stop its supervisor before retrying")
        if time.time() >= deadline:
            break
        time.sleep(0.1)
    raise TrialError("DLD shutdown timed out; helper retained, no process was force-killed")


def preflight(directory, panel, manifest):
    check_ram(directory)
    if (platform.release() != "3.8.13-bone80" or
            manifest.get("kernel_release") != platform.release() or
            not platform.machine().startswith("armv7")):
        raise TrialError("bundle requires the reference ARMv7 Linux 3.8.13-bone80 BBG")
    if run(["systemctl", "is-enabled", "ledscape.service"]) != "enabled":
        raise TrialError("LEDscape must already be enabled at boot for reboot recovery")
    if run(["systemctl", "show", "ledscape.service", "-p", "LoadState"]) != "LoadState=loaded":
        raise TrialError("LEDscape service is not loaded")
    vermagic = run(["modinfo", "-F", "vermagic", os.path.join(directory, "kernel/dld_quiet.ko")])
    if not vermagic.split() or vermagic.split()[0] != platform.release():
        raise TrialError("kernel helper does not match the running kernel")
    run([os.path.join(directory, "build/dld-config-check"), panel])
    run([sys.executable, "-B", os.path.join(directory, "tools/bench_prepare.py"), "--check"])


def start_receiver(directory, options, timeout=20.0):
    log_path = os.path.join(directory, "udp.log")
    pid_path = os.path.join(directory, "udp.pid")
    # Exclusive creation avoids truncating evidence if a directory is reused.
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        with open(os.devnull, "rb") as incoming:
            process = subprocess.Popen([os.path.join(directory, "build/dld-udp")] + options,
                                       stdin=incoming, stdout=output, stderr=subprocess.STDOUT,
                                       close_fds=True, preexec_fn=os.setsid, cwd=directory)
    try:
        descriptor = os.open(pid_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as destination:
            destination.write(str(process.pid) + "\n")
        deadline = time.time() + timeout
        # The count also bounds waiting if the old board's wall clock moves back.
        for unused in range(max(1, int(timeout / 0.1) + 1)):
            if process.poll() is not None:
                raise TrialError("dld-udp exited during startup; inspect " + log_path)
            if READY_MARKER in read(log_path).splitlines() and process.poll() is None:
                say("DLD trial ready: PID {0}; directory {1}".format(process.pid, directory))
                say("Receiver log: " + log_path)
                return process.pid
            if time.time() >= deadline:
                break
            time.sleep(0.1)
        raise TrialError("dld-udp readiness timed out; inspect " + log_path)
    except Exception:
        if process.poll() is None:
            process.terminate()  # Only the receiver launched by this attempt.
        raise


def swap(directory, panel, udp_options):
    # Everything staged above has passed validation before interrupting output.
    stop_dld()
    run(["systemctl", "stop", "ledscape.service"])
    state = run(["systemctl", "show", "ledscape.service", "-p", "ActiveState"])
    pid = run(["systemctl", "show", "ledscape.service", "-p", "MainPID"])
    if state not in ("ActiveState=inactive", "ActiveState=failed") or pid != "MainPID=0":
        raise TrialError("LEDscape did not stop; refusing to initialize")
    if os.path.exists("/sys/module/" + MODULE_NAME):
        run(["rmmod", MODULE_NAME])
        if os.path.exists("/sys/module/" + MODULE_NAME):
            raise TrialError("old dld_quiet helper is still loaded; refusing to initialize")
    run([sys.executable, "-B", os.path.join(directory, "tools/bench_prepare.py"),
         "--apply", os.path.join(directory, "preparation.json")])
    run(["modprobe", "uio_pruss"])
    run(["insmod", os.path.join(directory, "kernel/dld_quiet.ko")])
    if running_dld():
        raise TrialError("another DLD process started during handover; refusing to initialize")
    run([os.path.join(directory, "build/dld-init"), panel])
    return start_receiver(directory, udp_options)


def main():
    global _log
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--panel", required=True)
    parser.add_argument("--no-startup-flash", action="store_true")
    parser.add_argument("--no-idle-flash", action="store_true")
    args = parser.parse_args()
    directory = os.path.abspath(os.getcwd())
    guard = None
    try:
        check_ram(directory)
        descriptor = os.open(os.path.join(directory, "handover.log"),
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _log = os.fdopen(descriptor, "w")
        # Serialize launchers separately from the command lock used by init/send.
        guard = os.open("/run/dld-trial.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(guard).st_mode):
            raise TrialError("deployment lock is not a regular file")
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = extract_bundle(args.bundle, directory)
        panel = os.path.abspath(args.panel)
        if panel != os.path.join(directory, "panel.json"):
            raise TrialError("panel configuration must be the staged panel.json")
        preflight(directory, panel, manifest)
        options = []
        if args.no_startup_flash:
            options.append("--no-startup-flash")
        if args.no_idle_flash:
            options.append("--no-idle-flash")
        swap(directory, panel, options)
        say("Temporary handover complete. Reboot to return to the existing boot setup.")
        return 0
    except Exception as error:
        say("DLD trial failed: " + str(error))
        say("Files and diagnostics retained in " + directory + "; no automatic rollback. Reboot for recovery after runtime changes.")
        return 1
    finally:
        if guard is not None:
            os.close(guard)
        if _log is not None:
            _log.close()
            _log = None


if __name__ == "__main__":
    sys.exit(main())
