import ast
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class ControllerPreservationTests(unittest.TestCase):
    def setUp(self):
        self.old=ast.parse((ROOT/'tests/fixtures/dev3_gate.py').read_text())
        self.new=ast.parse((ROOT/'src/auto_xy_gate.py').read_text())
    @staticmethod
    def methods(tree):
        klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AutoXYGate')
        return {n.name:n for n in klass.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    def test_production_motion_calibration_and_endurance_functions_byte_equivalent_ast(self):
        a=self.methods(self.old);b=self.methods(self.new)
        allowed={'__init__','_persistent_capture','worker_capture','cmd_AUTO_XY_HEALTH','cmd_AUTO_XY_GATE'}
        for name,fn in a.items():
            if name in allowed:continue
            with self.subTest(function=name):
                self.assertIn(name,b)
                self.assertEqual(ast.dump(fn,include_attributes=False),
                                 ast.dump(b[name],include_attributes=False))
    def test_legacy_worker_and_detector_configuration_default(self):
        src=(ROOT/'src/auto_xy_gate.py').read_text()
        self.assertIn("config.get('camera_detector_mode','legacy')",src)
        self.assertIn("self.commissioned_detector_guard",src)
        self.assertIn('requires commissioned_detector_guard: True',src)
        self.assertIn("'detector_mode':self.camera_detector_mode",src)
        self.assertIn("'--detector-mode',self.camera_detector_mode",src)
    def test_no_extra_gcode_macro_or_new_motion_calls(self):
        a=self.methods(self.old);b=self.methods(self.new)
        self.assertEqual(set(a),set(b))
        for name in ('cmd_AUTO_XY_ENDURANCE_RUN','move','approach','measure','calibrate','endurance','tool'):
            if name in a:
                self.assertEqual(ast.dump(a[name],include_attributes=False),
                                 ast.dump(b[name],include_attributes=False),name)

if __name__=='__main__':unittest.main(verbosity=2)
