"""Nozzle reference generation lifecycle: 30 days from successful replacement."""
import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


worker = module('recommission_worker', 'src/tools/rc16_recommission_worker.py')
retention = module('retired_reference_retention', 'src/auto_xy_retention_rc16.py')
retired = module('retired_reference_cleanup', 'src/tools/rc16_reference_retention.py')


class NozzleReferenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'nozzle_references'
        self.results = base / 'print_gate_runs'
        self.root.mkdir()
        self.results.mkdir()
        self.archive = self.root / 'archived'
        self.archive.mkdir()
        self.old_session = self.root / 'session-aaaaaaaa'
        self.old_session.mkdir()
        self.new_session = self.root / 'session-bbbbbbbb'
        self.new_session.mkdir()
        self.profile = base / 'camera_commission.json'
        self.old_images = self.make_frames(self.old_session, 'abcabcabcabc')
        self.new_images = self.make_frames(self.new_session, 'defdefdefdef')
        old = self.make_profile(self.old_images)
        self.profile.write_text(json.dumps(old))

    def make_frames(self, folder, ident):
        groups = []
        for tool in (0, 1):
            frames = []
            for i in (1, 2, 3):
                path = folder / ('t%d_%s_%d.jpg' % (tool, ident, i))
                path.write_bytes(('JPEG-%d-%d-' % (tool, i)).encode() +
                                 bytes([tool*8+i]) * 2000)
                frames.append(str(path))
            groups.append(frames)
        return groups

    def make_profile(self, images):
        profile = {
            'format': 1, 'status': 'COMMISSIONED_FROM_IMAGES',
            'camera_url': 'http://localhost/test', 'roi': 'auto',
            'reference_schema': 'rc16-full-frame-raw-jpeg-v1'}
        for tool in (0, 1):
            profile['tool%d' % tool] = {
                'num_images': 3, 'radius_px': 144.,
                'registration_center_px': [500., 450.],
                'reference_images': [
                    {'path': path,
                     'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                    for path in images[tool]]}
        return profile

    def publish(self, now=1_700_000_000):
        with patch.object(worker, 'detect', return_value=(500., 450., 144.)):
            return worker.publish(
                self.new_session, self.new_images, 'http://localhost/test',
                self.profile, self.archive,
                reference_root=self.root, results_root=self.results, now=now)

    def test_active_images_never_deleted_old_only_after_thirty_days(self):
        moment = 1_700_000_000
        receipt = self.publish(now=moment)
        self.assertTrue(Path(receipt['retired_manifest']).is_file())
        active_before = {p: Path(p).read_bytes()
                         for group in self.new_images for p in group}
        for elapsed_days in (0, 7, 29, 29.999):
            report = retired.clean(
                self.root, self.profile, now=moment + elapsed_days*86400,
                execute=True)
            self.assertEqual(report['removed_generations'], 0)
            self.assertTrue(all(Path(p).exists() for group in self.old_images for p in group))
        result = retired.clean(
            self.root, self.profile, now=moment + 30*86400, execute=True)
        self.assertEqual(result['removed_generations'], 1)
        self.assertEqual(result['removed_images'], 6)
        self.assertTrue(all(not Path(p).exists() for group in self.old_images for p in group))
        self.assertFalse(Path(receipt['backup']).exists())
        self.assertFalse(Path(receipt['retired_manifest']).exists())
        self.assertEqual(active_before,
                         {p: Path(p).read_bytes()
                          for group in self.new_images for p in group})

    def test_failed_recommission_does_not_start_retirement_clock(self):
        before = self.profile.read_bytes()
        with patch.object(worker, 'detect',
                          side_effect=ValueError('two nozzle-sized circles')):
            with self.assertRaisesRegex(ValueError, 'current profile preserved'):
                worker.publish(self.new_session, self.new_images,
                               'http://localhost/test', self.profile,
                               self.archive, reference_root=self.root,
                               results_root=self.results)
        self.assertEqual(self.profile.read_bytes(), before)
        self.assertEqual(list(self.archive.iterdir()), [])
        self.assertTrue(all(Path(p).exists() for group in self.old_images for p in group))

    def test_interrupted_cleanup_resumes_after_partial_deletion(self):
        moment = 1_700_000_000
        receipt = self.publish(now=moment)
        first = Path(self.old_images[0][0])
        first.unlink()  # Simulated prior cleanup that stopped before completion
        remaining = sum(Path(p).exists() for group in self.old_images for p in group)
        self.assertEqual(remaining, 5)
        result = retired.clean(
            self.root, self.profile, now=moment + 31*86400, execute=True)
        self.assertEqual(result['removed_generations'], 1)
        self.assertEqual(result['removed_images'], 5)
        self.assertFalse(Path(receipt['retired_manifest']).exists())
        self.assertFalse(Path(receipt['backup']).exists())
        self.assertTrue(all(Path(p).exists() for group in self.new_images for p in group))

    def test_current_image_referenced_from_old_generation_is_not_removed(self):
        moment = 1_700_000_000
        self.publish(now=moment)
        current = json.loads(self.profile.read_text())
        current['tool0']['reference_images'][0] = (
            self.make_profile(self.old_images)['tool0']['reference_images'][0])
        self.profile.write_text(json.dumps(current))
        result = retired.clean(
            self.root, self.profile, now=moment+31*86400, execute=True)
        self.assertEqual(result['removed_images'], 0)
        self.assertTrue(all(Path(p).exists() for group in self.old_images for p in group))
        self.assertTrue(any('active' in w.lower() for w in result['warnings']))

    def test_corrupted_manifest_or_backup_blocks_cleanup(self):
        moment = 1_700_000_000
        receipt = self.publish(now=moment)
        Path(receipt['backup']).write_bytes(b'changed')
        result = retired.clean(
            self.root, self.profile, now=moment+40*86400, execute=True)
        self.assertEqual(result['removed_images'], 0)
        self.assertTrue(all(Path(p).exists() for group in self.old_images for p in group))

    def test_protected_library_counted_but_never_exposed_to_run_deletion(self):
        self.publish()
        selected, tally, _ = retention.plan(
            self.results, now=1_800_000_000,
            reference_root=self.root, max_total_images_mib=1)
        self.assertTrue(tally['all_images']['protected_reference_bytes'] > 0)
        self.assertFalse(any(self.root in e['path'].parents for e in selected))

    def test_abandoned_session_cleanup_requires_thirty_days_and_no_references(self):
        now = 1_800_000_000
        self.publish(now=now - 50*86400)
        orphan = self.root / 'session-cccccccc'
        orphan.mkdir()
        photo = orphan / 't0_abcdefabcdef_1.jpg'
        photo.write_bytes(b'old unreferenced image')
        import os
        old = now - 31*86400
        os.utime(orphan, (old, old))
        before = retired.clean_abandoned_sessions(
            self.root, self.profile, now=now-2*86400, execute=True)
        self.assertEqual(before['removed_sessions'], 0)
        self.assertTrue(photo.exists())
        after = retired.clean_abandoned_sessions(
            self.root, self.profile, now=now, execute=True)
        self.assertEqual(after['removed_sessions'], 1)
        self.assertFalse(orphan.exists())
        self.assertTrue(all(Path(p).exists() for group in self.new_images for p in group))

    def test_abandoned_cleanup_does_not_delete_active_or_archived_references(self):
        now = 1_800_000_000
        self.publish(now=now-50*86400)
        import os
        for folder in (self.old_session, self.new_session):
            os.utime(folder, (now-60*86400, now-60*86400))
        result = retired.clean_abandoned_sessions(
            self.root, self.profile, now=now, execute=True)
        self.assertEqual(result['removed_sessions'], 0)
        self.assertTrue(all(Path(p).exists() for group in self.old_images for p in group))
        self.assertTrue(all(Path(p).exists() for group in self.new_images for p in group))

    def test_corrupt_archive_blocks_abandoned_capture_deletion(self):
        now = 1_800_000_000
        self.publish(now=now-50*86400)
        orphan = self.root / 'session-cccccccc'
        orphan.mkdir()
        photo = orphan / 't0_abcdefabcdef_1.jpg'
        photo.write_bytes(b'unreferenced')
        import os
        os.utime(orphan, (now-60*86400, now-60*86400))
        (self.archive / 'camera_commission_broken.json').write_text('not json')
        outcome = retired.clean_abandoned_sessions(
            self.root, self.profile, now=now, execute=True)
        self.assertEqual(outcome['removed_images'], 0)
        self.assertTrue(photo.exists())

    def test_results_tree_cannot_be_used_for_reference_capture(self):
        rogue = self.results / 'run-1'
        rogue.mkdir()
        with self.assertRaisesRegex(ValueError, 'Reference'):
            worker.protected_session(
                rogue, reference_root=self.root, results_root=self.results)


if __name__ == '__main__':
    unittest.main()
