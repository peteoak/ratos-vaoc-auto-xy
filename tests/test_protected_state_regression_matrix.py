"""Offline fail-closed matrix for the public opt-in protected-print state machine.

Only public GitHub source is exercised. All printer state and motion are mocked.
No private recovery implementation or hardware is used.
"""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autoxy_public_state_audit", ROOT / "src/auto_xy_gate.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Command:
    def __init__(self):
        self.messages = []
    def error(self, message):
        return RuntimeError(message)
    def respond_info(self, message):
        self.messages.append(message)


class Printer:
    def get_state_message(self):
        return ("Printer is ready", "ready")


class Gcode:
    def __init__(self):
        self.commands = []
    def run_script_from_command(self, command):
        self.commands.append(command)


class Reactor:
    def __init__(self):
        self.now = 110.0
    def monotonic(self):
        return self.now


def gate():
    g = m.AutoXYGate.__new__(m.AutoXYGate)
    g.protected_print_enabled = True
    g.printer = Printer()
    g.reactor = Reactor()
    g.gcode = Gcode()
    g._protected_session = {
        "filename": "test.gcode",
        "phase": "AUTO_VERIFIED",
        "invalid": False,
        "started": 100.0,
        "bed_target": 60.0,
        "motor_signature": ("enabled",),
    }
    g._status = {
        "print_stats": {"filename": "test.gcode", "state": "printing"},
        "toolhead": {"homed_axes": "xyz"},
        "heater_bed": {"target": 60.0, "temperature": 59.0},
        "gcode_macro _VAOC": {"is_started": False},
    }
    g.sts = lambda key: g._status[key]
    g._motion_motor_signature = lambda: ("enabled",)
    g.flag = lambda _: False
    g.enabled = lambda: True
    return g


class ProtectedStateMatrix(unittest.TestCase):
    def test_happy_path_validates_original_filename_and_mode(self):
        g = gate()
        self.assertIs(g._protected_state("AUTO_VERIFIED", paused=False), g._protected_session)

    def test_disabled_configuration_refused(self):
        g = gate()
        g.protected_print_enabled = False
        with self.assertRaises(m.GateAbort):
            g._protected_state()

    def test_missing_session_refused(self):
        g = gate()
        g._protected_session = None
        with self.assertRaises(m.GateAbort):
            g._protected_state()

    def test_changed_filename_invalidates_session(self):
        g = gate()
        g._status["print_stats"]["filename"] = "other.gcode"
        with self.assertRaisesRegex(m.GateAbort, "filename changed"):
            g._protected_state()
        self.assertTrue(g._protected_session["invalid"])

    def test_elapsed_start_timeout_invalidates_session(self):
        g = gate()
        g.reactor.now = 3000.0
        with self.assertRaisesRegex(m.GateAbort, "timed out"):
            g._protected_state()
        self.assertTrue(g._protected_session["invalid"])

    def test_incorrect_phase_refused(self):
        g = gate()
        with self.assertRaisesRegex(m.GateAbort, "expected"):
            g._protected_state("MANUAL_REQUIRED")

    def test_wrong_virtual_sd_pause_state_refused(self):
        g = gate()
        with self.assertRaisesRegex(m.GateAbort, "paused original"):
            g._protected_state(paused=True)
        g._status["print_stats"]["state"] = "paused"
        with self.assertRaisesRegex(m.GateAbort, "actively running"):
            g._protected_state(paused=False)

    def test_unhomed_axes_refused(self):
        g = gate()
        g._status["toolhead"]["homed_axes"] = "xy"
        with self.assertRaisesRegex(m.GateAbort, "homing"):
            g._protected_state()

    def test_motor_disable_invalidates_session(self):
        g = gate()
        g._motion_motor_signature = lambda: ("disabled",)
        with self.assertRaisesRegex(m.GateAbort, "stepper enable state changed"):
            g._protected_state()
        self.assertTrue(g._protected_session["invalid"])

    def test_bed_target_or_temperature_change_refused(self):
        for target, actual in ((55.0, 59.0), (60.0, 52.0), (60.0, 65.0)):
            with self.subTest(target=target, actual=actual):
                g = gate()
                g._status["heater_bed"]["target"] = target
                g._status["heater_bed"]["temperature"] = actual
                with self.assertRaisesRegex(m.GateAbort, "heatsoak no longer valid"):
                    g._protected_state()

    def test_not_running_or_paused_filename_refused(self):
        g = gate()
        g._status["print_stats"]["state"] = "complete"
        with self.assertRaises(m.GateAbort):
            g._protected_state()

    def test_guard_checks_validated_auto_result(self):
        g = gate()
        g.cmd_AUTOXY_PRINT_CHECK(Command())
        self.assertEqual(g._protected_session["phase"], "GUARD_CONSUMED")

    def test_guard_checks_validated_manual_result(self):
        g = gate()
        g._protected_session["phase"] = "MANUAL_AUTHORIZED"
        g.cmd_AUTOXY_PRINT_CHECK(Command())
        self.assertEqual(g._protected_session["phase"], "GUARD_CONSUMED")

    def test_guard_rejects_unvalidated_phase(self):
        for phase in ("PREPARING", "MANUAL_REQUIRED", "RETRY_REQUIRED", "RETRY_ACTIVE", "FINISHING"):
            with self.subTest(phase=phase):
                g = gate()
                g._protected_session["phase"] = phase
                with self.assertRaisesRegex(RuntimeError, "unauthorized continuation"):
                    g.cmd_AUTOXY_PRINT_CHECK(Command())
                self.assertTrue(g._protected_session["invalid"])

    def test_guard_rejects_autoxy_turned_off(self):
        g = gate()
        g.enabled = lambda: False
        with self.assertRaisesRegex(RuntimeError, "switched OFF"):
            g.cmd_AUTOXY_PRINT_CHECK(Command())
        self.assertTrue(g._protected_session["invalid"])

    def test_guard_rejects_active_vaoc(self):
        g = gate()
        g._status["gcode_macro _VAOC"]["is_started"] = True
        with self.assertRaisesRegex(RuntimeError, "VAOC still active"):
            g.cmd_AUTOXY_PRINT_CHECK(Command())

    def test_guard_rejects_fault_latch(self):
        g = gate()
        g.flag = lambda key: key == "auto_xy_v2_fault"
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            g.cmd_AUTOXY_PRINT_CHECK(Command())

    def test_manual_continue_only_after_authorisation(self):
        g = gate()
        g._status["print_stats"]["state"] = "paused"
        g._protected_session["phase"] = "MANUAL_AUTHORIZED"
        g.cmd_AUTOXY_PRINT_CONTINUE(Command())
        self.assertEqual(g.gcode.commands, ["M24"])

    def test_manual_continue_rejected_before_authorisation(self):
        g = gate()
        g._status["print_stats"]["state"] = "paused"
        g._protected_session["phase"] = "MANUAL_REQUIRED"
        with self.assertRaisesRegex(RuntimeError, "cannot continue"):
            g.cmd_AUTOXY_PRINT_CONTINUE(Command())
        self.assertEqual(g.gcode.commands, [])

    def test_continue_rejected_when_fault_latched(self):
        g = gate()
        g._status["print_stats"]["state"] = "paused"
        g._protected_session["phase"] = "MANUAL_AUTHORIZED"
        g.flag = lambda key: key == "auto_xy_v2_in_progress"
        with self.assertRaisesRegex(RuntimeError, "fault/in-progress"):
            g.cmd_AUTOXY_PRINT_CONTINUE(Command())
        self.assertEqual(g.gcode.commands, [])

    def test_finish_only_after_guard_consumed(self):
        g = gate()
        with self.assertRaises(m.GateAbort):
            g.cmd_AUTOXY_PRINT_FINISH_GATE(Command())
        g._protected_session["phase"] = "GUARD_CONSUMED"
        g.cmd_AUTOXY_PRINT_FINISH_GATE(Command())
        self.assertEqual(g._protected_session["phase"], "FINISHING")


if __name__ == "__main__":
    unittest.main()
