#!/usr/bin/env python3
"""Windows/desktop SSH pull of AutoXY historical backup archives.

The printer reads and streams; archives and receipts exist only on this PC.
No delete capability. Requires Python 3 and Windows OpenSSH ssh.exe.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "autoxy_archive", HERE / "rc16_archive_historical_backups.py")
if not spec or not spec.loader:
    raise SystemExit("Missing companion rc16_archive_historical_backups.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)

CHUNK = 4 * 1024 * 1024
MAX_MANIFEST = 15 * 1024 * 1024
MAX_STREAM = 32 * 1024 ** 3
REMOTE_PATH = "/home/pi/AutoXY_Historical_Archive_Tool/rc16_archive_historical_backups.py"


def sha256(path):
    return archive.digest(path)


def exactly(stream, count):
    result = bytearray()
    while len(result) < count:
        block = stream.read(min(CHUNK, count - len(result)))
        if not block:
            raise archive.Refuse("SSH stream truncated")
        result.extend(block)
    return bytes(result)


def receive(stream, dest, expected_group):
    """Parse framed stream, reject interrupted copy, verify tar members."""
    magic = exactly(stream, len(archive.NETWORK_MAGIC))
    archive.require(magic == archive.NETWORK_MAGIC, "Unexpected SSH stdout header")
    length_line = stream.readline(32)
    archive.require(length_line.endswith(b"\n") and
                    length_line[:-1].isdigit(), "Invalid stream manifest length")
    size = int(length_line[:-1])
    archive.require(0 < size <= MAX_MANIFEST, "Oversized stream manifest")
    snapshot = json.loads(exactly(stream, size))
    archive.require(snapshot.get("group") == expected_group and
                    isinstance(snapshot.get("entries"), dict) and
                    snapshot.get("bytes", -1) <= archive.MAX_BYTES,
                    "Unexpected source inventory")
    archive.require(not dest.exists(), "Refuse to overwrite existing archive")
    count = 0
    with dest.open("xb") as output:
        while True:
            part = stream.read(CHUNK)
            if not part:
                break
            count += len(part)
            archive.require(count <= MAX_STREAM, "Archive stream exceeded limit")
            output.write(part)
        output.flush()
        os.fsync(output.fileno())
    with dest.open("r+b") as output:
        archive.require(count > len(archive.NETWORK_DONE),
                        "Missing archive payload")
        output.seek(-len(archive.NETWORK_DONE), os.SEEK_END)
        archive.require(output.read() == archive.NETWORK_DONE,
                        "Missing completed source recheck footer")
        output.truncate(count - len(archive.NETWORK_DONE))
        output.flush()
        os.fsync(output.fileno())
    archive.verify_archive(snapshot, dest)
    return snapshot


def pull_group(host, user, group, target):
    # Fixed remote script path and allowlisted group: no user-supplied shell code.
    command = ["ssh", "-T", f"{user}@{host}",
               f"python3 -B {REMOTE_PATH} network-stream --group {group}"]
    print("Pulling", group, "through SSH (archive stored on PC)", flush=True)
    tar_path = target / (group + ".tar.gz")
    with (target / (group + ".ssh-stderr.txt")).open("xb") as error:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error)
        try:
            snapshot = receive(process.stdout, tar_path, group)
            process.stdout.close()
            code = process.wait()
            archive.require(code == 0, "SSH/source verification failed")
        except BaseException:
            process.kill()
            process.wait()
            raise
    # Write the private manifest only after successful SSH exit and tar check.
    (target / (group + ".manifest.json")).write_text(
        json.dumps(snapshot, sort_keys=True, indent=2), encoding="utf-8")
    print("VERIFIED:", group, snapshot["files"], "source files", flush=True)
    return {"archive": tar_path.name, "sha256": sha256(tar_path),
            "source_bytes": snapshot["bytes"], "source_files": snapshot["files"],
            "manifest": group + ".manifest.json"}


def verify_local(folder):
    receipt_path = folder / "receipt.json"
    archive.require(receipt_path.is_file() and not receipt_path.is_symlink(),
                    "Missing completed receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    archive.require(receipt.get("schema") == 1 and
                    receipt.get("policy") == "PC_ONLY_NO_PI_WRITES" and
                    set(receipt.get("groups", {})) == set(archive.GROUPS),
                    "Invalid/incomplete receipt")
    for group, info in receipt["groups"].items():
        archive.require(info["archive"] == group + ".tar.gz" and
                        info["manifest"] == group + ".manifest.json",
                        "Unexpected receipt file names")
        path = folder / info["archive"]
        manifest_path = folder / info["manifest"]
        archive.require(path.is_file() and not path.is_symlink() and
                        manifest_path.is_file() and not manifest_path.is_symlink(),
                        "Missing archive/manifest")
        archive.require(manifest_path.stat().st_size <= MAX_MANIFEST,
                        "Oversized private manifest")
        snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
        archive.require(snapshot.get("group") == group and
                        sha256(path) == info["sha256"], "Archive checksum mismatch")
        archive.verify_archive(snapshot, path)
        print("REVERIFIED:", group, flush=True)
    print("PASS: All four archives independently verified on this PC.")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    pull = sub.add_parser("pull")
    pull.add_argument("--host", required=True)
    pull.add_argument("--user", default="pi")
    pull.add_argument("--destination", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--archive-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            verify_local(Path(args.archive_dir))
            return 0
        archive.require(all(ch.isalnum() or ch in ".-_" for ch in args.host) and
                        all(ch.isalnum() or ch in "-_" for ch in args.user),
                        "Invalid SSH host/user")
        archive.require(shutil.which("ssh") is not None, "OpenSSH client not found")
        dest = Path(args.destination).expanduser().resolve()
        archive.require(dest.is_dir(), "Create your destination folder first")
        archive.require(shutil.disk_usage(dest).free >= 6 * 1024 ** 3,
                        "Destination needs at least 6 GiB free")
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = dest / ("AutoXY_PC_Backup_" + stamp)
        target.mkdir(mode=0o700, exist_ok=False)
        result = {"schema": 1, "policy": "PC_ONLY_NO_PI_WRITES",
                  "created_utc": stamp, "groups": {}}
        for group in archive.GROUPS:
            result["groups"][group] = pull_group(args.host, args.user, group, target)
        receipt = target / "receipt.json"
        with receipt.open("x", encoding="utf-8") as out:
            json.dump(result, out, indent=2, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        verify_local(target)
        print("COMPLETE PC BACKUP:", target)
        print("All Pi originals preserved. No delete operation exists.")
        return 0
    except (archive.Refuse, OSError, ValueError, KeyError,
            json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print("REFUSED:", exc, file=sys.stderr)
        print("Pi source files remain untouched. Treat partial PC folder as incomplete.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
