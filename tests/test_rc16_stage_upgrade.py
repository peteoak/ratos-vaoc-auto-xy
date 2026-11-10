"""Offline RC15->RC16 staging and exact rollback regression tests.

This suite does not interact with RatOS services, a printer or any home directory.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rc16_upgrader', ROOT / 'scripts/rc16_stage_upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class StagedUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'pi'
        for dirname in ('auto_xy/klipper', 'auto_xy/rollbacks',
                        'printer_data/config', 'klipper/klippy/extras'):
            (self.home / dirname).mkdir(parents=True)
        self.rc15 = (ROOT / 'src/auto_xy_gate.py').read_bytes()
        self.gate_path = self.home / 'auto_xy/klipper/auto_xy_gate.py'
        self.gate_path.write_bytes(self.rc15)
        self.existing = self.home / 'klipper/klippy/extras/auto_xy_gate.py'
        self.existing.symlink_to(self.gate_path)
        self.config = self.home / 'printer_data/config/auto_xy.cfg'
        self.config.write_text('[include auto_xy_macros.cfg]\n[auto_xy_gate]\nprotected_print_enabled: False\n')
        self.printer = self.home / 'printer_data/config/printer.cfg'
        self.printer_before = '[include auto_xy.cfg]\n\n#*# SAVE_CONFIG marker\n#*# DO NOT CHANGE\n'
        self.printer.write_text(self.printer_before)

    def check(self):
        return upgrade.check_installation(self.home, ROOT, live=False)

    def test_offline_stage_then_rollback_exact_restoration(self):
        changes = self.check()
        self.assertEqual(len(changes), 8)
        folder = upgrade.stage(self.home, changes)
        self.assertEqual((self.home / 'auto_xy/klipper/auto_xy_gate.py').read_bytes(),
                         (ROOT / 'src/auto_xy_gate_rc16.py').read_bytes())
        self.assertTrue((self.home / upgrade.LINK).is_symlink())
        self.assertIn('enabled: False', (self.home / upgrade.CONFIG).read_text())
        self.assertIn('retention_enabled: True', (self.home / upgrade.CONFIG).read_text())
        self.assertIn('retention_days: 30', (self.home / upgrade.CONFIG).read_text())
        self.assertIn('retention_interval_hours: 24', (self.home / upgrade.CONFIG).read_text())
        self.assertEqual((self.home / 'printer_data/config/auto_xy.cfg').read_text(),
                         '[include auto_xy_macros.cfg]\n[auto_xy_gate]\nprotected_print_enabled: False\n')
        after = self.printer.read_text()
        self.assertIn(upgrade.INCLUDE, after)
        self.assertTrue(after.endswith('#*# SAVE_CONFIG marker\n#*# DO NOT CHANGE\n'))
        upgrade.rollback(self.home, folder, live=False)
        self.assertEqual(self.gate_path.read_bytes(), self.rc15)
        self.assertEqual(self.printer.read_text(), self.printer_before)
        self.assertFalse((self.home / upgrade.CONFIG).exists())
        self.assertFalse((self.home / upgrade.LINK).exists())
        self.assertEqual(self.existing.resolve(), self.gate_path)

    def test_check_read_only(self):
        before = set(str(p.relative_to(self.home)) for p in self.home.rglob('*'))
        self.check()
        after = set(str(p.relative_to(self.home)) for p in self.home.rglob('*'))
        self.assertEqual(before, after)

    def test_refuse_unknown_existing_controller(self):
        self.gate_path.write_bytes(b'untrusted gate\n')
        with self.assertRaisesRegex(upgrade.Refuse, 'controller differs'):
            self.check()

    def test_refuse_protected_enabled(self):
        self.config.write_text(self.config.read_text().replace('False', 'True'))
        with self.assertRaisesRegex(upgrade.Refuse, 'Protected print is already active'):
            self.check()

    def test_refuse_unknown_rc16_files(self):
        (self.home / upgrade.CONFIG).write_text('modified config')
        with self.assertRaisesRegex(upgrade.Refuse, 'target already present'):
            self.check()

    def test_refuse_incomplete_existing_extension_link(self):
        self.existing.unlink()
        with self.assertRaisesRegex(upgrade.Refuse, 'Existing gate symlink'):
            self.check()

    def test_rollback_rejects_any_managed_file_change(self):
        folder = upgrade.stage(self.home, self.check())
        (self.home / upgrade.CONFIG).write_text('changed by human')
        with self.assertRaisesRegex(upgrade.Refuse, 'Managed file changed'):
            upgrade.rollback(self.home, folder, live=False)
        self.assertIn(upgrade.INCLUDE, self.printer.read_text())

    def test_installer_activation_requires_explicit_disabled_token(self):
        rc = upgrade.main(['--stage', '--offline', '--home', str(self.home)])
        self.assertEqual(rc, 2)
        self.assertEqual(self.printer.read_text(), self.printer_before)

    def test_rollback_requires_explicit_token(self):
        folder = upgrade.stage(self.home, self.check())
        rc = upgrade.main(['--rollback', str(folder), '--offline',
                           '--home', str(self.home)])
        self.assertEqual(rc, 2)
        self.assertIn(upgrade.INCLUDE, self.printer.read_text())


if __name__ == '__main__':
    unittest.main()
