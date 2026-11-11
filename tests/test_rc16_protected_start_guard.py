"""Static RC16 start-gate invariants; real print qualification still required."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProtectedStartGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'src/auto_xy_gate_rc16.py').read_text(encoding='utf-8')
        tree = ast.parse(cls.source)
        cls.methods = {
            f.name: f for node in tree.body if isinstance(node, ast.ClassDef)
            for f in node.body if isinstance(f, ast.FunctionDef)}

    @classmethod
    def body(cls, name):
        node = cls.methods[name]
        return '\n'.join(cls.source.splitlines()[node.lineno - 1:node.end_lineno])

    def test_native_console_start_bypasses_protected_gate(self):
        gate = self.body('cmd_AUTO_XY_GATE')
        self.assertIn('not self.virtual_sd_active()', gate)
        self.assertIn('not a virtual-SD print; skipping automatic gate', gate)
        self.assertLess(gate.index('not self.virtual_sd_active()'),
                        gate.index('self.protected_print_enabled and not self._protected_inside_gate'),
                        'Console-started native VAOC must not be rejected by print-file guard')

    def test_positive_two_tool_capture_requires_protected_begin(self):
        capture = self.body('cmd_AUTO_XY_CAPTURE_MODE')
        self.assertIn("requested=='INDEPENDENT'", capture)
        self.assertIn("session.get('phase')!='PREPARING'", capture)
        self.assertIn('unprotected two-tool G-code refused before bed heat', capture)

    def test_protected_independent_print_cannot_reuse_prior_alignment(self):
        body = self.body('cmd_AUTO_XY_GATE')
        self.assertIn("if (not self.protected_print_enabled and", body)
        self.assertLess(body.index('not self.protected_print_enabled and'),
                        body.index('reuse_candidate and self.reuse_enabled()'))
        self.assertIn('ALWAYS get a fresh', body)

    def test_protected_gate_still_rejects_unconverted_idex(self):
        gate = self.body('cmd_AUTO_XY_GATE')
        self.assertIn('self.protected_print_enabled and not self._protected_inside_gate', gate)
        self.assertIn('Ordinary START_PRINT cannot safely hold original G-code.', gate)


if __name__ == '__main__':
    unittest.main()
