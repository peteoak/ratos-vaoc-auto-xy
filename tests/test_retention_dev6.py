"""Offline safety tests for RC15 dev6 retention planner."""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "auto_xy_retention.py"
spec = importlib.util.spec_from_file_location("auto_xy_retention", MODULE)
ret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ret)


class StorageRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = time.time()

    def run_dir(self, stamp, status, age_days, image=True):
        folder = self.root / (stamp + "_print")
        folder.mkdir()
        captures = []
        if image:
            photo = folder / "001_t0_attempt_1.jpg"
            photo.write_bytes(b"example")
            captures = [{"image": str(photo)}]
        report = folder / "report.json"
        report.write_text(json.dumps({"status": status, "captures": captures}))
        when = self.now - age_days * 86400
        import os
        os.utime(report, (when, when))
        return folder

    def test_success_expiry_preserves_report(self):
        folder = self.run_dir("20260101-010101", "CALIBRATED_AND_VAOC_EXITED", 8)
        selected, _, _ = ret.plan(self.root, now=self.now)
        self.assertEqual({x["path"].name for x in selected}, {"001_t0_attempt_1.jpg"})
        self.assertEqual(ret.execute(self.root, selected), 1)
        self.assertTrue((folder / "report.json").exists())

    def test_failed_images_retained_until_thirty_days(self):
        folder = self.run_dir("20260101-010101", "FAILED_DO_NOT_PRINT", 20)
        selected, _, _ = ret.plan(self.root, now=self.now)
        self.assertFalse(selected)
        self.assertTrue((folder / "001_t0_attempt_1.jpg").exists())

    def test_report_expires_with_known_artifacts(self):
        folder = self.run_dir("20260101-010101", "CALIBRATED_AND_VAOC_EXITED", 91)
        selected, _, _ = ret.plan(self.root, now=self.now)
        self.assertEqual(len(selected), 2)
        self.assertEqual(ret.execute(self.root, selected), 2)

    def test_unfinished_and_unknown_artifacts_protected(self):
        folder = self.run_dir("20260101-010101", "CALIBRATED_AND_VAOC_EXITED", 91)
        (folder / "personal.txt").write_text("never delete")
        pending = self.run_dir("20260102-010101", "IN_PROGRESS", 91)
        selected, _, warnings = ret.plan(self.root, now=self.now)
        self.assertFalse(selected)
        self.assertTrue(warnings)
        self.assertTrue((pending / "report.json").exists())

    def test_replaced_file_not_deleted(self):
        folder = self.run_dir("20260101-010101", "CALIBRATED_AND_VAOC_EXITED", 8)
        selected, _, _ = ret.plan(self.root, now=self.now)
        photo = folder / "001_t0_attempt_1.jpg"
        photo.write_bytes(b"new-image")
        self.assertEqual(ret.execute(self.root, selected), 0)
        self.assertTrue(photo.exists())


if __name__ == "__main__":
    unittest.main()
