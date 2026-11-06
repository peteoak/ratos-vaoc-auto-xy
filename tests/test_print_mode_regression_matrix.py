"""Offline regression tests of public AutoXY RC15 dev6 print-mode gates.

Runs the ACTUAL source controller with fake RatOS status objects. No heaters,
motors, VAOC, network calls, offset writes or private recovery code.
"""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autoxy_public_mode_audit", ROOT / "src/auto_xy_gate.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


class FakeCommand:
    def __init__(self, **params):
        self.params = params
        self.messages = []

    def get(self, key, default=None):
        return self.params.get(key, default)

    def respond_info(self, message):
        self.messages.append(message)

    def error(self, message):
        return RuntimeError(message)


def make_gate(*, enabled=True, protected=False, sd=True, mode="inactive",
              shifts=None, fault=False, in_progress=False, session=None):
    gate = public.AutoXYGate.__new__(public.AutoXYGate)
    gate.busy = False
    gate._endurance_request = None
    gate._retry_precommitted = False
    gate._retry_context = None
    gate.protected_print_enabled = protected
    gate._protected_inside_gate = False
    gate._protected_session = session
    gate.pending_print_mode = None
    gate.pending_toolshifts = None
    gate.pending_reuse_candidate = False
    gate.active_print_mode = None
    gate._slicer_printing_temps = None
    gate._slicer_camera_targets = None
    gate.thermal_mode = "ratos"
    gate.reuse_enabled = lambda: False
    gate.enabled = lambda: enabled
    gate.virtual_sd_active = lambda: sd
    gate.flag = lambda name: {
        "auto_xy_v2_fault": fault,
        "auto_xy_v2_in_progress": in_progress,
        "auto_xy_v2_measure_only": False,
    }.get(name, False)
    gate.variables = lambda: {
        "auto_xy_v2_fault": 1 if fault else 0,
        "auto_xy_v2_in_progress": 1 if in_progress else 0,
    }
    gate.sts = lambda obj: {
        "dual_carriage": {"carriage_1": mode},
        "toolhead": {"homed_axes": ""},
    }[obj]
    gate.ensure_commissioned = lambda cmd: None
    gate._reuse_check = lambda **kwargs: (False, "not eligible")
    return gate


class StartCaptureMatrix(unittest.TestCase):
    def test_single_tool_t0_and_t1_virtual_sd_classify_single_tool(self):
        # First tool can be T0 or T1; the public capture reads shift count
        # and carriage mode, not the initial-tool parameter.
        for initial_tool in (0, 1):
            for state in ("inactive", "primary", "parked"):
                with self.subTest(initial_tool=initial_tool, carriage=state):
                    g = make_gate(mode=state)
                    cmd = FakeCommand(TOTAL_TOOLSHIFTS="0", INITIAL_TOOL=str(initial_tool))
                    g.cmd_AUTO_XY_CAPTURE_MODE(cmd)
                    self.assertEqual(g.pending_print_mode, "SINGLE_TOOL")
                    self.assertEqual(g.pending_toolshifts, 0)

    def test_independent_dual_tool_classification(self):
        for shifts in ("1", "2", "20"):
            with self.subTest(shifts=shifts):
                g = make_gate(mode="inactive")
                g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS=shifts))
                self.assertEqual(g.pending_print_mode, "INDEPENDENT")
                self.assertEqual(g.pending_toolshifts, int(shifts))

    def test_missing_shift_count_defaults_single_tool(self):
        g = make_gate()
        g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand())
        self.assertEqual(g.pending_print_mode, "SINGLE_TOOL")

    def test_copy_and_mirror_bypass_classification(self):
        for mode in ("copy", "mirror"):
            for shifts in ("0", "1"):
                with self.subTest(mode=mode, shifts=shifts):
                    g = make_gate(mode=mode)
                    g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS=shifts))
                    self.assertEqual(g.pending_print_mode, mode.upper())

    def test_console_start_cannot_impersonate_virtual_sd_print(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                g = make_gate(sd=False, protected=protected)
                g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="12"))
                self.assertIsNone(g.pending_print_mode)

    def test_invalid_toolshift_values_refused(self):
        for raw in ("-1", "not-a-number", "1.5", "nan"):
            with self.subTest(raw=raw):
                g = make_gate()
                with self.assertRaises(RuntimeError):
                    g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS=raw))

    def test_unsupported_carriage_mode_fails_closed_when_on(self):
        g = make_gate(mode="unknown")
        with self.assertRaisesRegex(RuntimeError, "Unknown RatOS dual_carriage"):
            g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="0"))

    def test_latched_fault_refused_before_heating_even_if_disabled(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                g = make_gate(enabled=enabled, fault=True)
                with self.assertRaisesRegex(RuntimeError, "safety fault"):
                    g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="0"))

    def test_protected_independent_requires_preparing_session(self):
        for session in (None, {"phase": "FINISHING"}, {"phase": "PREPARING", "invalid": True}):
            with self.subTest(session=session):
                g = make_gate(protected=True, session=session)
                with self.assertRaisesRegex(RuntimeError, "unprotected two-tool"):
                    g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="2"))

    def test_protected_independent_allows_valid_preparing_session(self):
        g = make_gate(protected=True, session={"phase": "PREPARING", "invalid": False})
        g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="2"))
        self.assertEqual(g.pending_print_mode, "INDEPENDENT")

    def test_protected_single_tool_requires_no_session(self):
        for state in ("inactive", "copy", "mirror"):
            with self.subTest(state=state):
                g = make_gate(protected=True, session=None, mode=state)
                g.cmd_AUTO_XY_CAPTURE_MODE(FakeCommand(TOTAL_TOOLSHIFTS="0"))
                self.assertEqual(g.pending_print_mode,
                                 state.upper() if state in ("copy", "mirror") else "SINGLE_TOOL")


class PostHeatsoakGateMatrix(unittest.TestCase):
    def gate(self, mode, *, protected, sd=True):
        g = make_gate(protected=protected, sd=sd)
        g.pending_print_mode = mode
        g.pending_toolshifts = 0
        cmd = FakeCommand(MODE="PRINT")
        g.cmd_AUTO_XY_GATE(cmd)
        return g, cmd

    def test_unprotected_single_copy_mirror_reach_bypass(self):
        for mode in ("SINGLE_TOOL", "COPY", "MIRROR"):
            with self.subTest(mode=mode):
                g, cmd = self.gate(mode, protected=False)
                self.assertIsNone(g.active_print_mode)
                self.assertTrue(any("skipping VAOC entirely" in t for t in cmd.messages))

    def test_protected_single_copy_mirror_must_still_bypass(self):
        """Regression: protected-file enforcement must not block bypass modes.

        This test is intentionally capable of failing against an existing
        public release. A failure is evidence of a regression, not permission
        to weaken INDEPENDENT protection.
        """
        for mode in ("SINGLE_TOOL", "COPY", "MIRROR"):
            with self.subTest(mode=mode):
                g, cmd = self.gate(mode, protected=True)
                self.assertIsNone(g.active_print_mode)
                self.assertTrue(any("skipping VAOC entirely" in t for t in cmd.messages))

    def test_protected_console_start_is_not_a_virtual_sd_print(self):
        g, cmd = self.gate("INDEPENDENT", protected=True, sd=False)
        self.assertTrue(any("not a virtual-SD print" in t for t in cmd.messages))

    def test_ordinary_two_tool_start_still_refused_when_protected(self):
        g = make_gate(protected=True, session=None)
        g.pending_print_mode = "INDEPENDENT"
        g.pending_toolshifts = 2
        with self.assertRaisesRegex(RuntimeError, "requires AUTOXY_PRINT_CALIBRATE"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

    def test_disabled_autoxy_bypasses_without_offset_writes(self):
        g = make_gate(enabled=False, protected=True)
        g.pending_print_mode = "SINGLE_TOOL"
        cmd = FakeCommand(MODE="PRINT")
        g.cmd_AUTO_XY_GATE(cmd)
        self.assertIsNone(g.pending_print_mode)
        self.assertTrue(any("OFF" in s for s in cmd.messages))

    def test_no_homing_refuses_unprotected_independent_before_motion(self):
        g = make_gate(protected=False)
        g.pending_print_mode = "INDEPENDENT"
        g.pending_toolshifts = 1
        with self.assertRaisesRegex(RuntimeError, "Printer must be homed"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

    def test_latched_fault_blocks_even_bypass(self):
        g = make_gate(protected=False, fault=True)
        g.pending_print_mode = "SINGLE_TOOL"
        with self.assertRaisesRegex(RuntimeError, "fault latched"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

    def test_unsupported_gate_mode_fails_closed(self):
        g = make_gate()
        with self.assertRaisesRegex(RuntimeError, "MODE must be"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="OTHER"))


    def test_protected_independent_blocks_before_reuse_shortcut(self):
        g = make_gate(protected=True)
        g.pending_print_mode = "INDEPENDENT"
        g.pending_toolshifts = 1
        g.pending_reuse_candidate = True
        g.reuse_enabled = lambda: True
        g._reuse_check = lambda **kwargs: (True, "eligible")
        with self.assertRaisesRegex(RuntimeError, "requires AUTOXY_PRINT_CALIBRATE"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

    def test_protected_independent_retry_without_owned_phase_blocked(self):
        g = make_gate(protected=True, sd=False)
        g._retry_precommitted = True
        g.active_print_mode = "INDEPENDENT"
        with self.assertRaisesRegex(RuntimeError, "requires AUTOXY_PRINT_CALIBRATE"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

    def test_protected_independent_inside_owned_phase_passes_guard(self):
        g = make_gate(protected=True)
        g._protected_inside_gate = True
        g.pending_print_mode = "INDEPENDENT"
        g.pending_toolshifts = 1
        # Deliberately stop at the next prerequisite; no hardware is simulated.
        with self.assertRaisesRegex(RuntimeError, "Printer must be homed"):
            g.cmd_AUTO_XY_GATE(FakeCommand(MODE="PRINT"))

class SlicerThermalMatrix(unittest.TestCase):
    def test_dual_temp_inputs_remain_independent(self):
        p, target = public.slicer_nozzle_targets("210,245", (50.0, 50.0), 200.0)
        self.assertEqual(p, [210.0, 245.0])
        self.assertEqual(target, [160.0, 195.0])

    def test_bad_temperature_inputs_fail_closed(self):
        for raw in ("", "210", "210,", "210,NaN", "999,210", "210,245,250", "-2,210"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    public.slicer_nozzle_targets(raw, (50.0, 50.0), 200.0)


if __name__ == "__main__":
    unittest.main()
