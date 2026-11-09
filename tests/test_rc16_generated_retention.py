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
        self.assertEqual(calls, [True])
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
        # Policy is based on complete run registry closure rather than
        # output file mtime, whose timestamp may be updated after publish.
        result = ret.clean(self.root, self.registry, now=self.now)
        self.assertEqual(result['eligible_count'], 1)


if __name__ == '__main__':
    unittest.main()
