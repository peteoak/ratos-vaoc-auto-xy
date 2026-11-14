"""RC16 retention never removes unknown, active, modified or recent G-code."""
import hashlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_retention', ROOT / 'src/tools/rc16_generated_retention.py')
ret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ret)


class GeneratedRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'gcodes'
        self.registry = Path(self.tmp.name) / 'registry'
        self.root.mkdir()
        self.registry.mkdir()
        self.now = time.time()
        self.name = '__AutoXY_RC16_' + 'b' * 24 + '.gcode'
        self.original = b'; processed by RatOS.PostProcessor 1.0.0 test\nG1 X10\n'
        self.file = self.root / self.name
        self.record = self.registry / ('b' * 24 + '.json')
        self.file.write_bytes(self.original)
        rec = {'schema': 1, 'stage_name': self.name,
               'source': 'user_original.gcode',
               'source_sha256': 'a' * 64,
               'output_sha256': hashlib.sha256(self.original).hexdigest()}
        self.record.write_text(json.dumps(rec))
        old = self.now - 31 * 86400
        os.utime(self.file, (old, old))
        os.utime(self.record, (old, old))

    def test_timestamped_registered_file_expires_only_when_owned(self):
        token = 'Dragon_Head_20261113-174530-123456'
        new_name = '__AutoXY_RC16_' + token + '.gcode'
        file = self.root / new_name
        record = self.registry / (token + '.json')
        file.write_bytes(self.original)
        record.write_text(json.dumps({
            'schema': 1, 'stage_name': new_name,
            'source': 'original.gcode', 'source_sha256': 'a'*64,
            'output_sha256': hashlib.sha256(self.original).hexdigest()}))
        old = self.now - 31 * 86400
        os.utime(file, (old, old))
        os.utime(record, (old, old))
        eligible = ret.candidates(self.root, self.registry, now=self.now)
        self.assertIn(file, [item[0] for item in eligible])
        result = ret.clean(self.root, self.registry, execute=True, auto=True,
                           now=self.now, idle_probe=lambda: True)
        self.assertEqual(result['deleted_count'], 2)
        self.assertFalse(file.exists())
        self.assertFalse(record.exists())

    def test_timestamped_pending_stage_is_protected_after_final_registration(self):
        token = 'Dragon_Head_20261113-174530-654321'
        name = '__AutoXY_RC16_' + token + '.gcode'
        stage = self.root / name
        marker = self.registry / (token + '.pending.json')
        final = self.registry / (token + '.json')
        stage.write_bytes(self.original)
        marker.write_text(json.dumps({
            'schema': 1, 'kind': 'autoxy_pending_stage',
            'stage_name': name, 'bytes': len(self.original),
            'sha256': hashlib.sha256(self.original).hexdigest()
        }))
        final.write_text(json.dumps({
            'schema': 1, 'stage_name': name, 'source': 'original.gcode',
            'source_sha256': 'a'*64,
            'output_sha256': hashlib.sha256(self.original).hexdigest()
        }))
        old = self.now - 31 * 86400
        for path in (stage, marker, final):
            os.utime(path, (old, old))
        self.assertEqual(ret.pending_candidates(
            self.root, self.registry, now=self.now), [])
        result = ret.clean(self.root, self.registry, execute=True, auto=True,
                           now=self.now, idle_probe=lambda: True)
        self.assertTrue(marker.exists())
        self.assertFalse(final.exists())
        self.assertFalse(stage.exists())

    def test_timestamped_pending_without_final_can_expire(self):
        token = 'Dragon_Head_20261113-174530-000123'
        stage = self.root / ('__AutoXY_RC16_' + token + '.gcode')
        marker = self.registry / (token + '.pending.json')
        stage.write_bytes(self.original)
        marker.write_text(json.dumps({
            'schema': 1, 'kind': 'autoxy_pending_stage',
            'stage_name': stage.name, 'bytes': len(self.original),
            'sha256': hashlib.sha256(self.original).hexdigest()
        }))
        old = self.now - 31 * 86400
        os.utime(stage, (old, old))
        os.utime(marker, (old, old))
        self.assertIn(stage, [x[0] for x in ret.pending_candidates(
            self.root, self.registry, now=self.now)])
        result = ret.clean(self.root, self.registry, execute=True, auto=True,
                           now=self.now, idle_probe=lambda: True)
        self.assertEqual(result['pending_deleted_count'], 1)
        self.assertFalse(stage.exists())
        self.assertFalse(marker.exists())

    def test_default_dry_run_never_deletes(self):
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['eligible_count'], 1)
        self.assertTrue(self.file.exists())
        self.assertTrue(self.record.exists())

    def test_explicit_deletion_only_with_idle_confirmation(self):
        calls = []
        result = ret.clean(
            self.root, self.registry, execute=True, confirm='DELETE_RC16_EXPIRED',
            now=self.now, idle_probe=lambda: calls.append(True))
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(result['deleted_count'], 1)
        self.assertFalse(self.file.exists())
        self.assertFalse(self.record.exists())

    def test_active_printer_refuses_all_deletion(self):
        def busy():
            raise ret.Refuse('active')
        with self.assertRaises(ret.Refuse):
            ret.clean(self.root, self.registry, execute=True,
                      confirm='DELETE_RC16_EXPIRED',
                      now=self.now, idle_probe=busy)
        self.assertTrue(self.file.exists())
        self.assertTrue(self.record.exists())

    def test_refuses_unsafe_confirmation(self):
        with self.assertRaisesRegex(ret.Refuse, 'confirmation'):
            ret.clean(self.root, self.registry, execute=True, now=self.now)
        self.assertTrue(self.file.exists())

    def test_modified_gcode_preserved(self):
        with self.file.open('ab') as stream:
            stream.write(b'; changed\n')
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 0)
        self.assertTrue(self.file.exists())

    def test_new_registry_entries_preserved(self):
        os.utime(self.record, None)
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 0)

    def test_symlinked_generated_file_is_ignored(self):
        outside = Path(self.tmp.name) / 'external.gcode'
        outside.write_bytes(self.original)
        self.file.unlink()
        self.file.symlink_to(outside)
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 0)
        self.assertEqual(outside.read_bytes(), self.original)

    def test_unknown_record_is_ignored(self):
        self.record.write_text('not json')
        os.utime(self.record, (self.now - 31*86400, self.now - 31*86400))
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 0)
        self.assertTrue(self.file.exists())

    def test_recent_file_preserved_even_if_registry_expired(self):
        os.utime(self.file, None)
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 0)
        self.assertTrue(self.file.exists())

    def test_automatic_cleanup_needs_no_manual_confirmation(self):
        calls = []
        result = ret.clean(
            self.root, self.registry, execute=True, auto=True,
            now=self.now, idle_probe=lambda: calls.append(True))
        self.assertEqual(result['deleted_count'], 1)
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(self.file.exists())

    def test_auto_without_execute_is_rejected(self):
        with self.assertRaises(ret.Refuse):
            ret.clean(self.root, self.registry, auto=True, now=self.now)

    def test_auto_stops_if_printer_becomes_busy(self):
        called = []
        def changing():
            called.append(True)
            if len(called) >= 2:
                raise ret.Refuse('printer busy')
        result = ret.clean(
            self.root, self.registry, execute=True, auto=True,
            now=self.now, idle_probe=changing)
        self.assertEqual(result['deleted_count'], 0)
        self.assertTrue(self.file.exists())

    def test_report_contains_no_filenames_or_paths(self):
        result = ret.clean(self.root, self.registry, now=self.now)
        ret.write_report(self.registry, result)
        report = (self.registry / ret.REPORT_NAME).read_text()
        self.assertNotIn(self.name, report)
        self.assertNotIn(str(self.root), report)

    def test_limited_batch_does_not_delete_all_at_once(self):
        result = ret.clean(
            self.root, self.registry, execute=True, auto=True,
            max_delete=1, now=self.now, idle_probe=lambda: True)
        self.assertEqual(result['deleted_count'], 1)

    def test_verified_abandoned_stage_deleted_after_retention(self):
        import hashlib
        stage = self.root / ('__AutoXY_RC16_' + 'c' * 24 + '.gcode')
        payload = b'; unfinished stage\\nG1 X22\\n'
        stage.write_bytes(payload)
        pending = self.registry / ('c' * 24 + '.pending.json')
        pending.write_text(json.dumps({
            'schema': 1, 'kind': 'autoxy_pending_stage',
            'stage_name': stage.name, 'bytes': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest()
        }))
        old = self.now - 31*86400
        os.utime(stage, (old, old))
        os.utime(pending, (old, old))
        found = ret.pending_candidates(self.root, self.registry, now=self.now)
        self.assertEqual(len(found), 1)
        result = ret.clean(self.root, self.registry, execute=True, auto=True,
                           now=self.now, idle_probe=lambda: True)
        self.assertEqual(result['pending_deleted_count'], 1)
        self.assertFalse(stage.exists())
        self.assertFalse(pending.exists())

    def test_changed_abandoned_stage_never_deleted(self):
        stage = self.root / ('__AutoXY_RC16_' + 'd' * 24 + '.gcode')
        stage.write_bytes(b'changed G-code')
        pending = self.registry / ('d' * 24 + '.pending.json')
        pending.write_text(json.dumps({
            'schema': 1, 'kind': 'autoxy_pending_stage',
            'stage_name': stage.name, 'bytes': 22,
            'sha256': 'a'*64
        }))
        old = self.now - 31*86400
        os.utime(stage, (old, old))
        os.utime(pending, (old, old))
        result = ret.clean(self.root, self.registry, execute=True, auto=True,
                           now=self.now, idle_probe=lambda: True)
        self.assertEqual(result['pending_deleted_count'], 0)
        self.assertTrue(stage.exists() and pending.exists())

    def test_successfully_registered_file_protects_stale_pending(self):
        import hashlib
        pending = self.registry / ('b' * 24 + '.pending.json')
        pending.write_text(json.dumps({
            'schema': 1, 'kind': 'autoxy_pending_stage',
            'stage_name': self.name, 'bytes': len(self.original),
            'sha256': hashlib.sha256(self.original).hexdigest()
        }))
        old = self.now - 31*86400
        os.utime(pending, (old, old))
        # The ordinary final record exists: pending cleanup must ignore it.
        self.assertEqual(ret.pending_candidates(self.root, self.registry, now=self.now), [])

    def test_unknown_original_is_never_deleted(self):
        source = self.root / 'original.gcode'
        source.write_bytes(b'G1 X20\n')
        ret.clean(self.root, self.registry, execute=True, auto=True,
                  now=self.now, idle_probe=lambda: True)
        self.assertEqual(source.read_bytes(), b'G1 X20\n')


if __name__ == '__main__':
    unittest.main()
