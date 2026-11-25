"""RC16 local annular detector-only update preserves unrelated sources."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_local_annular_refresh',
    ROOT / 'scripts/rc16_local_annular_refresh.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class LocalAnnularUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'auto_xy/rollbacks').mkdir(parents=True)
        self.source = (ROOT / mod.SOURCE).read_bytes()
        self.assertEqual(mod.blob(self.source), mod.NEW_BLOB)
        self.installed = self.home / mod.TARGET
        self.installed.parent.mkdir(parents=True)
        self.installed.write_bytes(b'old approved detector fixture')
        self.old_hash = mod.blob(self.installed.read_bytes())
        for name in ('auto_xy/klipper/auto_xy_gate.py',
                     'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py'):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'reviewed unchanged controller fixture')

    def override_pins(self):
        return (patch.object(mod, 'OLD_BLOB', self.old_hash),
                patch.object(mod, 'GATE_BLOB',
                             mod.blob((self.home / 'auto_xy/klipper/auto_xy_gate.py').read_bytes())),
                patch.object(mod, 'HANDOFF_BLOB',
                             mod.blob((self.home / 'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py').read_bytes())))

    def test_pinned_offline_update_snapshots_one_file(self):
        with self.override_pins()[0], self.override_pins()[1], self.override_pins()[2]:
            result, backup = mod.install(ROOT, self.home, live=False)
        self.assertEqual(result, 'updated')
        self.assertEqual(mod.blob(self.installed.read_bytes()), mod.NEW_BLOB)
        self.assertEqual((backup / 'previous_detector.py').read_bytes(),
                         b'old approved detector fixture')

    def test_failed_install_restores_previous_bytes(self):
        old = self.installed.read_bytes()
        def fail_restart():
            raise RuntimeError('injected readiness failure')
        with self.override_pins()[0], self.override_pins()[1], self.override_pins()[2], \
             patch.object(mod, 'status', return_value=self.safe_state()):
            with self.assertRaisesRegex(mod.Refuse, 'original detector restored'):
                mod.install(ROOT, self.home, live=True,
                            restart_service=fail_restart,
                            wait_ready=lambda: None)
        self.assertEqual(self.installed.read_bytes(), old)

    def test_rejects_unknown_binary_without_changes(self):
        before = self.installed.read_bytes()
        with self.assertRaises(mod.Refuse):
            mod.checked_files(ROOT, self.home)
        self.assertEqual(self.installed.read_bytes(), before)

    @staticmethod
    def safe_state():
        return {
            'print_stats': {'state': 'standby'},
            'virtual_sdcard': {'is_active': False, 'file_path': None,
                               'filename': None},
            'gcode_macro _VAOC': {'is_started': False},
            'auto_xy_ratos_handoff_rc16': {'armed': True, 'busy': False},
            'save_variables': {'variables': {'auto_xy_v2_enabled': 1}}
        }

    def test_preflight_refuses_paused_selected_vaoc_or_fault(self):
        from copy import deepcopy
        base = self.safe_state()
        mod.require_idle(base)
        changes = (
            lambda d: d['print_stats'].update(state='paused'),
            lambda d: d['virtual_sdcard'].update(file_path='held.gcode'),
            lambda d: d['gcode_macro _VAOC'].update(is_started=True),
            lambda d: d['save_variables']['variables'].update(auto_xy_v2_fault=1),
            lambda d: d['save_variables']['variables'].update(auto_xy_v2_in_progress=1),
        )
        for edit in changes:
            data = deepcopy(base)
            edit(data)
            with self.assertRaises(mod.Refuse):
                mod.require_idle(data)


if __name__ == '__main__':
    unittest.main()
