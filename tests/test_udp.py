#!/usr/bin/env python3
"""Real loopback UDP tests against the fake sender; no hardware, Python 3.2+."""
from __future__ import print_function

import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest


BINARY = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build/test-udp")
if len(sys.argv) > 1:
    del sys.argv[1]


def opc(rgb, channel=0, command=0, payload=None):
    if payload is None:
        payload = bytes(bytearray([(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255]))
    return struct.pack("!BBH", channel, command, len(payload)) + payload


def wait_until(condition, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def available_port():
    probe = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        probe.bind(("::", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


class Receiver(object):
    def __init__(self, owner, bind="127.0.0.1", delay=0, fail_after=0,
                 port=None, ready=True, arguments=None, startup=False, idle=False,
                 fake_clock=False):
        self.owner = owner
        self.port = available_port() if port is None else port
        self.path = os.path.join(owner.directory, "sender-{0}.log".format(len(owner.receivers)))
        self.error_path = self.path + ".stderr"
        self.output_path = self.path + ".stdout"
        self.clock_path = self.path + ".clock"
        self.stderr = open(self.error_path, "wb")
        self.stdout = open(self.output_path, "wb")
        environment = os.environ.copy()
        environment.update({"DLD_TEST_LOG": self.path,
                            "DLD_TEST_DELAY_MS": str(delay),
                            "DLD_TEST_FAIL_AFTER": str(fail_after)})
        # Do not inherit test clock settings from the invoking shell.
        environment.pop("DLD_TEST_CLOCK", None)
        if fake_clock:
            self.set_clock(0)
            environment["DLD_TEST_CLOCK"] = self.clock_path
        command = [BINARY, "--port", str(self.port)]
        if not startup:
            command.append("--no-startup-flash")
        if not idle:
            command.append("--no-idle-flash")
        if bind is not None:
            command.extend(["--bind", bind])
        if arguments is not None:
            command = [BINARY] + arguments
        self.process = subprocess.Popen(command, stdout=self.stdout,
                                        stderr=self.stderr, env=environment)
        owner.receivers.append(self)
        if ready:
            if not wait_until(lambda: "listening on" in self.errors() or
                              self.process.poll() is not None):
                raise AssertionError("receiver did not start: " + self.errors())
            if self.process.poll() is not None:
                raise AssertionError("receiver startup failed: " + self.errors())

    def read(self, path):
        try:
            with open(path, "r") as source:
                return source.read()
        except IOError:
            return ""

    def set_clock(self, offset_ms):
        # POSIX rename replaces atomically, including while the receiver reads.
        temporary = self.clock_path + ".new"
        with open(temporary, "w") as destination:
            destination.write(str(offset_ms) + "\n")
        os.rename(temporary, self.clock_path)

    def errors(self):
        return self.read(self.error_path)

    def lines(self):
        return self.read(self.path).splitlines()

    def colors(self, prefix="DONE"):
        return [int(line.split()[1], 16) for line in self.lines()
                if line.startswith(prefix + " ")]

    def send(self, packet, ipv6=False, broadcast=False):
        channel = socket.socket(socket.AF_INET6 if ipv6 else socket.AF_INET,
                                socket.SOCK_DGRAM)
        try:
            if broadcast:
                channel.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            address = "127.255.255.255" if broadcast else ("::1" if ipv6 else "127.0.0.1")
            channel.sendto(packet, (address, self.port))
        finally:
            channel.close()

    def stop(self, number=signal.SIGTERM):
        if self.process.poll() is None:
            self.process.send_signal(number)
        if not wait_until(lambda: self.process.poll() is not None):
            raise AssertionError("receiver did not stop: " + self.errors())
        return self.process.returncode


class UdpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="dld-udp-unit-")
        self.receivers = []

    def tearDown(self):
        for receiver in self.receivers:
            if receiver.process.poll() is None:
                # A test may fail while the process is deliberately SIGSTOPed.
                receiver.process.send_signal(signal.SIGCONT)
                receiver.process.kill()
                receiver.process.wait()
            receiver.stderr.close()
            receiver.stdout.close()
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def test_first_pixel_channel_and_trailing_bytes(self):
        receiver = Receiver(self)
        receiver.send(opc(0x123456, channel=255, payload=b"\x12\x34\x56\xAA\xBB\xCC") +
                      opc(0xabcdef))
        self.assertTrue(wait_until(lambda: receiver.colors() == [0x123456]))
        self.assertEqual(receiver.stop(), 0)
        self.assertEqual(receiver.lines(), ["OPEN", "SEND 123456", "DONE 123456", "CLOSE"])
        self.assertEqual(receiver.read(receiver.output_path), "")

    def test_malformed_and_unsupported_packets_are_ignored(self):
        receiver = Receiver(self)
        packets = [b"", b"\0", b"\0\0\0", b"\0\0\0\0", b"\0\0\0\2\x12\x34",
                   b"\0\0\0\6\x12\x34\x56", b"\0\0\xff\xff\x12\x34\x56",
                   opc(0x123456, command=1)]
        for packet in packets:
            receiver.send(packet)
        receiver.send(opc(0x010203))
        self.assertTrue(wait_until(lambda: receiver.colors() == [0x010203]))
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("received=9 valid=1 malformed=7 unsupported=1", receiver.errors())

    def test_duplicate_colors_are_sent_again_and_one_open_is_retained(self):
        receiver = Receiver(self)
        for count in range(3):
            receiver.send(opc(0xabcdef))
            self.assertTrue(wait_until(lambda: len(receiver.colors()) == count + 1))
        self.assertEqual(receiver.stop(), 0)
        self.assertEqual(receiver.colors(), [0xabcdef] * 3)
        self.assertEqual(receiver.lines().count("OPEN"), 1)
        self.assertEqual(receiver.lines().count("CLOSE"), 1)

    def test_latest_valid_survives_later_invalid_packet(self):
        receiver = Receiver(self)
        receiver.process.send_signal(signal.SIGSTOP)
        time.sleep(0.04)
        receiver.send(opc(1))
        receiver.send(opc(2))
        receiver.send(b"bad")
        receiver.send(opc(3, command=1))
        receiver.process.send_signal(signal.SIGCONT)
        self.assertTrue(wait_until(lambda: receiver.colors() == [2]))
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("coalesced=1 sent=1", receiver.errors())

    def test_batch_limit_sends_before_draining_entire_backlog(self):
        receiver = Receiver(self)
        receiver.process.send_signal(signal.SIGSTOP)
        time.sleep(0.04)
        for rgb in range(1, 121):
            receiver.send(opc(rgb))
        receiver.process.send_signal(signal.SIGCONT)
        self.assertTrue(wait_until(lambda: len(receiver.colors()) >= 2))
        self.assertEqual(receiver.stop(), 0)
        self.assertEqual(receiver.colors(), [64, 120])
        self.assertIn("received=120 valid=120", receiver.errors())
        self.assertIn("coalesced=118 sent=2", receiver.errors())

    def test_twenty_hz_ingress_with_slower_sends_coalesces_to_latest(self):
        receiver = Receiver(self, delay=80)
        started = time.time()
        for rgb in range(1, 22):
            receiver.send(opc(rgb))
            remaining = started + rgb * 0.05 - time.time()
            if remaining > 0:
                time.sleep(remaining)
        self.assertTrue(wait_until(lambda: receiver.colors() and receiver.colors()[-1] == 21))
        self.assertEqual(receiver.stop(), 0)
        colors = receiver.colors()
        self.assertEqual(colors[-1], 21)
        self.assertLess(len(colors), 21)
        self.assertEqual(colors, sorted(set(colors)))
        self.assertEqual(receiver.lines().count("OPEN"), 1)

    def test_twenty_hz_ingress_with_thirty_ms_sends_delivers_each_color(self):
        receiver = Receiver(self, delay=30)
        started = time.time()
        for rgb in range(1, 21):
            # Maintain a minimum 50 ms between datagrams. A delayed scheduler
            # may lengthen the test; it must not turn clean 20 Hz into a burst.
            # There is no per-packet wait for DONE or hardware interaction.
            receiver.send(opc(rgb))
            if rgb < 20:
                time.sleep(0.05)
        self.assertTrue(wait_until(lambda: receiver.colors() and receiver.colors()[-1] == 20))
        elapsed = time.time() - started
        self.assertEqual(receiver.stop(), 0)
        self.assertEqual(receiver.colors(), list(range(1, 21)),
                         "30 ms synthetic sends lost a paced update ({0:.3f} s)".format(elapsed))
        self.assertIn("received=20 valid=20", receiver.errors())
        self.assertIn("coalesced=0 sent=20", receiver.errors())

    def test_default_dual_stack_accepts_ipv4_and_ipv6(self):
        receiver = Receiver(self, bind=None)
        receiver.send(opc(1))
        self.assertTrue(wait_until(lambda: receiver.colors() == [1]))
        receiver.send(opc(2), ipv6=True)
        self.assertTrue(wait_until(lambda: receiver.colors() == [1, 2]))
        self.assertEqual(receiver.stop(), 0)

    def test_default_dual_stack_accepts_ipv4_loopback_broadcast(self):
        receiver = Receiver(self, bind=None)
        receiver.send(opc(0x123456), broadcast=True)
        self.assertTrue(wait_until(lambda: receiver.colors() == [0x123456]))
        self.assertEqual(receiver.stop(), 0)

    def test_endpoint_is_exclusive(self):
        receiver = Receiver(self, bind=None)
        duplicate = Receiver(self, port=receiver.port, ready=False)
        self.assertTrue(wait_until(lambda: duplicate.process.poll() is not None))
        self.assertEqual(duplicate.process.returncode, 3)
        self.assertIn("bind UDP", duplicate.errors())
        self.assertEqual(duplicate.lines(), [])
        self.assertEqual(receiver.stop(), 0)

    def test_idle_signals_exit_successfully_and_release_socket(self):
        for number in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
            receiver = Receiver(self)
            started = time.time()
            self.assertEqual(receiver.stop(number), 0)
            self.assertLess(time.time() - started, 1.0)
            self.assertEqual(receiver.lines(), ["OPEN", "CLOSE"])
            replacement = Receiver(self, port=receiver.port)
            self.assertEqual(replacement.stop(), 0)

    def test_sender_failure_stops_without_retry(self):
        receiver = Receiver(self, fail_after=1)
        receiver.send(opc(0x112233))
        self.assertTrue(wait_until(lambda: receiver.process.poll() is not None))
        self.assertEqual(receiver.process.returncode, 5)
        self.assertEqual(receiver.lines(), ["OPEN", "SEND 112233", "FAIL", "CLOSE"])
        self.assertIn("fake critical submitted-frame failure", receiver.errors())
        self.assertIn("sent=0", receiver.errors())

    def test_inflight_signal_preserves_critical_failure(self):
        receiver = Receiver(self, delay=150)
        receiver.send(opc(0xff0000))
        self.assertTrue(wait_until(lambda: receiver.colors("SEND") == [0xff0000]))
        self.assertEqual(receiver.stop(), 5)
        self.assertEqual(receiver.lines(), ["OPEN", "SEND ff0000", "FAIL", "CLOSE"])

    def test_invalid_arguments_and_help_do_not_open_sender(self):
        arguments = [["--port", "0"], ["--port", "65536"], ["--port", "-1"],
                     ["--port", "1x"], ["--port", "999999999999999999999"],
                     ["--port"], ["--bind", ""], ["--bind", "localhost"], ["--unknown"]]
        for args in arguments:
            receiver = Receiver(self, ready=False, arguments=args)
            self.assertTrue(wait_until(lambda: receiver.process.poll() is not None))
            self.assertEqual(receiver.process.returncode, 2, str(args))
            self.assertEqual(receiver.lines(), [])
        receiver = Receiver(self, ready=False, arguments=["--help"])
        self.assertTrue(wait_until(lambda: receiver.process.poll() is not None))
        self.assertEqual(receiver.process.returncode, 0)
        self.assertIn("usage: dld-udp", receiver.read(receiver.output_path))
        self.assertIn("--no-startup-flash", receiver.read(receiver.output_path))
        self.assertIn("--no-idle-flash", receiver.read(receiver.output_path))
        self.assertEqual(receiver.lines(), [])

    def flash_complete(self, receiver, color, count=1):
        colors = receiver.colors()
        return colors.count(color) >= count and len(colors) >= 3 and colors[-1] == 0

    def assert_smooth_flash(self, colors, color):
        self.assertEqual(colors[0], 0)
        self.assertEqual(colors[-1], 0)
        self.assertGreaterEqual(len(colors), 5)
        self.assertLess(len(colors), 30)  # 80 ms blocking sends gate the frame rate.
        self.assertIn(color, colors)
        peak = colors.index(color)
        self.assertEqual(colors[:peak], sorted(colors[:peak]))
        self.assertEqual(colors[peak:], sorted(colors[peak:], reverse=True))
        self.assertTrue(all((rgb & ~color) == 0 for rgb in colors))

    def test_default_startup_green_and_idle_red(self):
        receiver = Receiver(self, startup=True, idle=True, delay=80, fake_clock=True)
        self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0x00ff00)))
        green = receiver.colors()
        self.assert_smooth_flash(green, 0x00ff00)
        receiver.set_clock(61000)
        self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0xff0000)))
        self.assert_smooth_flash(receiver.colors()[len(green):], 0xff0000)
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("sent=0 flash_frames={0}".format(len(receiver.colors())), receiver.errors())
        self.assertIn("startup_flashes=1 idle_flashes=1 interrupted_flashes=0", receiver.errors())
        self.assertEqual(receiver.lines().count("OPEN"), 1)
        # Every selected frame completes before the next frame is submitted.
        events = receiver.lines()[1:-1]
        self.assertEqual(len(events), 2 * len(receiver.colors()))
        for index in range(0, len(events), 2):
            self.assertEqual(events[index].replace("SEND", "DONE"), events[index + 1])

    def test_independent_opt_out_flags(self):
        startup = Receiver(self, startup=True, idle=False, delay=80, fake_clock=True)
        self.assertTrue(wait_until(lambda: self.flash_complete(startup, 0x00ff00)))
        green = startup.colors()
        startup.set_clock(600000)
        time.sleep(0.35)
        self.assertEqual(startup.colors(), green)
        self.assertEqual(startup.stop(), 0)
        self.assertIn("startup_flashes=1 idle_flashes=0", startup.errors())
        idle = Receiver(self, startup=False, idle=True, delay=80, fake_clock=True)
        self.assertEqual(idle.colors("SEND"), [])
        idle.set_clock(61000)
        self.assertTrue(wait_until(lambda: self.flash_complete(idle, 0xff0000)))
        self.assertEqual(idle.stop(), 0)
        self.assertIn("startup_flashes=0 idle_flashes=1", idle.errors())
        neither = Receiver(self, fake_clock=True)
        neither.set_clock(600000)
        time.sleep(0.35)
        self.assertEqual(neither.stop(), 0)
        self.assertEqual(neither.lines(), ["OPEN", "CLOSE"])

    def test_clock_jump_during_send_preserves_flash_endpoints(self):
        receiver = Receiver(self, startup=True, delay=200, fake_clock=True)
        self.assertTrue(wait_until(lambda: receiver.colors("SEND") == [0]))
        receiver.set_clock(2000)
        self.assertTrue(wait_until(lambda: receiver.colors() == [0, 0x00ff00, 0]))
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("flash_frames=3 startup_flashes=1", receiver.errors())

    def test_udp_color_interrupts_each_flash_without_appended_black(self):
        for startup in [True, False]:
            receiver = Receiver(self, startup=startup, idle=True,
                                delay=80, fake_clock=True)
            if not startup:
                receiver.set_clock(61000)
            self.assertTrue(wait_until(lambda: len(receiver.colors("SEND")) >= 2))
            receiver.send(opc(0x123456))
            self.assertTrue(wait_until(lambda: receiver.colors()[-1:] == [0x123456]))
            colors = receiver.colors()
            time.sleep(0.35)
            self.assertEqual(receiver.colors(), colors)
            self.assertEqual(receiver.stop(), 0)
            self.assertIn("valid=1", receiver.errors())
            self.assertIn("sent=1", receiver.errors())
            self.assertIn("startup_flashes=0 idle_flashes=0 interrupted_flashes=1", receiver.errors())

    def test_invalid_input_during_flash_does_not_interrupt(self):
        receiver = Receiver(self, startup=True, delay=80)
        self.assertTrue(wait_until(lambda: len(receiver.colors("SEND")) >= 2))
        receiver.send(b"bad")
        receiver.send(opc(0, command=1))
        self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0x00ff00)))
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("received=2 valid=0 malformed=1 unsupported=1", receiver.errors())
        self.assertIn("startup_flashes=1 idle_flashes=0 interrupted_flashes=0", receiver.errors())

    def test_every_received_datagram_restarts_idle_interval(self):
        for packet in [b"bad", opc(0, command=1), opc(0x123456)]:
            receiver = Receiver(self, idle=True, delay=80, fake_clock=True)
            receiver.set_clock(59000)
            receiver.send(packet)
            time.sleep(0.35)  # Allow the real nonblocking socket to consume it.
            before = receiver.colors()
            receiver.set_clock(61000)
            time.sleep(0.35)
            self.assertEqual(receiver.colors("SEND"), before)
            receiver.set_clock(120000)
            self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0xff0000)))
            self.assertEqual(receiver.stop(), 0)
            self.assertIn("received=1", receiver.errors())
            self.assertIn("idle_flashes=1", receiver.errors())

    def test_red_interval_restarts_after_completed_final_black(self):
        receiver = Receiver(self, idle=True, delay=80, fake_clock=True)
        receiver.set_clock(61000)
        self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0xff0000)))
        first = receiver.colors()
        time.sleep(0.35)
        self.assertEqual(receiver.colors("SEND"), first)  # No overdue catch-up loop.
        receiver.set_clock(120000)
        time.sleep(0.35)
        self.assertEqual(receiver.colors("SEND"), first)
        receiver.set_clock(123000)
        self.assertTrue(wait_until(lambda: self.flash_complete(receiver, 0xff0000, count=2)))
        self.assertEqual(receiver.stop(), 0)
        self.assertIn("startup_flashes=0 idle_flashes=2 interrupted_flashes=0", receiver.errors())

    def test_queued_udp_takes_priority_over_overdue_idle_flash(self):
        receiver = Receiver(self, idle=True, delay=80, fake_clock=True)
        receiver.process.send_signal(signal.SIGSTOP)
        time.sleep(0.04)
        receiver.set_clock(61000)
        receiver.send(opc(0x123456))
        receiver.process.send_signal(signal.SIGCONT)
        self.assertTrue(wait_until(lambda: receiver.colors() == [0x123456]))
        time.sleep(0.35)
        self.assertEqual(receiver.stop(), 0)
        self.assertEqual(receiver.colors(), [0x123456])
        self.assertIn("flash_frames=0 startup_flashes=0 idle_flashes=0", receiver.errors())

    def test_status_send_failure_stops_without_recovery_flash(self):
        receiver = Receiver(self, startup=True, delay=80, fail_after=2)
        self.assertTrue(wait_until(lambda: receiver.process.poll() is not None))
        self.assertEqual(receiver.process.returncode, 5)
        self.assertEqual(len(receiver.colors("SEND")), 2)
        self.assertEqual(receiver.colors(), [0])
        self.assertIn("flash_frames=1 startup_flashes=0", receiver.errors())
        self.assertEqual(receiver.lines()[-2:], ["FAIL", "CLOSE"])

    def test_signal_during_status_send_preserves_critical_failure(self):
        receiver = Receiver(self, startup=True, delay=200)
        self.assertTrue(wait_until(lambda: receiver.colors("SEND") == [0]))
        self.assertEqual(receiver.stop(), 5)
        self.assertEqual(receiver.lines(), ["OPEN", "SEND 000000", "FAIL", "CLOSE"])
        self.assertIn("flash_frames=0 startup_flashes=0", receiver.errors())

    def test_clock_failure_closes_sender_and_exits(self):
        receiver = Receiver(self, idle=True, fake_clock=True)
        os.unlink(receiver.clock_path)
        self.assertTrue(wait_until(lambda: receiver.process.poll() is not None))
        self.assertEqual(receiver.process.returncode, 3)
        self.assertEqual(receiver.lines(), ["OPEN", "CLOSE"])
        self.assertIn("read monotonic clock", receiver.errors())


if __name__ == "__main__":
    unittest.main()
