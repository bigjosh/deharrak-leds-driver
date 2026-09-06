"""Offline release packaging checks; no SSH, GitHub, or device access."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gateway_package", ROOT / "tools/package-gateway.py")
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class PackageTests(unittest.TestCase):
    def setUp(self):
        # Keep test temporaries on the workspace drive, not the host's full C:.
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="gateway-package-test-", dir=str(ROOT / "build"))
        self.root = Path(self.temporary.name)
        for name in pack.GATEWAY_FILES + ("docs/gateway.md", "docs/example.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture " + name + "\n").encode())
        secret = self.root / "build/private-keys/id_ed25519"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE MATERIAL MUST NOT SHIP")
        self.bundle = self.root / "build/dld-trial.tar.gz"
        self.contents = {name: name.encode() for name in pack.NATIVE_FILES}
        self.contents["tools/trial-remote.py"] = (self.root / "tools/trial-remote.py").read_bytes()
        self.manifest = {"schema": 1, "kernel_release": "3.8.13-bone80", "lock_path": "/run/dld.lock",
                         "files": {name: pack.digest(data) for name, data in self.contents.items()}}
        self.make_bundle()

    def tearDown(self):
        resolved = self.root.resolve()
        self.assertEqual(resolved.parent, (ROOT / "build").resolve())
        self.assertTrue(resolved.name.startswith("gateway-package-test-"))
        self.temporary.cleanup()

    def make_bundle(self, extra=None, omit=None):
        with tarfile.open(self.bundle, "w:gz") as archive:
            files = dict(self.contents)
            files["manifest.json"] = json.dumps(self.manifest).encode()
            if omit is not None:
                del files[omit]
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra is not None:
                archive.addfile(extra, io.BytesIO(b""))

    def package(self, destination="out", version="v0.1.0"):
        return pack.package(self.root, self.bundle, self.root / destination, version, "a" * 40)

    def test_archive_is_complete_regular_relative_and_excludes_private_material(self):
        output = self.package()
        with tarfile.open(output) as archive:
            members = archive.getmembers()
            files = {member.name: archive.extractfile(member).read() for member in members}
            self.assertTrue(all(member.isfile() and not member.name.startswith("/")
                                and ".." not in member.name.split("/") for member in members))
            self.assertEqual(archive.getmember("dld-gateway-v0.1.0/dld-deploy").mode, 0o755)
        expected = set(pack.GATEWAY_FILES) | {"docs/gateway.md", "docs/example.md",
                                              "release.json", "build/dld-trial.tar.gz"}
        self.assertEqual(set(files), {"dld-gateway-v0.1.0/" + name for name in expected})
        self.assertFalse(any(b"PRIVATE MATERIAL" in data for data in files.values()))
        release = json.loads(files["dld-gateway-v0.1.0/release.json"])
        self.assertEqual(release["gateway_source_commit"], "a" * 40)
        self.assertEqual(release["native_bundle_sha256"], pack.digest(self.bundle.read_bytes()))
        for name, digest in release["files"].items():
            self.assertEqual(pack.digest(files["dld-gateway-v0.1.0/" + name]), digest)
        self.assertEqual((output.parent / "SHA256SUMS").read_text(),
                         hashlib.sha256(output.read_bytes()).hexdigest() + "  " + output.name + "\n")

    def test_identical_inputs_produce_identical_archives(self):
        self.assertEqual(self.package("one").read_bytes(), self.package("two").read_bytes())

    def test_existing_release_cannot_be_overwritten(self):
        path = self.package()
        before = path.read_bytes()
        self.assertRaises(ValueError, self.package)
        self.assertEqual(path.read_bytes(), before)

    def test_existing_checksum_is_preserved(self):
        (self.root / "out").mkdir()
        checksum = self.root / "out/SHA256SUMS"
        checksum.write_text("existing")
        self.assertRaises(ValueError, self.package)
        self.assertEqual(checksum.read_text(), "existing")
        self.assertFalse((self.root / "out/dld-gateway-v0.1.0.tar.gz").exists())

    def test_mismatched_bootstrap_refuses_package(self):
        (self.root / "tools/trial-remote.py").write_text("new version")
        self.assertRaises(ValueError, self.package)
        self.assertFalse((self.root / "out").exists())

    def test_tampered_native_payload_refuses_package(self):
        self.contents["build/dld-init"] += b"changed"
        self.make_bundle()
        self.assertRaises(ValueError, self.package)

    def test_wrong_kernel_or_non_ram_lock_refuses_package(self):
        for key, value in (("kernel_release", "6.6.1"), ("lock_path", "/var/lock/dld.lock")):
            original = self.manifest[key]
            self.manifest[key] = value
            self.make_bundle()
            self.assertRaises(ValueError, self.package)
            self.manifest[key] = original

    def test_missing_native_member_refuses_package(self):
        self.make_bundle(omit="build/dld-send")
        self.assertRaises(ValueError, self.package)

    def test_duplicate_traversal_and_link_members_refused(self):
        for name, member_type in (("build/dld-send", tarfile.REGTYPE),
                                  ("../escape", tarfile.REGTYPE),
                                  ("build/dld-send", tarfile.SYMTYPE)):
            member = tarfile.TarInfo(name)
            member.type = member_type
            member.linkname = "/outside"
            self.make_bundle(extra=member)
            self.assertRaises(ValueError, self.package)

    def test_invalid_version_cannot_escape_output_directory(self):
        for version in ("../bad", "v0.1.0/other", "main", "v0.1.0\n"):
            self.assertRaises(ValueError, self.package, version=version)

    def test_missing_operating_guide_refused(self):
        (self.root / "docs/gateway.md").unlink()
        self.assertRaises(ValueError, self.package)


if __name__ == "__main__":
    unittest.main()
