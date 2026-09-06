#!/usr/bin/env python3
"""Bounded host-side UDP traffic for the explicitly selected BeagleBone bench.

Example (run only while the operator owns the bench):
  python tools/bench_udp_load.py --target 192.168.68.71 --seconds 60 --stop-file build/bench-20260906/STOP

No receiver/server is required. The operator must select an unused UDP port;
the board may answer with ICMP port-unreachable packets. JSON lines on stdout
report local send attempts, not packets delivered or physical bus activity.
Redirect stdout to a new caller-owned log. The script writes no files, changes
no network settings, and does not access GPIO/PRU or manage board services.
Requires host Python 3.3+ with time.monotonic (the bundled Windows Python works).
"""

import argparse
import json
import math
import os
import signal
import socket
import sys
import time


BENCH_ADDRESS = "192.168.68.71"
MAX_SECONDS = 3600
MAX_PPS = 20000
PROGRESS_SECONDS = 5.0
MAX_CREDIT_SECONDS = 0.005
MAX_CONSECUTIVE_ERRORS = 100


def bounded_integer(low, high):
    def parse(value):
        try:
            number = int(value, 10)
        except ValueError:
            raise argparse.ArgumentTypeError("must be an integer")
        if not low <= number <= high:
            raise argparse.ArgumentTypeError("must be %d..%d" % (low, high))
        return number
    return parse


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True, choices=[BENCH_ADDRESS],
                        help="explicitly select the authorized bench address")
    parser.add_argument("--seconds", required=True,
                        type=bounded_integer(1, MAX_SECONDS))
    parser.add_argument("--pps", default=2000, type=bounded_integer(1, MAX_PPS),
                        help="target packets/second, 1..20000 (default: 2000)")
    parser.add_argument("--payload-bytes", default=128,
                        type=bounded_integer(64, 256),
                        help="UDP payload size, 64..256 (default: 128)")
    parser.add_argument("--port", default=49152,
                        type=bounded_integer(49152, 65535),
                        help="operator-selected unused UDP port (default: 49152)")
    parser.add_argument("--stop-file", metavar="PATH",
                        help="stop when this path exists; never create or remove it")
    return parser.parse_args(argv)


def emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
    sys.stdout.flush()


def run_load(args, sock, stopped, clock=time.monotonic, sleep=time.sleep,
             report=emit):
    """Run with a nonblocking socket; injectable clock/socket allow offline tests."""
    start = clock()
    deadline = start + args.seconds
    previous = start
    tokens = 1.0
    max_credit = max(1, int(math.ceil(args.pps * MAX_CREDIT_SECONDS)))
    next_report = start + PROGRESS_SECONDS
    sent = 0
    attempts = 0
    errors = 0
    consecutive_errors = 0
    error_counts = {}
    first_error = None
    reason = "deadline"
    payload = (b"DLD bench UDP only\x00" + bytes(range(256)))[:args.payload_bytes]
    destination = (args.target, args.port)

    def snapshot(event, now):
        elapsed = max(0.0, now - start)
        return {
            "event": event,
            "target": args.target,
            "port": args.port,
            "requested_seconds": args.seconds,
            "requested_pps": args.pps,
            "payload_bytes": args.payload_bytes,
            "attempts": attempts,
            "sent": sent,
            "errors": errors,
            "elapsed_seconds": elapsed,
            "sent_pps": sent / elapsed if elapsed else 0.0,
            "payload_bytes_sent": sent * args.payload_bytes,
        }

    report(snapshot("start", start))
    while True:
        now = clock()
        requested_stop = stopped()
        if requested_stop:
            reason = requested_stop
            break
        if now >= deadline:
            break
        if now < previous:
            raise RuntimeError("monotonic clock moved backwards")
        tokens = min(max_credit, tokens + (now - previous) * args.pps)
        previous = now
        if now >= next_report:
            report(snapshot("progress", now))
            next_report = now + PROGRESS_SECONDS
        if tokens < 1.0:
            sleep(min((1.0 - tokens) / args.pps, deadline - now, 0.05))
            continue

        # Credit is capped at 5 ms, so a delayed host never catches up with an
        # arbitrarily large burst. Actual achieved rate is measured, not assumed.
        for unused in range(int(tokens)):
            now = clock()
            requested_stop = stopped()
            if requested_stop or now >= deadline:
                reason = requested_stop or "deadline"
                break
            tokens -= 1.0
            attempts += 1
            try:
                count = sock.sendto(payload, destination)
                if count != len(payload):
                    raise OSError("partial UDP datagram send")
            except OSError as error:
                errors += 1
                consecutive_errors += 1
                key = str(getattr(error, "winerror", None) or error.errno or
                          type(error).__name__)
                error_counts[key] = error_counts.get(key, 0) + 1
                if first_error is None:
                    first_error = str(error)
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    reason = "consecutive_socket_errors"
                    break
            else:
                sent += 1
                consecutive_errors = 0
        if requested_stop or now >= deadline or reason == "consecutive_socket_errors":
            break

    result = snapshot("finish", clock())
    result["stop_reason"] = reason
    result["error_counts"] = error_counts
    result["first_error"] = first_error
    report(result)
    return 1 if errors else 0


def main(argv=None):
    args = arguments(argv)
    stop_path = os.path.abspath(args.stop_file) if args.stop_file else None
    caught_signal = [None]

    def handle_signal(number, unused_frame):
        caught_signal[0] = "signal_%d" % number

    def stopped():
        if caught_signal[0]:
            return caught_signal[0]
        if stop_path is not None and os.path.exists(stop_path):
            return "stop_file"
        return None

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    sock = None
    try:
        # A preexisting stop file is a clean no-traffic result. Creating an
        # unbound socket does not contact the board; run_load checks before send.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        return run_load(args, sock, stopped)
    except (OSError, RuntimeError) as error:
        sys.stderr.write("bench_udp_load: %s\n" % error)
        return 1
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    sys.exit(main())
