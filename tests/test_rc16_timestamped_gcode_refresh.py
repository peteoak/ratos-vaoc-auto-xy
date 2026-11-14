"""Offline transaction tests for the RC16 dated generated-name update."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'rc16_timestamped_gcode_refresh',
    ROOT / 'scripts/rc16_timestamped_gcode_refresh.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class TimestampedGCodeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        self.source_root = Path(self.tmp.name) / 'repo'
        self.source_root.mkdir()
        for sub in ('auto_xy/workers', 'auto_xy/klipper', 'auto_xy/rollbacks'):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        self.rows = []
        for index, folder in enumerate(
                ('auto_xy/workers', 'auto_xy/workers', 'auto_xy/klipper')):
            source_name = 'source_%d.py' % index
            target_name = '%s/file_%d.py' % (folder, index)
            old = ('old-%d\n' % index).encode()
            new = ('new-%d\n' % index).encode()
            (self.source_root / source_name).write_bytes(new)
            (self.home / target_name).write_bytes(old)
            self.rows.append((source_name, target_name,
                              refresh.git_blob(old), refresh.git_blob(new)))
        self.pin = patch.object(refresh, 'SOURCES', tuple(self.rows))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_preflight_identifies_three_reviewed_files(self):
        states, updated = refresh.check_files(self.source_root, self.home)
        self.assertEqual(len(states), 3)
        self.assertFalse(updated)

    def test_offline_exact_update_creates_verifiable_snapshot(self):
        result, backup = refresh.install(
            self.source_root, self.home, live=False)
        self.assertEqual(result, 'updated')
        self.assertEqual(len(list((backup/'originals').glob('*.py'))), 3)
        receipt = json.loads((backup/'receipt.json').read_text())
        for row in receipt:
            self.assertEqual(refresh.git_blob(Path(row['target']).read_bytes()),
                             row['new_blob'])
        result, _ = refresh.install(self.source_root, self.home, live=False)
        self.assertEqual(result, 'already-updated')

    def test_mixed_version_is_refused(self):
        (self.home / self.rows[0][1]).write_bytes(
            (self.source_root / self.rows[0][0]).read_bytes())
        with self.assertRaisesRegex(refresh.Refuse,'Mixed old/new'):
            refresh.check_files(self.source_root, self.home)

    def test_unknown_installed_source_is_refused(self):
        (self.home / self.rows[0][1]).write_text('surprise')
        with self.assertRaisesRegex(refresh.Refuse,'differs'):
            refresh.check_files(self.source_root, self.home)

    def test_restart_failure_restores_all_originals(self):
        calls = []
        def restarting():
            calls.append(1)
            if len(calls) == 1:
                raise OSError('simulated restart failure')
        status = {'print_stats': {'state':'standby'},
                  'virtual_sdcard': {'is_active':False,
                                     'file_path':'', 'filename':''},
                  'gcode_macro _VAOC': {'is_started':False},
                  'auto_xy_ratos_handoff_rc16': {'armed':True, 'busy':False}}
        with patch.object(refresh, 'read_status', return_value=status):
            with self.assertRaisesRegex(refresh.Refuse,'original files restored'):
                refresh.install(self.source_root, self.home,
                                live=True, do_restart=restarting,
                                wait_ready=lambda:None)
        self.assertEqual(len(calls), 2)
        for source, target, old_hash, _ in self.rows:
            self.assertEqual(refresh.git_blob((self.home/target).read_bytes()),
                             old_hash)

    def test_busy_state_refuses_before_writing(self):
        with patch.object(refresh, 'read_status', return_value={
                'print_stats': {'state':'paused'}}):
            with self.assertRaisesRegex(refresh.Refuse,'busy'):
                refresh.install(self.source_root, self.home, live=True,
                                do_restart=lambda:None, wait_ready=lambda:None)
        for _, target, old_hash, _ in self.rows:
            self.assertEqual(refresh.git_blob((self.home/target).read_bytes()),
                             old_hash)


if __name__ == '__main__':
    unittest.main()
