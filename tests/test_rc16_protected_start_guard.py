"""Regression checks for the fail-closed RC16 legacy start gate.

These are static invariants, not a substitute for supervised printer tests.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProtectedStartGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'src/auto_xy_gate_rc16.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.method = next(
            f for node in cls.tree.body if isinstance(node, ast.ClassDef)
            for f in node.body if isinstance(f, ast.FunctionDef)
            and f.name == 'cmd_AUTO_XY_GATE')

    def test_guard_precedes_non_virtual_sd_return(self):
        lines = self.source.splitlines()
        body = '\n'.join(lines[self.method.lineno - 1:self.method.end_lineno])
        guard = body.index('self.protected_print_enabled and not self._protected_inside_gate')
        early_return = body.index('not self.virtual_sd_active()')
        self.assertLess(guard, early_return,
                        'Unknown direct starts must not escape through console bypass')

    def test_guard_exempts_only_positively_classified_non_idex_modes(self):
        lines = self.source.splitlines()
        body = '\n'.join(lines[self.method.lineno - 1:self.method.end_lineno])
        self.assertIn("self.pending_print_mode not in\n                 ('SINGLE_TOOL', 'COPY', 'MIRROR')", body)
        self.assertIn('self._retry_precommitted or self.pending_print_mode', body)
        self.assertIn('Ordinary START_PRINT cannot safely hold original G-code.', body)


if __name__ == '__main__':
    unittest.main()
