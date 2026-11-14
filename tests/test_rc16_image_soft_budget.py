"""Offline regression tests for RC16 automatic soft-budget image cleanup.

These tests never access the printer or run live retention.
"""
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_image_retention', ROOT / 'src/auto_xy_retention_rc16.py')
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RC16ImageSoftBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.results = root / 'print_gate_runs'
        self.references = root / 'nozzle_references'
        self.results.mkdir()
        self.references.mkdir()
        self.now = time.time()

    def make_run(self, name, status='CALIBRATED_AND_VAOC_EXITED',
                 size_kib=700, age_days=1):
        run = self.results / name
        run.mkdir()
        image = run / '001_preflight_t0_attempt_1.jpg'
        image.write_bytes(b'x' * (size_kib * 1024))
        import json
        report = run / 'report.json'
        report.write_text(json.dumps({
            'status': status,
            'captures': [{'image': str(image), 'tool': 0,
                          'label': 'preflight_t0'}]}))
        when = self.now - age_days * 86400
        os.utime(report, (when, when))
        return report, image

    def plan(self, **kwargs):
        return retention.plan(
            self.results, now=self.now, reference_root=self.references,
            max_total_images_mib=1, **kwargs)

    def test_budget_trigger_deletes_oldest_closed_images_not_newest(self):
        old_report, old_img = self.make_run('20261110-010000_print', age_days=2)
        new_report, new_img = self.make_run('20261111-010000_print', age_days=1)
        selected, totals, warnings = self.plan()
        chosen = {e['path'] for e in selected}
        self.assertIn(old_img, chosen)
        self.assertNotIn(new_img, chosen)
        self.assertNotIn(old_report, chosen)
        self.assertLessEqual(totals['all_images']['after_bytes'],
                             retention.MIB)
        self.assertEqual(retention.execute(self.results, selected), 1)
        self.assertFalse(old_img.exists())
        self.assertTrue(old_report.exists())
        self.assertTrue(new_img.exists())

    def test_references_count_against_budget_but_cannot_be_deleted(self):
        report, image = self.make_run('20261110-020000_print', size_kib=700)
        ref = self.references / 'commissioned_T0.jpg'
        ref.write_bytes(b'r' * (2 * retention.MIB))
        selected, totals, warnings = self.plan()
        chosen = {e['path'] for e in selected}
        self.assertIn(image, chosen)
        self.assertNotIn(ref, chosen)
        self.assertEqual(totals['all_images']['protected_reference_bytes'],
                         ref.stat().st_size)
        self.assertGreater(totals['all_images']['after_bytes'],
                           retention.MIB)
        self.assertTrue(any('quota remains exceeded' in w for w in warnings))
        retention.execute(self.results, selected)
        self.assertTrue(ref.exists())

    def test_unrecognised_images_count_but_are_never_deleted(self):
        self.make_run('20261110-030000_print')
        stray = self.results / 'unknown-import'
        stray.mkdir()
        unknown = stray / 'other_camera.jpg'
        unknown.write_bytes(b'u' * (2 * retention.MIB))
        selected, totals, warnings = self.plan()
        self.assertEqual(len(selected), 1)
        self.assertNotEqual(selected[0]['path'], unknown)
        self.assertGreater(totals['all_images']['after_bytes'],
                           retention.MIB)
        retention.execute(self.results, selected)
        self.assertTrue(unknown.exists())

    def test_nonterminal_run_images_count_but_are_not_deleted(self):
        self.make_run('20261110-040000_print', age_days=2)
        report, active_img = self.make_run(
            '20261111-040000_print', status='STARTING',
            age_days=1, size_kib=1500)
        selected, totals, warnings = self.plan()
        self.assertTrue(all(e['path'] != active_img for e in selected))
        self.assertTrue(any('Nonterminal/unknown' in w for w in warnings))
        retention.execute(self.results, selected)
        self.assertTrue(report.exists())
        self.assertTrue(active_img.exists())

    def test_modified_report_invalidates_deletion_plan(self):
        self.make_run('20261110-050000_print', age_days=2)
        new_report, new_img = self.make_run('20261111-050000_print', age_days=1)
        selected, totals, warnings = self.plan()
        old = next(e for e in selected if e['category'] == 'success_images')
        report = old['run'] / 'report.json'
        report.write_text(report.read_text() + '\n')
        self.assertEqual(retention.execute(self.results, selected), 0)
        self.assertTrue(old['path'].exists())

    def test_modified_image_invalidates_deletion_plan(self):
        self.make_run('20261110-060000_print', age_days=2)
        self.make_run('20261111-060000_print', age_days=1)
        selected, _, _ = self.plan()
        old = next(e for e in selected if e['category'] == 'success_images')
        os.utime(old['path'], ns=(
            old['path'].stat().st_atime_ns,
            old['path'].stat().st_mtime_ns + 1000000000))
        self.assertEqual(retention.execute(self.results, selected), 0)
        self.assertTrue(old['path'].exists())

    def test_old_terminal_run_can_expire_with_report(self):
        report, image = self.make_run(
            '20261010-010000_print', age_days=100)
        selected, _, _ = self.plan()
        chosen = {e['path'] for e in selected}
        self.assertEqual(chosen, {report, image})
        self.assertEqual(retention.execute(self.results, selected), 2)
        self.assertFalse(image.exists())
        self.assertFalse(report.exists())

    def test_only_protected_references_never_block_captures(self):
        ref = self.references / 'active_T1.jpg'
        ref.write_bytes(b'r' * (2 * retention.MIB))
        selected, totals, warnings = self.plan()
        self.assertEqual(selected, [])
        self.assertGreater(totals['all_images']['after_bytes'],
                           retention.MIB)
        self.assertTrue(any('quota remains exceeded' in w for w in warnings))


if __name__ == '__main__':
    unittest.main()
