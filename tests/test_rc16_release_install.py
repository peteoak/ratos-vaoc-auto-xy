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
        original_preflight = release.preflight
        restarts = []
        def restart():
            restarts.append('restart')
            if len(restarts) == 1:
                raise release.ReleaseInstallError('simulated first startup failed')
        with patch.object(release, 'preflight',
                          side_effect=lambda home, root, live:
                          original_preflight(home, root, live=False)), \
             patch.object(release.ratos_extension, 'snapshot', return_value={
                 'registered': str(self.home / release.upgrade.OLD_GATE),
                 'link': str(self.home / release.upgrade.OLD_GATE)}), \
             patch.object(release.ratos_extension, 'unregister', return_value=None), \
             patch.object(release.active, 'post_gcode') as post:
            with self.assertRaisesRegex(release.ReleaseInstallError, 'previous state restored'):
                release.install_fresh(
                    self.home, ROOT, live=True,
                    restart=restart, verify=lambda: None, activate=lambda: None)
        self.assertEqual(restarts, ['restart', 'restart'])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.printer.read_text(), self.before_printer)
        self.assertFalse((self.home / 'printer_data/config/auto_xy.cfg').exists())
        self.assertFalse((self.home / 'printer_data/config/auto_xy_rc16.cfg').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_gate.py').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_recommission_rc16.py').exists())

    def test_supervised_fresh_installer_registers_and_unregisters_only_autoxy(self):
        registry = {'owner': None}
        link = self.home / 'klipper/klippy/extras/auto_xy_gate.py'
        original_home = release.baseline.HOME
        def absent(home):
            if registry['owner'] is not None or link.exists() or link.is_symlink():
                raise RuntimeError('AutoXY registration already present')
        def register(home, target):
            if registry['owner'] is not None:
                raise RuntimeError('duplicate registration')
            registry['owner'] = str(target)
            link.symlink_to(target)
        def snapshot(home):
            return {'registered': registry['owner'],
                    'link': str(link.resolve()) if link.is_symlink() else None}
        def unregister(home, expected, other):
            self.assertEqual(registry['owner'], expected)
            self.assertEqual(other, expected)
            registry['owner'] = None
            link.unlink()
        starts = []
        def restart():
            starts.append('restart')
            if len(starts) == 1:
                raise release.ReleaseInstallError('simulated Klipper not ready')
        with patch.object(release.baseline, 'HOME', self.home.resolve()), \
             patch.object(release.baseline, 'live_safe',
                          return_value={'idex_xoffset': -.1, 'idex_yoffset': -.2,
                                        'idex_zoffset': .02}), \
             patch.object(release.ratos_extension, 'verify_absent', side_effect=absent), \
             patch.object(release.ratos_extension, 'register', side_effect=register), \
             patch.object(release.ratos_extension, 'snapshot', side_effect=snapshot), \
             patch.object(release.ratos_extension, 'unregister', side_effect=unregister), \
             patch.object(release.active, 'post_gcode') as post:
            with self.assertRaisesRegex(release.ReleaseInstallError, 'previous state restored'):
                release.install_fresh(self.home, ROOT, live=True,
                                      restart=restart, activate=lambda: None)
        self.assertEqual(registry['owner'], None)
        self.assertFalse(link.exists() or link.is_symlink())
        self.assertEqual(self.printer.read_text(), self.before_printer)
        self.assertEqual(starts, ['restart', 'restart'])
        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            ['SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0',
             'SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=0'])

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
