"""RC16 release installer: simulate one-step active fresh/upgrade and rollback."""
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
    'rc16_release', ROOT / 'scripts/rc16_release_install.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        for directory in ('auto_xy/rollbacks', 'printer_data/config',
                          'klipper/klippy/extras'):
            (self.home / directory).mkdir(parents=True)
        self.printer = self.home / 'printer_data/config/printer.cfg'
        self.before_printer = ('[mcu]\nserial: /dev/ttyS0\n\n'
                               '#*# SAVE_CONFIG\n#*# preserved offsets\n')
        self.printer.write_text(self.before_printer)

    def test_detects_new_install_without_guessing_partial(self):
        self.assertEqual(release.install_kind(self.home), 'fresh')
        (self.home / 'printer_data/config/auto_xy.cfg').write_text('partial')
        with self.assertRaisesRegex(release.ReleaseInstallError, 'Incomplete'):
            release.install_kind(self.home)

    def test_full_fresh_install_is_active_in_one_transaction(self):
        receipt = release.install_fresh(self.home, ROOT, live=False)
        self.assertTrue(receipt.is_dir())
        self.assertIn('enabled: True',
                      (self.home / 'printer_data/config/auto_xy_rc16.cfg').read_text())
        self.assertIn('protected_print_enabled: True',
                      (self.home / 'printer_data/config/auto_xy.cfg').read_text())
        self.assertTrue((self.home / 'klipper/klippy/extras/auto_xy_recommission_rc16.py').is_symlink())
        self.assertTrue((self.home / 'auto_xy/workers/rc16_reference_retention.py').is_file())
        self.assertTrue((self.home / 'printer_data/config/auto_xy_recommission_rc16_macros.cfg').is_file())
        self.assertEqual(self.printer.read_text().split('#*# SAVE_CONFIG')[1],
                         self.before_printer.split('#*# SAVE_CONFIG')[1])
        self.assertTrue((receipt / release.RECEIPT).is_file())

    def test_fresh_failure_rolls_back_all_files_and_registration_link(self):
        def fail():
            raise release.ReleaseInstallError('simulated first startup failed')
        with patch.object(release.ratos_extension, 'snapshot', return_value={
            'registered': str(self.home / release.upgrade.OLD_GATE),
            'link': str(self.home / release.upgrade.OLD_GATE)}), \
             patch.object(release.ratos_extension, 'unregister', return_value=None):
            with self.assertRaisesRegex(release.ReleaseInstallError, 'previous state restored'):
                release.install_fresh(
                    self.home, ROOT, live=True,
                    restart=fail, verify=lambda: None, activate=lambda: None)
        self.assertEqual(self.printer.read_text(), self.before_printer)
        self.assertFalse((self.home / 'printer_data/config/auto_xy.cfg').exists())
        self.assertFalse((self.home / 'printer_data/config/auto_xy_rc16.cfg').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_gate.py').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_recommission_rc16.py').exists())

    def test_unified_offline_upgrade_recognises_reviewed_baseline(self):
        baseline = release.baseline.plan(self.home, ROOT, 'fresh')
        release.baseline.install(self.home, baseline, 'fresh', offsets=None)
        variant, receipt = release.install_complete(self.home, ROOT, live=False)
        self.assertEqual(variant, 'rc15-dev6-reference')
        self.assertTrue(receipt.is_dir())
        self.assertIn('enabled: True',
                      (self.home / 'printer_data/config/auto_xy_rc16.cfg').read_text())

    def test_fresh_active_config_does_not_offer_disabled_stage(self):
        self.assertNotIn('STAGE_RC16_DEV1_DISABLED',
                         (ROOT / 'scripts/rc16_release_install.py').read_text())
        self.assertNotIn('FRESH_RC16_DEV1_DISABLED',
                         (ROOT / 'scripts/rc16_release_install.py').read_text())


if __name__ == '__main__':
    unittest.main()
