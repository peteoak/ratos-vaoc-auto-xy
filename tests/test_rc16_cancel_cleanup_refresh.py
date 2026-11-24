"""Legacy one-file RC16 cancellation updater retained for compatibility.

Its exact pinned source was superseded by the combined operator-enable update.
Verify it cannot accidentally overwrite a newer RC16 controller or handoff.
The current updater's detailed rollback tests live in test_rc16_operator_enable.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_cancel_cleanup_refresh',
    ROOT/'scripts/rc16_cancel_cleanup_refresh.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacyCancelRefreshTests(unittest.TestCase):
    def test_current_coordinator_is_not_the_legacy_pinned_update_source(self):
        source = (ROOT/legacy.SOURCE).read_bytes()
        self.assertNotEqual(legacy.blob(source), legacy.NEW_BLOB)

    def test_refuses_new_coordinator_without_mutating_installed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            handoff = home/legacy.TARGET
            handoff.parent.mkdir(parents=True)
            current = (ROOT/legacy.SOURCE).read_bytes()
            handoff.write_bytes(current)
            gate = home/'auto_xy/klipper/auto_xy_gate.py'
            gate.write_bytes((ROOT/'src/auto_xy_gate_rc16.py').read_bytes())
            before = handoff.read_bytes()
            with self.assertRaises(legacy.Refuse):
                legacy.checked_files(ROOT, home)
            self.assertEqual(before, handoff.read_bytes())

    def test_legacy_idle_validator_still_refuses_selected_or_recovery(self):
        data = {
            'print_stats': {'state':'standby'},
            'virtual_sdcard': {'is_active':False, 'file_path':None},
            'gcode_macro _VAOC': {'is_started':False},
            'auto_xy_ratos_handoff_rc16': {'armed':True, 'busy':False},
            'save_variables': {'variables':{}}
        }
        legacy.require_idle(data)
        data['virtual_sdcard']['file_path']='active.gcode'
        with self.assertRaises(legacy.Refuse):
            legacy.require_idle(data)
        data['virtual_sdcard']['file_path']=None
        data['save_variables']['variables']['auto_xy_v2_fault']=1
        with self.assertRaises(legacy.Refuse):
            legacy.require_idle(data)


if __name__=='__main__':
    unittest.main()
