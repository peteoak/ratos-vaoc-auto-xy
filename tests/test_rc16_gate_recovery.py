"""RC16 unit tests: native manual VAOC handoff and independent hold timeout.

No actual printer or RatOS subprocess. Adapted as fresh tests against the
RC16 controller, without copying the owner's private trial fixtures.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rc16_gate', ROOT / 'src/auto_xy_gate_rc16.py')
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)


class Clock:
    def __init__(self, value=100.0):
        self.t = value
    def monotonic(self):
        return self.t


class Handoff:
    installed = True
    def __init__(self):
        self.releases = []
    def release_protected_m24(self, gcmd):
        self.releases.append('M24')


class Printer:
    def __init__(self):
        self.handoff = Handoff()
    def get_state_message(self):
        return 'Printer ready', 'ready'
    def lookup_object(self, name, default=None):
        if name == 'auto_xy_ratos_handoff_rc16':
            return self.handoff
        return default


class Gcode:
    def __init__(self):
        self.commands = []
        self.ui = []
    def run_script_from_command(self, line):
        self.commands.append(line)
    def respond_raw(self, line):
        self.ui.append(line)


class Cmd:
    def __init__(self):
        self.messages = []
    def error(self, line):
        return RuntimeError(line)
    def respond_info(self, line):
        self.messages.append(line)


def fixture(phase='MANUAL_REQUIRED'):
    gate = gate_module.AutoXYGate.__new__(gate_module.AutoXYGate)
    gate.protected_print_enabled = True
    gate.reactor = Clock()
    gate.printer = Printer()
    gate.gcode = Gcode()
    gate._protected_session = {
        'phase': phase, 'invalid': False, 'filename': 'RC16.gcode',
        'started': 20.0, 'bed_target': 60.0, 'manual_seen': True,
        'initial_offsets': (0.0, 0.0, 0.0), 'motor_signature': None
    }
    gate._protected_printer_file = lambda: ('RC16.gcode', 'paused')
    gate.vaoc = False
    gate.sts = lambda name: {
        'toolhead': {'homed_axes': 'xyz'},
        'heater_bed': {'target': 60.0, 'temperature': 60.0},
        'gcode_macro _VAOC': {'is_started': gate.vaoc}
    }[name]
    gate.flags = {'auto_xy_v2_fault': True, 'auto_xy_v2_in_progress': True}
    gate.flag = lambda key: gate.flags.get(key, False)
    gate.offsets = lambda: (0.1, -0.2, 0.02)
    def run(line):
        if line.startswith('SAVE_VARIABLE'):
            parts = dict(x.split('=',1) for x in line.split()[1:])
            gate.flags[parts['VARIABLE']] = bool(int(parts['VALUE']))
    gate.run_gcode = run
    return gate


class ProtectedRecoveryTests(unittest.TestCase):
    def test_long_heatsoak_does_not_consume_held_recovery_window(self):
        gate = fixture()
        gate.reactor.t = 2600.0
        gate._protected_printer_file = lambda: ('RC16.gcode', 'printing')
        gate._protected_prompt = lambda page: None
        gate.cmd_AUTOXY_PRINT_HOLD(Cmd())
        self.assertEqual(gate.gcode.commands, ['M25'])
        self.assertEqual(gate._protected_session['held_at'], 2600.0)
        gate._protected_printer_file = lambda: ('RC16.gcode', 'paused')
        gate.reactor.t = 4000.0
        self.assertIs(gate._protected_state(paused=True), gate._protected_session)

    def test_held_timeout_fails_closed(self):
        gate = fixture()
        gate._protected_session['held_at'] = 2500.0
        gate.reactor.t = 5201.0
        with self.assertRaisesRegex(Exception, 'held-print recovery timed out'):
            gate._protected_state(paused=True)
        self.assertTrue(gate._protected_session['invalid'])

    def test_startup_timeout_without_hold_remains(self):
        gate = fixture('PREPARING')
        gate._protected_printer_file = lambda: ('RC16.gcode', 'printing')
        gate.reactor.t = 3000.0
        with self.assertRaisesRegex(Exception, 'print-start recovery timed out'):
            gate._protected_state(paused=False)

    def test_manual_acceptance_requires_vaoc_exit(self):
        gate = fixture('MANUAL_ACTIVE')
        gate.vaoc = True
        with self.assertRaisesRegex(Exception, 'must be exited'):
            gate.cmd_AUTOXY_MANUAL_ACCEPT(Cmd())
        self.assertEqual(gate._protected_session['phase'], 'MANUAL_ACTIVE')

    def test_manual_acceptance_and_one_use_guarded_resume(self):
        gate = fixture('MANUAL_ACTIVE')
        gate.cmd_AUTOXY_MANUAL_ACCEPT_CONTINUE(Cmd())
        self.assertEqual(gate._protected_session['phase'], 'MANUAL_AUTHORIZED')
        self.assertEqual(gate.printer.handoff.releases, ['M24'])
        self.assertFalse(gate.flags['auto_xy_v2_fault'])
        self.assertFalse(gate.flags['auto_xy_v2_in_progress'])
        gate._protected_printer_file = lambda: ('RC16.gcode', 'printing')
        gate.cmd_AUTOXY_PRINT_CHECK(Cmd())
        self.assertEqual(gate._protected_session['phase'], 'GUARD_CONSUMED')
        with self.assertRaises(Exception):
            gate.cmd_AUTOXY_PRINT_CHECK(Cmd())

    def test_recovery_refuses_release_without_handoff(self):
        gate = fixture('MANUAL_AUTHORIZED')
        gate.flags = {'auto_xy_v2_fault': False,
                      'auto_xy_v2_in_progress': False}
        gate.printer.handoff = None
        with self.assertRaisesRegex(Exception, 'print handoff missing'):
            gate.cmd_AUTOXY_PRINT_CONTINUE(Cmd())

    def test_normal_resume_cannot_bypass_barrier(self):
        gate = fixture()
        gate._protected_printer_file = lambda: ('RC16.gcode', 'printing')
        with self.assertRaisesRegex(Exception, 'unauthorized'):
            gate.cmd_AUTOXY_PRINT_CHECK(Cmd())
        self.assertTrue(gate._protected_session['invalid'])

    def test_rehome_invalidates_manual_recovery(self):
        gate = fixture('MANUAL_ACTIVE')
        gate._invalidate_held_recovery('Homing started')
        self.assertTrue(gate._protected_session['invalid'])
        with self.assertRaises(Exception):
            gate.cmd_AUTOXY_MANUAL_ACCEPT(Cmd())

    def test_rehome_during_prepare_does_not_invalidate(self):
        gate = fixture('PREPARING')
        gate._invalidate_held_recovery('Normal START_PRINT homing')
        self.assertFalse(gate._protected_session['invalid'])

    def test_motor_disable_invalidates_held_recovery(self):
        gate = fixture('MANUAL_REQUIRED')
        gate._invalidate_held_recovery('Motor disabled')
        self.assertTrue(gate._protected_session['invalid'])

    def test_standalone_is_not_a_print_release(self):
        gate = fixture()
        self.assertFalse(hasattr(gate, '_standalone_held_print_resume'))


if __name__ == '__main__':
    unittest.main()
