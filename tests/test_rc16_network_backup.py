"""Offline SSH streaming tests: no Pi writes, complete footer required."""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "scripts"

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

server = load("rc16_archive_stream", "rc16_archive_historical_backups.py")
client = load("rc16_pc_stream", "rc16_pc_network_backup.py")


class NetworkArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "pi"
        self.home.mkdir()
        self.out = Path(self.tmp.name) / "pc"
        self.out.mkdir()
        (self.home / "old").mkdir()
        (self.home / "old" / "original.cfg").write_bytes(b"keep-original-contents")
        (self.home / "old" / "link").symlink_to("/unavailable/private/source")
        groups = {"old": ("old",)}
        p1 = patch.object(server, "GROUPS", groups)
        p2 = patch.object(client.archive, "GROUPS", groups)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def stream(self):
        buffer = io.BytesIO()
        server.network_stream_group(self.home, "old", buffer)
        return buffer.getvalue()

    def test_complete_stream_verified_on_pc_and_original_unchanged(self):
        data = self.stream()
        self.assertTrue(data.endswith(server.NETWORK_DONE))
        dest = self.out / "old.tar.gz"
        snapshot = client.receive(io.BytesIO(data), dest, "old")
        self.assertEqual(snapshot["files"], 1)
        self.assertEqual((self.home / "old" / "original.cfg").read_bytes(),
                         b"keep-original-contents")
        self.assertEqual(snapshot["entries"]["old/link"]["kind"], "symlink")
        self.assertTrue(server.verify_archive(snapshot, dest))

    def test_truncated_stream_cannot_be_verified(self):
        with self.assertRaisesRegex(client.archive.Refuse, "completion|footer|Missing"):
            client.receive(io.BytesIO(self.stream()[:-12]),
                           self.out / "truncated.tar.gz", "old")

    def test_changed_source_after_tar_emits_no_success_footer(self):
        real_scan = server.scan_group
        calls = 0
        def changing_scan(home, group, *, hash_files):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.home / "old" / "original.cfg").write_bytes(b"changed")
            return real_scan(home, group, hash_files=hash_files)
        buf = io.BytesIO()
        with patch.object(server, "scan_group", side_effect=changing_scan):
            with self.assertRaisesRegex(server.Refuse, "Backup changed"):
                server.network_stream_group(self.home, "old", buf)
        self.assertFalse(buf.getvalue().endswith(server.NETWORK_DONE))

    def test_refuse_unexpected_group(self):
        with self.assertRaisesRegex(client.archive.Refuse, "Unexpected source inventory"):
            client.receive(io.BytesIO(self.stream()),
                           self.out / "other.tar.gz", "other")

    def test_refuse_non_protocol_stdout(self):
        with self.assertRaisesRegex(client.archive.Refuse, "Unexpected SSH stdout"):
            client.receive(io.BytesIO(b"X" * len(server.NETWORK_MAGIC)),
                           self.out / "bad.tar.gz", "old")


if __name__ == "__main__":
    unittest.main()
