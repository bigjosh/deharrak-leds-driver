#!/usr/bin/env python3
"""Exercise the POSIX launcher with fake ssh/scp, never contacting a target."""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "tools", "deploy-trial.sh")
FAKE_TOOL = '''#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
path = os.environ['DLD_FAKE_LOG']
try:
    with open(path) as source:
        previous = [json.loads(line) for line in source]
except IOError:
    previous = []
with open(path, 'a') as destination:
    destination.write(json.dumps([name] + sys.argv[1:]) + '\\n')
phase = 'prepare' if name == 'ssh' and not previous else 'handover'
if name == 'scp':
    phase = 'scp' + str(1 + sum(row[0] == 'scp' for row in previous))
if os.environ.get('DLD_FAKE_FAIL') == phase:
    sys.exit(int(os.environ.get('DLD_FAKE_STATUS', '7')))
if phase == 'prepare':
    print(os.environ.get('DLD_FAKE_DIRECTORY', '/run/dld-trial.Ab12Z9'))
elif phase == 'handover':
    print('DLD trial ready: fake receiver')
'''


@unittest.skipUnless(os.name == "posix", "POSIX shell launcher; Windows has native argv checks")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="dld-launcher-unit-")
        self.bin = os.path.join(self.directory, "fake bin")
        os.mkdir(self.bin)
        for name in ("ssh", "scp"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as destination:
                destination.write(FAKE_TOOL)
            os.chmod(path, 0o700)
        self.panel = os.path.join(self.directory, "panel with spaces.json")
        self.bundle = os.path.join(self.directory, "bundle with spaces.tar.gz")
        self.known = os.path.join(self.directory, "known hosts")
        for path in (self.panel, self.bundle, self.known):
            with open(path, "w") as destination:
                destination.write("test fixture\n")
        self.log = os.path.join(self.directory, "calls.jsonl")
        self.environment = os.environ.copy()
        for key in list(self.environment):
            if key.startswith("DLD_FAKE_"):
                del self.environment[key]
        self.environment["PATH"] = self.bin + os.pathsep + self.environment.get("PATH", "")
        self.environment["DLD_FAKE_LOG"] = self.log

    def tearDown(self):
        path = os.path.realpath(self.directory)
        self.assertEqual(os.path.dirname(path), os.path.realpath(tempfile.gettempdir()))
        self.assertTrue(os.path.basename(path).startswith("dld-launcher-unit-"))
        shutil.rmtree(path)

    def launch(self, target="192.0.2.50", options=None):
        command = ["sh", LAUNCHER, target, self.panel, "--bundle", self.bundle]
        command.extend(options or [])
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=self.environment, universal_newlines=True)
        out, err = process.communicate()
        try:
            with open(self.log) as source:
                calls = [json.loads(line) for line in source]
        except IOError:
            calls = []
        return process.returncode, out, err, calls

    def test_transfer_and_handover_keep_paths_and_strict_ssh_options(self):
        status, out, err, calls = self.launch(options=["--known-hosts", self.known])
        self.assertEqual(status, 0, err)
        self.assertEqual([row[0] for row in calls], ["ssh", "scp", "scp", "scp", "ssh"])
        for row in calls:
            self.assertIn("BatchMode=yes", row)
            self.assertIn("ConnectTimeout=10", row)
            self.assertIn("StrictHostKeyChecking=yes", row)
            self.assertIn('UserKnownHostsFile="' + self.known + '"', row)
        for row, local, remote in zip(calls[1:4],
                [self.bundle, self.panel, os.path.join(ROOT, "tools", "trial-remote.py")],
                ["bundle.tar.gz", "panel.json", "trial-bootstrap.py"]):
            self.assertIn("-O", row)
            self.assertEqual(row[-2], local)
            self.assertEqual(row[-1], "root@192.0.2.50:/run/dld-trial.Ab12Z9/" + remote)
        self.assertEqual(calls[-1][-1], "cd /run/dld-trial.Ab12Z9 && python3 -B trial-bootstrap.py --bundle bundle.tar.gz --panel panel.json")
        self.assertIn("DLD trial is running", out)

    def test_ipv6_and_flash_options(self):
        status, out, err, calls = self.launch("2001:db8::50",
                ["--no-startup-flash", "--no-idle-flash"])
        self.assertEqual(status, 0, err)
        self.assertEqual(calls[0][-2], "root@2001:db8::50")
        self.assertEqual(calls[1][-1], "root@[2001:db8::50]:/run/dld-trial.Ab12Z9/bundle.tar.gz")
        self.assertTrue(calls[-1][-1].endswith(" --no-startup-flash --no-idle-flash"))

    def test_invalid_targets_never_invoke_ssh(self):
        for target in ["-oProxyCommand=x", "root@host", "host;reboot", "host/path",
                       "[::1]", "1:2:3", "1:::2", "12345::1", "a..b", "a\nb"]:
            status, out, err, calls = self.launch(target)
            self.assertEqual(status, 2, target)
            self.assertEqual(calls, [], target)

    def test_missing_file_rejected_before_ssh(self):
        os.unlink(self.bundle)
        status, out, err, calls = self.launch()
        self.assertEqual(status, 2)
        self.assertEqual(calls, [])

    def test_unknown_option_rejected_before_ssh(self):
        status, out, err, calls = self.launch(options=["--port", "9000"])
        self.assertEqual(status, 2)
        self.assertEqual(calls, [])

    def test_remote_directory_response_cannot_become_shell_code(self):
        self.environment["DLD_FAKE_DIRECTORY"] = "/run/dld-trial.Ab12Z9; reboot"
        status, out, err, calls = self.launch()
        self.assertEqual(status, 2)
        self.assertEqual(len(calls), 1)

    def test_ssh_preflight_failure_propagates_without_transfer(self):
        self.environment.update({"DLD_FAKE_FAIL": "prepare", "DLD_FAKE_STATUS": "255"})
        status, out, err, calls = self.launch()
        self.assertEqual(status, 255)
        self.assertEqual(len(calls), 1)

    def test_transfer_failure_never_starts_handover(self):
        self.environment.update({"DLD_FAKE_FAIL": "scp2", "DLD_FAKE_STATUS": "7"})
        status, out, err, calls = self.launch()
        self.assertEqual(status, 7)
        self.assertEqual([row[0] for row in calls], ["ssh", "scp", "scp"])
        self.assertIn("retained /run/dld-trial.Ab12Z9", err)

    def test_handover_failure_is_not_reported_as_success(self):
        self.environment.update({"DLD_FAKE_FAIL": "handover", "DLD_FAKE_STATUS": "9"})
        status, out, err, calls = self.launch()
        self.assertEqual(status, 9)
        self.assertEqual(len(calls), 5)
        self.assertNotIn("DLD trial is running", out)
        self.assertIn("reboot to recover", err)


if __name__ == "__main__":
    unittest.main()
