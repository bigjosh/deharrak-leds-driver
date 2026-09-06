#!/usr/bin/env python3
"""Repeat visual OPC/UDP scenes locally; Python 3.8+, standard library only."""

import argparse
import datetime
import hashlib
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import time

from exercise_scenes import SCENES, color_at


PROGRAM = "dld-panel-exercise"


def encode_color(rgb):
    """Enforce the power budget at the final packet boundary, for every scene."""
    if (len(rgb) != 3 or any(type(value) is not int or not 0 <= value <= 255
                             for value in rgb) or sum(rgb) > 255):
        raise ValueError("RGB must contain three integer bytes with sum <= 255")
    return struct.pack("!BBHBBB", 0, 0, 3, *rgb)


def select_scene(elapsed, scenes=SCENES):
    cycle, position = divmod(max(0.0, elapsed), sum(scene.duration for scene in scenes))
    for index, scene in enumerate(scenes):
        if position < scene.duration:
            return int(cycle), index, scene, position
        position -= scene.duration
    # Floating point subtraction can leave the final boundary a few ulps out.
    return int(cycle) + 1, 0, scenes[0], 0.0


def run_exercise(targets, fps, seed, duration, stopped, send, report,
                 clock=time.monotonic, sleep=time.sleep):
    """Injected transport/clock keep pacing, shutdown and packet tests offline."""
    if not math.isfinite(fps) or not 0 < fps <= 10:
        raise ValueError("packet rate must be greater than zero and at most 10")
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("duration must be finite and greater than zero")
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("targets must be nonempty and unique")
    interval = 1.0 / fps
    start = clock()
    next_frame = start
    next_progress = start
    current_scene = None
    reason = "error"
    attempted = False
    stats = {target: {"attempted": 0, "sent": 0, "errors": 0,
                      "consecutive_errors": 0, "last_color": None,
                      "last_error": None} for target in targets}
    last_error_report = {target: float("-inf") for target in targets}

    def snapshot(event):
        elapsed = max(0.0, clock() - start)
        cycle, index, scene, position = select_scene(elapsed)
        return {"event": event, "elapsed_seconds": round(elapsed, 3),
                "cycle": cycle + 1, "scene_index": index,
                "scene": scene.name, "scene_seconds": round(position, 3),
                "fps_limit": fps, "targets": {key: dict(value) for key, value in stats.items()}}

    def transmit(colors):
        nonlocal attempted, next_frame
        # Validate the entire frame before transmitting to even the first panel.
        packets = [encode_color(rgb) for rgb in colors]
        for target, rgb, packet in zip(targets, colors, packets):
            attempted = True
            counts = stats[target]
            counts["attempted"] += 1
            try:
                try:
                    send(target, packet)
                finally:
                    # Keep the final black paced even if transport or logging
                    # raises an unexpected error halfway through a frame.
                    next_frame = clock() + interval
            except OSError as error:
                counts["errors"] += 1
                counts["consecutive_errors"] += 1
                counts["last_error"] = str(error)
                now = clock()
                if counts["consecutive_errors"] == 1 or now - last_error_report[target] >= 60:
                    report({"event": "send_error", "target": target,
                            "error": str(error), "errors": counts["errors"]})
                    last_error_report[target] = now
            else:
                counts["sent"] += 1
                counts["last_color"] = list(rgb)
                if counts["consecutive_errors"]:
                    report({"event": "send_recovered", "target": target,
                            "previous_consecutive_errors": counts["consecutive_errors"]})
                counts["consecutive_errors"] = 0
                counts["last_error"] = None

    try:
        report(snapshot("start"))
        while True:
            now = clock()
            requested_stop = stopped()
            if requested_stop:
                reason = str(requested_stop)
                break
            if duration is not None and now - start >= duration:
                reason = "duration"
                break
            if now < next_frame:
                sleep(min(0.05, next_frame - now))
                continue
            cycle, index, scene, position = select_scene(now - start)
            if current_scene != (cycle, index):
                current_scene = (cycle, index)
                report(snapshot("scene"))
            colors = [color_at(scene, position, panel_index, len(targets), cycle, seed)
                      for panel_index in range(len(targets))]
            transmit(colors)
            # Anchor after all sends. Delayed frames never accumulate credit,
            # and each target has at least one full interval between attempts.
            next_frame = clock() + interval
            if clock() >= next_progress:
                report(snapshot("progress"))
                next_progress = clock() + 5.0
    except KeyboardInterrupt:
        reason = "keyboard_interrupt"
    finally:
        if attempted:
            # A normal stop ends black, with the same rate and RGB constraints.
            while True:
                remaining = next_frame - clock()
                if remaining <= 0:
                    break
                sleep(min(0.05, remaining))
            transmit([(0, 0, 0)] * len(targets))
        result = snapshot("finish")
        result["stop_reason"] = reason
        report(result)
    return result


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(str(temporary), str(path))


def ipv4(value):
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        raise argparse.ArgumentTypeError("use a numeric IPv4 address")


def finite_positive(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use a positive finite number")
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("use a positive finite number")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", nargs="+", type=ipv4, help="panel IPv4 addresses, in scene order")
    parser.add_argument("--port", type=int, default=7890)
    parser.add_argument("--fps", type=finite_positive, default=10.0, help="packet limit per panel, at most 10")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--duration", type=finite_positive, help="optional run duration in seconds; default indefinite")
    parser.add_argument("--run-dir", type=Path, help="new directory for logs, status and STOP marker")
    parser.add_argument("--stop", type=Path, metavar="RUN_DIR", help="request graceful stop of an existing run")
    args = parser.parse_args(argv)
    if args.stop is not None:
        if args.targets or args.run_dir is not None:
            parser.error("--stop cannot be combined with --targets or --run-dir")
    elif not args.targets:
        parser.error("--targets is required to start a run")
    if args.fps > 10 or not 1 <= args.port <= 65535:
        parser.error("--fps must be at most 10; --port must be 1..65535")
    if args.targets and len(set(args.targets)) != len(args.targets):
        parser.error("duplicate targets would exceed the per-panel packet limit")
    return args


def main(argv=None):
    args = arguments(argv)
    if args.stop is not None:
        directory = args.stop.resolve()
        with (directory / "run.json").open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("program") != PROGRAM:
            raise ValueError("not a panel exercise run directory")
        (directory / "STOP").touch(exist_ok=True)
        print("Stop requested: " + str(directory))
        return 0

    if args.run_dir is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        args.run_dir = Path(__file__).resolve().parent.parent / "build" / ("panel-exercise-" + stamp + "-" + str(os.getpid()))
    directory = args.run_dir.resolve()
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # Never truncate evidence or share a running directory.
    manifest = {"program": PROGRAM, "pid": os.getpid(), "started_utc": utc_now(),
                "python": sys.version.split()[0], "executable": sys.executable,
                "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ("exercise_panels.py", "exercise_scenes.py")},
                "targets": args.targets, "port": args.port, "fps_limit": args.fps,
                "seed": args.seed, "duration": args.duration, "rgb_sum_limit": 255,
                "playlist_seconds": sum(scene.duration for scene in SCENES),
                "scenes": [{"name": scene.name, "seconds": scene.duration} for scene in SCENES]}
    write_json(directory / "run.json", manifest)
    logger = logging.getLogger(PROGRAM + "." + str(os.getpid()))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(str(directory / "events.log"), maxBytes=1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    def report(event):
        value = dict(event, utc=utc_now(), pid=os.getpid())
        logger.info(json.dumps(value, sort_keys=True))
        if event["event"] in ("start", "scene", "progress", "finish"):
            write_json(directory / "status.json", value)

    caught = [None]

    def handle_signal(number, unused_frame):
        caught[0] = "signal_" + str(number)

    def stopped():
        return caught[0] or ("stop_file" if (directory / "STOP").exists() else None)

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, handle_signal)
    sockets = {}
    try:
        for target in args.targets:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets[target] = sock
            sock.setblocking(False)

        def send(target, packet):
            count = sockets[target].sendto(packet, (target, args.port))
            if count != len(packet):
                raise OSError("partial UDP datagram send")

        print("Panel exercise running; logs and STOP marker: " + str(directory), flush=True)
        result = run_exercise(args.targets, args.fps, args.seed, args.duration,
                              stopped, send, report)
        print("Panel exercise stopped: " + result["stop_reason"], flush=True)
        return 0
    finally:
        for sock in sockets.values():
            sock.close()
        handler.close()
        logger.removeHandler(handler)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print("exercise_panels: " + str(error), file=sys.stderr)
        sys.exit(1)
