#!/usr/bin/env python3
"""Package the already-built BBG bundle for a Linux/Pi gateway; Python 3.8+."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parent.parent
NATIVE_FILES = {
    "build/dld-init", "build/dld-send", "build/dld-udp",
    "build/dld-config-check", "build/build-report.txt",
    "kernel/dld_quiet.ko", "tools/bench_prepare.py", "tools/trial-remote.py",
}
GATEWAY_FILES = (
    "dld-deploy", "README.md", "spec.md", "todo.md",
    "config/panel.example.json", "tools/deploy-trial.sh",
    "tools/deploy-trial.ps1", "tools/deploy-trial.bat", "tools/trial-remote.py",
    "tools/exercise_panels.py", "tools/exercise_scenes.py",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_bundle(data, bootstrap):
    files = {}
    total = 0
    allowed = NATIVE_FILES | {"manifest.json"}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if member.name not in allowed or member.name in files or not member.isfile():
                raise ValueError("unexpected native bundle member: " + member.name)
            total += member.size
            if member.size < 0 or total > 8 * 1024 * 1024:
                raise ValueError("native bundle exceeds its 8 MiB size limit")
            files[member.name] = archive.extractfile(member).read()
    if set(files) != allowed:
        raise ValueError("incomplete native bundle")
    manifest = json.loads(files["manifest.json"])
    if (manifest.get("schema") != 1 or manifest.get("lock_path") != "/run/dld.lock" or
            manifest.get("kernel_release") != "3.8.13-bone80" or
            set(manifest.get("files", {})) != NATIVE_FILES):
        raise ValueError("unsupported native bundle manifest")
    for name in NATIVE_FILES:
        if digest(files[name]) != manifest["files"][name]:
            raise ValueError("native bundle checksum mismatch: " + name)
    if files["tools/trial-remote.py"] != bootstrap:
        raise ValueError("native bundle and gateway bootstrap differ; rebuild the native bundle")
    return manifest


def package(root, bundle, output_directory, version, source_commit):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("use a version such as v0.1.0")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source commit must be a complete Git commit SHA")
    root = Path(root).resolve()
    payloads = {}
    # Explicit source paths and top-level Markdown docs only. Never include
    # build directories wholesale: they can contain SSH keys and bench data.
    paths = list(GATEWAY_FILES)
    paths += sorted(str(path.relative_to(root)).replace("\\", "/")
                    for path in (root / "docs").glob("*.md"))
    if "docs/gateway.md" not in paths:
        raise ValueError("gateway operating guide is missing")
    for name in paths:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("release source must be a regular file: " + name)
        path.resolve().relative_to(root)
        payloads[name] = path.read_bytes()
    bundle_data = Path(bundle).read_bytes()
    check_bundle(bundle_data, payloads["tools/trial-remote.py"])
    payloads["build/dld-trial.tar.gz"] = bundle_data
    release = {"schema": 1, "version": version, "gateway_source_commit": source_commit,
               "native_bundle_sha256": digest(bundle_data),
               "bbg_kernel": "3.8.13-bone80", "bbg_architecture": "armv7l",
               "gateway": "Linux/POSIX shell with OpenSSH scp -O support; no compiler needed",
               "files": {name: digest(data) for name, data in sorted(payloads.items())}}
    payloads["release.json"] = (json.dumps(release, indent=2, sort_keys=True) + "\n").encode()

    top = "dld-gateway-" + version
    filename = top + ".tar.gz"
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    output = output_directory / filename
    checksum = output_directory / "SHA256SUMS"
    if output.exists() or output.is_symlink() or checksum.exists() or checksum.is_symlink():
        raise ValueError("release archive/checksum already exists; choose a fresh output directory")
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, data in sorted(payloads.items()):
                member = tarfile.TarInfo(top + "/" + name)
                member.size = len(data)
                member.mode = 0o755 if name == "dld-deploy" or name.endswith(".sh") else 0o644
                member.mtime = 0
                archive.addfile(member, io.BytesIO(data))
    packed = buffer.getvalue()
    with output.open("xb") as stream:
        stream.write(packed)
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(digest(packed) + "  " + filename + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--bundle", type=Path, default=ROOT / "build/dld-trial.tar.gz")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()
    output = package(ROOT, args.bundle, args.output_dir, args.version, commit)
    print(output)
    print((output.parent / "SHA256SUMS").read_text().strip())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        raise SystemExit("package-gateway: " + str(error))
