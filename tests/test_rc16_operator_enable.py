"""Operator-owned ON/OFF contract and historical update rejection tests.

The reviewed user-enable updater is superseded on this branch by the
fresh-no-write coordinator update. It must not silently consume the newer
controller. Transactional installation tests use the latest updater.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'legacy_operator_enable',
    ROOT/'scripts/rc16_operator_enable_refresh.py')
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)


class OperatorOwnedEnableTests(unittest.TestCase):
    def test_calibration_never_changes_user_on_off_preference(self):
        gate=(ROOT/'src/auto_xy_gate_rc16.py').read_text(encoding='utf-8')
        self.assertNotIn('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0',gate)
        self.assertNotIn('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=1',gate)
        self.assertIn("self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=1')",gate)

    def test_retry_dismiss_does_not_switch_off(self):
        macros=(ROOT/'config/auto_xy_macros.cfg').read_text()
        start=macros.index('[gcode_macro AUTO_XY_RETRY_DISMISS]')
        end=macros.index('[gcode_macro AUTO_XY_RETRY_PROMPT]',start)
        dismiss=macros[start:end]
        self.assertNotIn('auto_xy_v2_enabled VALUE=0',dismiss)
        self.assertNotIn('auto_xy_v2_enabled VALUE=1',dismiss)
        self.assertIn('auto_xy_v2_retry_available VALUE=0',dismiss)

    def test_protected_print_refusal_gives_specific_reasons(self):
        handoff=(ROOT/'src/auto_xy_ratos_handoff_rc16.py').read_text()
        for text in ('AutoXY is OFF by operator selection.',
                     'AutoXY calibration fault/interrupted run is latched.',
                     'AutoXY calibration is busy.'):
            self.assertIn(text,handoff)

    def test_legacy_updater_fails_closed_with_new_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            for target,src,old,new in legacy.FILES:
                path=home/target
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((ROOT/src).read_bytes())
            previous=(home/legacy.FILES[0][0]).read_bytes()
            with self.assertRaises(legacy.Refuse):
                legacy.checked_files(ROOT,home)
            self.assertEqual((home/legacy.FILES[0][0]).read_bytes(),previous)

    def test_legacy_updater_still_rejects_paused_and_faulted(self):
        base={'print_stats':{'state':'standby'},
              'virtual_sdcard':{'is_active':False,'file_path':None},
              'gcode_macro _VAOC':{'is_started':False},
              'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False},
              'save_variables':{'variables':{}}}
        legacy.require_idle(base)
        base['print_stats']['state']='paused'
        with self.assertRaises(legacy.Refuse):
            legacy.require_idle(base)
        base['print_stats']['state']='standby'
        base['save_variables']['variables']['auto_xy_v2_fault']=1
        with self.assertRaises(legacy.Refuse):
            legacy.require_idle(base)


if __name__=='__main__':
    unittest.main()
