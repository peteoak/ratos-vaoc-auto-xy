"""Offline active RC15->RC16 migration planning and exact rollback.

Tests simulate filesystem installation only. They do not start Klipper or
qualify the protected print-file handoff for hardware use.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'rc16_stage_upgrade_active', ROOT / 'scripts/rc16_stage_upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class ActiveUpgradeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        for directory in ('auto_xy/klipper', 'auto_xy/rollbacks',
                          'printer_data/config', 'klipper/klippy/extras'):
            (self.home / directory).mkdir(parents=True)
        self.old_gate = (ROOT / 'src/auto_xy_gate.py').read_bytes()
        self.gate = self.home / upgrade.OLD_GATE
        self.gate.write_bytes(self.old_gate)
        vision = self.home / 'auto_xy/vision/auto_xy_vision_worker.py'
        vision.parent.mkdir(parents=True, exist_ok=True)
        vision.write_bytes((ROOT / 'src/auto_xy_vision_worker.py').read_bytes())
        (self.home / 'klipper/klippy/extras/auto_xy_gate.py').symlink_to(self.gate)
        self.settings = self.home / upgrade.GATE_CONFIG
        self.settings.write_text(
            '[auto_xy_gate]\nprotected_print_enabled: True\n'
            'camera_detector_mode: two_stage_roi\n')
        self.printer = self.home / upgrade.PRINTER
        self.original_printer = ('[include auto_xy.cfg]\n'
                                 '#*# SAVE_CONFIG\n#*# offsets stay untouched\n')
        self.printer.write_text(self.original_printer)

    def test_active_transaction_round_trip(self):
        variant, changes = upgrade.check_active_installation(
            self.home, ROOT, live=False)
        self.assertEqual(variant, 'rc15-dev6-reference')
        self.assertIn(upgrade.GATE_CONFIG, changes)
        self.assertTrue(all(isinstance(value, bytes) for value in changes.values()),
                        'Every staged file must be supplied as bytes')
        self.assertIn(b'protected_print_enabled: True', changes[upgrade.GATE_CONFIG])
        self.assertIn(b'enabled: True', changes[upgrade.CONFIG])
        self.assertIn(b'retention_enabled: True', changes[upgrade.CONFIG])
        self.assertEqual(self.settings.read_text(),
                         '[auto_xy_gate]\nprotected_print_enabled: True\n'
                         'camera_detector_mode: two_stage_roi\n')
        snapshot = upgrade.stage(self.home, changes)
        self.assertIn('enabled: True',
                      (self.home / upgrade.CONFIG).read_text())
        self.assertIn(upgrade.INCLUDE, self.printer.read_text())
        self.assertEqual(self.gate.read_bytes(),
                         (ROOT / 'src/auto_xy_gate_rc16.py').read_bytes())
        upgrade.rollback(self.home, snapshot, live=False)
        self.assertEqual(self.gate.read_bytes(), self.old_gate)
        self.assertEqual(self.printer.read_text(), self.original_printer)
        self.assertEqual(self.settings.read_text(),
                         '[auto_xy_gate]\nprotected_print_enabled: True\n'
                         'camera_detector_mode: two_stage_roi\n')
        self.assertFalse((self.home / upgrade.CONFIG).exists())

    def test_existing_false_transitions_to_true_and_rolls_back(self):
        original = self.settings.read_text().replace('True', 'False')
        self.settings.write_text(original)
        variant, changes = upgrade.check_active_installation(
            self.home, ROOT, live=False)
        self.assertIn(b'protected_print_enabled: True', changes[upgrade.GATE_CONFIG])
        snapshot = upgrade.stage(self.home, changes)
        self.assertIn('protected_print_enabled: True', self.settings.read_text())
        upgrade.rollback(self.home, snapshot, live=False)
        self.assertEqual(self.settings.read_text(), original)

    def test_unknown_controller_stays_unmodified(self):
        self.gate.write_bytes(b'unrecognised')
        before = self.gate.read_bytes()
        with self.assertRaisesRegex(Exception, 'Controller not recognised'):
            upgrade.check_active_installation(self.home, ROOT, live=False)
        self.assertEqual(self.gate.read_bytes(), before)
        self.assertNotIn(upgrade.INCLUDE, self.printer.read_text())

    def test_unknown_rc16_target_refused_before_writes(self):
        (self.home / upgrade.CONFIG).write_text('user data')
        with self.assertRaisesRegex(upgrade.Refuse, 'target already present'):
            upgrade.check_active_installation(self.home, ROOT, live=False)
        self.assertEqual((self.home / upgrade.CONFIG).read_text(), 'user data')


if __name__ == '__main__':
    unittest.main()
