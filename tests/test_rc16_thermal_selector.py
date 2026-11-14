"""RC16 Mainsail thermal strategy: no offset writes, bounded targets."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rc16_gate_thermal',
                                              ROOT / 'src/auto_xy_gate_rc16.py')
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)


class Cmd:
    def __init__(self, **params):
        self.params = params
        self.messages = []
    def get(self, name, default=None):
        return self.params.get(name, default)
    def respond_info(self, text):
        self.messages.append(text)
    def error(self, text):
        return RuntimeError(text)


class ThermalSelectorTests(unittest.TestCase):
    def setUp(self):
        self.gate = gate_module.AutoXYGate.__new__(gate_module.AutoXYGate)
        gate = self.gate
        self.vars = {}
        self.sent = []
        gate.variables = lambda: self.vars
        gate.run_gcode = lambda text: self.sent.append(text)
        gate.thermal_mode = 'ratos'
        gate.thermal_reductions = [50., 50.]
        gate.thermal_fixed = [125., 125.]
        gate.thermal_band = 2.
        gate.max_camera_target_temp = 150.
        gate.max_camera_nozzle_temp = 155.
        gate.busy = False
        gate._protected_session = None
        gate.virtual_sd_active = lambda: False
        gate.sts = lambda key: {'print_stats': {'state': 'standby'},
                                'gcode_macro _VAOC': {'is_started': False}}[key]

    def test_default_preserves_ratos_and_saved_selection(self):
        self.assertEqual(self.gate.selected_thermal_mode(), 'ratos')
        for code, expected in enumerate(('ratos','slicer_derived','fixed')):
            self.vars['auto_xy_v2_thermal_strategy'] = code
            self.assertEqual(self.gate.selected_thermal_mode(), expected)
        self.vars['auto_xy_v2_thermal_strategy'] = 5
        with self.assertRaisesRegex(Exception, 'Unexpected saved'):
            self.gate.selected_thermal_mode()

    def test_fixed_temperature_independent_and_bounded(self):
        self.gate.cmd_AUTO_XY_THERMAL_SET(Cmd(MODE='FIXED', T0='132', T1='141'))
        self.assertEqual(self.sent, [
            'SAVE_VARIABLE VARIABLE=auto_xy_v2_thermal_fixed_t0 VALUE=132.000',
            'SAVE_VARIABLE VARIABLE=auto_xy_v2_thermal_fixed_t1 VALUE=141.000',
            'SAVE_VARIABLE VARIABLE=auto_xy_v2_thermal_strategy VALUE=2'])
        self.sent.clear()
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            self.gate.cmd_AUTO_XY_THERMAL_SET(Cmd(MODE='FIXED', T0='160', T1='125'))
        self.assertEqual(self.sent, [])

    def test_slicer_reduction_independent_per_nozzle(self):
        self.gate.cmd_AUTO_XY_THERMAL_SET(
            Cmd(MODE='SLICER_DERIVED', T0='40', T1='60'))
        self.assertEqual(self.sent[-1],
                         'SAVE_VARIABLE VARIABLE=auto_xy_v2_thermal_strategy VALUE=1')
        self.vars['auto_xy_v2_thermal_reduction_t0'] = 40
        self.vars['auto_xy_v2_thermal_reduction_t1'] = 60
        self.assertEqual(self.gate.selected_thermal_values('slicer_derived'),
                         [40., 60.])

    def test_refuse_changes_while_printing_or_held_or_native_vaoc(self):
        self.gate._protected_session = {'phase': 'MANUAL_REQUIRED'}
        with self.assertRaisesRegex(RuntimeError, 'active/held'):
            self.gate.cmd_AUTO_XY_THERMAL_SET(Cmd(MODE='RATOS'))
        self.gate._protected_session = None
        self.gate.sts = lambda name: {'print_stats': {'state': 'paused'},
                                     'gcode_macro _VAOC': {'is_started': False}}[name]
        with self.assertRaisesRegex(RuntimeError, 'paused/active'):
            self.gate.cmd_AUTO_XY_THERMAL_SET(Cmd(MODE='RATOS'))
        self.assertEqual(self.sent, [])

    def test_menu_macros_are_included_and_official_prompt_actions_used(self):
        menu = (ROOT / 'config/auto_xy_thermal_rc16_macros.cfg').read_text()
        self.assertIn('action:prompt_begin', menu)
        self.assertIn('action:prompt_button', menu)
        self.assertIn('AUTO_XY_THERMAL_SET MODE=FIXED', menu)
        self.assertIn('AUTO_XY_THERMAL_SET MODE=SLICER_DERIVED', menu)
        self.assertIn('AUTO_XY_THERMAL_SET MODE=RATOS', menu)
        self.assertNotIn('SET_GCODE_OFFSET', menu)


if __name__ == '__main__':
    unittest.main()
