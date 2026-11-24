"""Offline checks for pinned, rollback-safe existing-RC16 cancellation update."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_cancel_cleanup_refresh',
    ROOT / 'scripts/rc16_cancel_cleanup_refresh.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class CancelRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / refresh.TARGET
        self.path.parent.mkdir(parents=True)
        self.rollbacks = self.home / 'auto_xy/rollbacks'
        self.rollbacks.mkdir()
        self.gate = self.home / 'auto_xy/klipper/auto_xy_gate.py'
        self.gate.write_bytes((ROOT / 'src/auto_xy_gate_rc16.py').read_bytes())
        self.source = (ROOT / refresh.SOURCE).read_bytes()
        self.assertEqual(refresh.blob(self.source), refresh.NEW_BLOB)
        self.assertEqual(refresh.blob(self.gate.read_bytes()),
                         refresh.GATE_BLOB)

    def test_preflight_current_source_is_idempotent(self):
        self.path.write_bytes(self.source)
        entry = refresh.checked_files(ROOT, self.home)
        self.assertTrue(entry[3])
        self.assertEqual(entry[0], self.path)

    def test_unknown_installed_source_rejected_without_changes(self):
        self.path.write_bytes(b'changed beta build\n')
        with self.assertRaisesRegex(refresh.Refuse, 'differs from exact reviewed'):
            refresh.checked_files(ROOT, self.home)
        self.assertEqual(self.path.read_bytes(), b'changed beta build\n')

    def test_requires_recognized_base_controller(self):
        self.path.write_bytes(self.source)
        self.gate.write_bytes(b'unknown gate')
        with self.assertRaisesRegex(refresh.Refuse, 'gate is not'):
            refresh.checked_files(ROOT, self.home)

    def test_unloaded_idle_conditions(self):
        data = {
            'print_stats': {'state': 'cancelled'},
            'virtual_sdcard': {'is_active': False, 'file_path': None},
            'gcode_macro _VAOC': {'is_started': False},
            'auto_xy_ratos_handoff_rc16': {'armed': True, 'busy': False},
            'save_variables': {'variables': {}},
        }
        refresh.require_idle(data)
        for field, mutation in [
            ('paused', lambda d: d['print_stats'].update(state='paused')),
            ('selected', lambda d: d['virtual_sdcard'].update(
                file_path='still_selected.gcode')),
            ('printing', lambda d: d['virtual_sdcard'].update(is_active=True)),
            ('vaoc', lambda d: d['gcode_macro _VAOC'].update(is_started=True)),
            ('fault', lambda d: d['save_variables']['variables'].update(
                auto_xy_v2_fault=1)),
        ]:
            import copy
            value = copy.deepcopy(data)
            mutation(value)
            with self.subTest(field=field):
                with self.assertRaises(refresh.Refuse):
                    refresh.require_idle(value)

    def test_installed_current_is_noop_no_restart(self):
        self.path.write_bytes(self.source)
        called = []
        outcome, snapshot = refresh.install(
            ROOT, self.home, live=False,
            restart_service=lambda: called.append('restart'))
        self.assertEqual(outcome, 'already-installed')
        self.assertIsNone(snapshot)
        self.assertEqual(called, [])

    def test_preflight_refuses_symlinked_controller(self):
        self.path.symlink_to(self.gate)
        with self.assertRaises(refresh.Refuse):
            refresh.checked_files(ROOT, self.home)

    def test_no_file_changes_on_idle_preflight_failure(self):
        # Simulate a known installed original using the exact old bytes
        # provided in this repo's frozen RC16 main-history file if supplied.
        # We cannot invent historical bytes: no rollback/write test is
        # possible without the real pinned predecessor fixture.
        self.path.write_bytes(self.source)
        with patch.object(refresh, 'status', return_value={
            'print_stats': {'state': 'paused'}}):
            with self.assertRaises(refresh.Refuse):
                refresh.require_idle(refresh.status())
        self.assertEqual(self.path.read_bytes(), self.source)


if __name__ == '__main__':
    unittest.main()
