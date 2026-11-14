"""RC16 manual-VAOC full-frame ambiguity evidence; no position guess."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'src'))
spec=importlib.util.spec_from_file_location(
    'rc16_candidate_module', ROOT/'src/auto_xy_commission_candidates_rc16.py')
matcher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(matcher)


class CommissionCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'sample.png'
        self.image = np.full((960, 1000, 3), 86, dtype=np.uint8)
        # Simulate a real bright annular nozzle near the operator's reticle.
        cv2.circle(self.image, (500, 480), 145, (238,238,238), -1)
        cv2.circle(self.image, (500, 480), 61, (70,70,70), -1)
        cv2.imwrite(str(self.path), self.image)
        self.candidates=np.array([[[500.,480.,175.],[510.,701.,176.]]],
                                 dtype=np.float32)

    def test_full_frame_independent_evidence_discards_lower_reflection(self):
        with patch.object(matcher.cv2,'HoughCircles',return_value=self.candidates):
            x,y,r=matcher.resolve_manual_vaoc_ambiguity(
                self.path,operator_confirmed=True)
        self.assertEqual((x,y,r),(500.,480.,175.))

    def test_manual_confirmation_is_required(self):
        with patch.object(matcher.cv2,'HoughCircles',return_value=self.candidates):
            with self.assertRaisesRegex(matcher.CommissionAmbiguity,'confirmation'):
                matcher.resolve_manual_vaoc_ambiguity(
                    self.path,operator_confirmed=False)

    def test_two_equally_plausible_circles_never_autoselect(self):
        equal=np.array([[[490.,480.,175.],[540.,480.,174.]]],dtype=np.float32)
        with patch.object(matcher.cv2,'HoughCircles',return_value=equal):
            with self.assertRaises(matcher.CommissionAmbiguity):
                matcher.resolve_manual_vaoc_ambiguity(
                    self.path,operator_confirmed=True)

    def test_nozzle_far_from_reticle_is_not_selected(self):
        shifted=np.array([[[700.,480.,175.],[740.,700.,174.]]],dtype=np.float32)
        with patch.object(matcher.cv2,'HoughCircles',return_value=shifted):
            with self.assertRaisesRegex(matcher.CommissionAmbiguity,'reticle'):
                matcher.resolve_manual_vaoc_ambiguity(
                    self.path,operator_confirmed=True)


if __name__=='__main__':
    unittest.main()
