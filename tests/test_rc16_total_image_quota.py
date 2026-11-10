"""RC16 soft total image quota tests: captured images must NEVER be blocked."""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_image_retention', ROOT / 'src/auto_xy_retention_rc16.py')
ret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ret)


class SoftImageBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runs'
        self.root.mkdir()
        self.now = time.time()

    def add_run(self, name, status, size=0, annotation=0, reported=True, age=3600):
        folder = self.root / name
        folder.mkdir()
        image = folder / '001_t0_attempt_1.jpg'
        image.write_bytes(b'a' * size)
        detected = folder / ('detected_' + image.name)
        if annotation:
            detected.write_bytes(b'b' * annotation)
        if reported:
            report = folder / 'report.json'
            report.write_text(json.dumps({
                'status': status,
                'captures': [{'image': str(image)}],
                'capture_failures': []
            }))
            os.utime(report, (self.now - age, self.now - age))
        return folder, image, detected

    def test_all_image_types_and_unknown_images_count_toward_total(self):
        _, a, da = self.add_run('20261109-090000_print', 'CALIBRATED_AND_VAOC_EXITED',
                                size=100000, annotation=120000)
        unknown = self.root / 'unknown' / 'arbitrary.png'
        unknown.parent.mkdir()
        unknown.write_bytes(b'x' * 200000)
        count, warnings = ret.all_image_usage(self.root)
        self.assertEqual(count, 420000)
        self.assertTrue(a.exists() and da.exists() and unknown.exists())
        self.assertEqual(warnings, [])

    def test_quota_triggers_cleanup_not_camera_capture_refusal(self):
        self.add_run('20261109-090000_print', 'CALIBRATED_AND_VAOC_EXITED',
                     size=450000, annotation=250000)
        self.add_run('20261109-100000_print', 'FAILED_DO_NOT_PRINT',
                     size=450000, annotation=250000)
        chosen, tally, warnings = ret.plan(self.root, now=self.now,
                                            max_total_images_mib=1)
        self.assertGreater(tally['all_images']['bytes'], ret.MIB)
        self.assertGreater(tally['all_images']['files_to_remove'], 0)
        self.assertLessEqual(tally['all_images']['after_bytes'], ret.MIB)
        self.assertGreater(ret.execute(self.root, chosen), 0)
        self.assertLessEqual(ret.all_image_usage(self.root)[0], ret.MIB)

    def test_newer_run_preferred_and_failed_report_preserved(self):
        a, _, _ = self.add_run('20261109-090000_print',
                               'CALIBRATED_AND_VAOC_EXITED', 650000, 100000, age=7200)
        b, _, _ = self.add_run('20261109-100000_print',
                               'FAILED_DO_NOT_PRINT', 650000, 100000, age=3600)
        chosen, _, _ = ret.plan(self.root, now=self.now, max_total_images_mib=1)
        self.assertTrue(any(e['run'] == a for e in chosen))
        self.assertFalse(any(e['run'] == b for e in chosen))
        ret.execute(self.root, chosen)
        self.assertTrue((b/'report.json').exists())

    def test_active_or_unreported_run_images_are_counted_but_never_deleted(self):
        folder, image, detected = self.add_run('20261109-090000_print',
                                                'CALIBRATED_AND_VAOC_EXITED',
                                                1000000, 400000, reported=False)
        selected, tally, warnings = ret.plan(self.root, now=self.now,
                                              max_total_images_mib=1)
        self.assertGreater(tally['all_images']['bytes'], ret.MIB)
        self.assertEqual(ret.execute(self.root, selected), 0)
        self.assertTrue(image.exists() and detected.exists())
        self.assertTrue(any('quota' in w.lower() for w in warnings))

    def test_old_annotated_image_is_recognised_and_reclaimed(self):
        folder, raw, annotated = self.add_run('20261001-090000_print',
                                               'FAILED_DO_NOT_PRINT',
                                               900000, 900000, age=40*86400)
        selected, tally, warnings = ret.plan(self.root, now=self.now,
                                              max_total_images_mib=1)
        names = {e['path'].name for e in selected}
        self.assertIn(raw.name, names)
        self.assertIn(annotated.name, names)
        self.assertEqual(ret.execute(self.root, selected), len(selected))

    def test_changed_report_after_planning_prevents_deletion(self):
        folder, raw, annotated = self.add_run(
            '20261001-090000_print', 'FAILED_DO_NOT_PRINT',
            900000, 900000, age=40*86400)
        selected, _, _ = ret.plan(self.root, now=self.now,
                                  max_total_images_mib=1)
        self.assertTrue(selected)
        report = folder / 'report.json'
        report.write_text(report.read_text() + '\\n ')
        self.assertEqual(ret.execute(self.root, selected), 0)
        self.assertTrue(raw.exists() and annotated.exists())

    def test_replaced_report_with_same_state_prevents_deletion(self):
        folder, raw, annotated = self.add_run(
            '20261001-090000_print', 'FAILED_DO_NOT_PRINT',
            900000, 900000, age=40*86400)
        selected, _, _ = ret.plan(self.root, now=self.now,
                                  max_total_images_mib=1)
        report = folder / 'report.json'
        replacement = folder / 'report.new'
        replacement.write_bytes(report.read_bytes())
        os.replace(replacement, report)
        self.assertEqual(ret.execute(self.root, selected), 0)
        self.assertTrue(raw.exists() and annotated.exists())

    def test_scheduled_controller_forwards_configured_total_image_limit(self):
        controller = (ROOT / 'src/auto_xy_gate_rc16.py').read_text(encoding='utf-8')
        start = controller.index('    def _retention_tick(')
        end = controller.index('    def ensure_commissioned(', start)
        scheduled = controller[start:end]
        self.assertIn("'--max-total-images-mib',str(self.retention_total_images_mib)",
                      scheduled)

    def test_no_hard_capture_gate_is_part_of_retention_policy(self):
        self.assertFalse(hasattr(ret, 'capture_budget'))
        self.assertFalse(hasattr(ret, 'enforce_before_capture'))


if __name__ == '__main__':
    unittest.main()
