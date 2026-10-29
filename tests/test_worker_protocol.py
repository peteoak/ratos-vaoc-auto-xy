import ast
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class WorkerProtocolTests(unittest.TestCase):
    def test_capture_transport_unmodified_except_opt_in_flag(self):
        old=ast.parse((ROOT/'tests/fixtures/dev4_worker.py').read_text())
        new=ast.parse((ROOT/'src/auto_xy_vision_worker.py').read_text())
        oa={n.name:n for n in old.body if isinstance(n,ast.FunctionDef)}
        na={n.name:n for n in new.body if isinstance(n,ast.FunctionDef)}
        for name in ('serve','main'):
            self.assertEqual(ast.dump(oa[name],include_attributes=False),
                             ast.dump(na[name],include_attributes=False),name)
        text=(ROOT/'src/auto_xy_vision_worker.py').read_text()
        self.assertIn("'detector_mode'",text)
        self.assertIn('detector_mode=a.detector_mode',text)

if __name__=='__main__':unittest.main(verbosity=2)
