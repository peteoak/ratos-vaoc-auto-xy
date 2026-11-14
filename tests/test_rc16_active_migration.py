"""Offline tests for the RC16 active-only migration planner."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_active_migration', ROOT / 'scripts/rc16_active_migration.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class ActiveMigrationTests(unittest.TestCase):
    def test_recognises_both_reviewed_rc15_variants(self):
        self.assertEqual(
            migration.RECOGNISED_CONTROLLERS[migration.OWNER_RC15_SHA256],
            'rc15-dev6-owner-recovery-variant')
        old = (ROOT / 'src/auto_xy_gate.py').read_bytes()
        self.assertEqual(migration.controller_variant(old), 'rc15-dev6-reference')

    def test_unknown_controller_refused(self):
        with self.assertRaises(migration.MigrationError):
            migration.controller_variant(b'unknown controller')

    def test_enabled_gate_is_unchanged(self):
        cfg = '[auto_xy_gate]\nprotected_print_enabled: True\nother: value\n'
        self.assertEqual(migration.preserve_enabled_gate(cfg), cfg)

    def test_disabled_gate_is_enabled_without_losing_other_settings(self):
        cfg = '[auto_xy_gate]\nprotected_print_enabled: False\nother: value\n'
        self.assertEqual(migration.preserve_enabled_gate(cfg),
                         cfg.replace('False', 'True'))

    def test_ambiguous_gate_refused(self):
        for cfg in ('protected_print_enabled: True\n',
                    '[auto_xy_gate]\n',
                    '[auto_xy_gate]\nprotected_print_enabled: True\n'
                    'protected_print_enabled: False\n'):
            with self.subTest(cfg=cfg):
                with self.assertRaises(migration.MigrationError):
                    migration.preserve_enabled_gate(cfg)

    def test_gate_edit_preserves_other_sections_and_crlf(self):
        cfg = ('[other_section]\r\n'
               'protected_print_enabled: True\r\n'
               '[auto_xy_gate]\r\n'
               'protected_print_enabled: False\r\n'
               'camera_detector_mode: two_stage_roi\r\n'
               '[another_section]\r\n'
               'protected_print_enabled: False\r\n')
        expected = cfg.replace(
            '[auto_xy_gate]\r\nprotected_print_enabled: False',
            '[auto_xy_gate]\r\nprotected_print_enabled: True')
        self.assertEqual(migration.preserve_enabled_gate(cfg), expected)

    def test_protection_key_in_other_section_is_not_accepted(self):
        cfg = ('[auto_xy_gate]\n'
               'some_setting: value\n'
               '[unrelated]\n'
               'protected_print_enabled: True\n')
        with self.assertRaisesRegex(migration.MigrationError,
                                    'in \[auto_xy_gate\]'):
            migration.preserve_enabled_gate(cfg)

    def test_repeated_gate_section_refuses_ambiguous_migration(self):
        cfg = ('[auto_xy_gate]\nprotected_print_enabled: True\n'
               '[auto_xy_gate]\nprotected_print_enabled: False\n')
        with self.assertRaisesRegex(migration.MigrationError,
                                    'Expected one \[auto_xy_gate\]'):
            migration.preserve_enabled_gate(cfg)

    def test_handoff_enabled_with_automatic_cleanup(self):
        cfg = migration.active_handoff_config(Path('/home/pi'))
        self.assertIn('enabled: True\n', cfg)
        self.assertIn('retention_enabled: True\n', cfg)
        self.assertIn('rc16_prepare_worker.py', cfg)
        self.assertIn('rc16_generated_retention.py', cfg)

    def test_planner_is_read_only_on_a_recognised_installation(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            gate = home / 'auto_xy/klipper/auto_xy_gate.py'
            cfg = home / 'printer_data/config/auto_xy.cfg'
            gate.parent.mkdir(parents=True)
            cfg.parent.mkdir(parents=True)
            gate.write_bytes((ROOT / 'src/auto_xy_gate.py').read_bytes())
            cfg.write_text('[auto_xy_gate]\nprotected_print_enabled: True\n')
            before = {p.relative_to(home): p.read_bytes()
                      for p in home.rglob('*') if p.is_file()}
            plan = migration.plan_existing_gate(home)
            after = {p.relative_to(home): p.read_bytes()
                     for p in home.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse(plan['gate_config_changed'])
            self.assertIn(b'enabled: True', plan['handoff_config_bytes'])


if __name__ == '__main__':
    unittest.main()
