"""Offline RC16 active installer tests (never restart actual Klipper)."""
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
    'rc16_active_install_test_module', ROOT / 'scripts/rc16_active_install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
upgrade = installer.upgrade


class ActiveInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        for name in ('auto_xy/klipper', 'auto_xy/rollbacks',
                     'printer_data/config', 'klipper/klippy/extras'):
            (self.home / name).mkdir(parents=True)
        self.gate = self.home / upgrade.OLD_GATE
        self.previous_gate = (ROOT / 'src/auto_xy_gate.py').read_bytes()
        self.gate.write_bytes(self.previous_gate)
        (self.home / 'klipper/klippy/extras/auto_xy_gate.py').symlink_to(self.gate)
        self.settings = self.home / upgrade.GATE_CONFIG
        self.settings.write_text(
            '[auto_xy_gate]\nprotected_print_enabled: True\n'
            'camera_detector_mode: two_stage_roi\n')
        self.printer = self.home / upgrade.PRINTER
        self.printer.write_text(
            '[include auto_xy.cfg]\n#*# SAVE_CONFIG\n#*# do not touch\n')
        self.before = (self.settings.read_bytes(), self.printer.read_bytes())

    def assert_restored(self):
        self.assertEqual(self.gate.read_bytes(), self.previous_gate)
        self.assertEqual(self.settings.read_bytes(), self.before[0])
        self.assertEqual(self.printer.read_bytes(), self.before[1])
        self.assertFalse((self.home / upgrade.CONFIG).exists())
        self.assertFalse((self.home / upgrade.LINK).exists())

    def test_active_install_is_single_transaction(self):
        calls = []
        variant, snapshot = installer.install(
            self.home, ROOT, live=True,
            restart=lambda: calls.append('restart'),
            verify=lambda: calls.append('verify'))
        self.assertEqual(variant, 'rc15-dev6-reference')
        self.assertEqual(calls, ['restart', 'verify'])
        self.assertTrue(snapshot.is_dir())
        self.assertIn('enabled: True', (self.home / upgrade.CONFIG).read_text())
        self.assertIn('protected_print_enabled: True', self.settings.read_text())
        upgrade.rollback(self.home, snapshot, live=False)
        self.assert_restored()

    def test_failed_verify_automatically_restores_prior_install(self):
        calls = []
        def failure():
            calls.append('verify')
            raise installer.InstallationError('simulated no-ready')
        with self.assertRaisesRegex(installer.InstallationError, 'prior installation restored'):
            installer.install(
                self.home, ROOT, live=True,
                restart=lambda: calls.append('restart'),
                verify=failure)
        self.assertEqual(calls, ['restart', 'verify', 'restart'])
        self.assert_restored()

    def test_snapshot_restored_after_failed_restart(self):
        attempts = []
        def restart():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('simulated first restart failure')
        with self.assertRaisesRegex(installer.InstallationError, 'prior installation restored'):
            installer.install(self.home, ROOT, live=True,
                              restart=restart, verify=lambda: None)
        self.assertEqual(len(attempts), 2)
        self.assert_restored()

    def test_runtime_requires_all_three_active_sections(self):
        expected = {
            'auto_xy_gate': {'protected_print_enabled': True},
            'auto_xy_ratos_handoff_rc16': {
                'enabled': True, 'retention_enabled': True}}
        def response(url):
            if url == installer.INFO_URL:
                return {'result': {'state': 'ready'}}
            return {'result': {'status': {'configfile': {'settings': expected}}}}
        with patch.object(installer, 'query_json', side_effect=response):
            installer.check_active_runtime(timeout=2, sleep=lambda _: None)
            expected['auto_xy_ratos_handoff_rc16']['enabled'] = False
            with self.assertRaisesRegex(installer.InstallationError,
                                        'handoff is not enabled'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)


if __name__ == '__main__':
    unittest.main()
