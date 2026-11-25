"""RC16 local annular fallback: tests use synthetic images, never user camera images.

The independent annular matcher may corroborate a unique commissioned Hough
circle when a stronger global annular peak lies on reflection or nozzle ooze.
This must not loosen commissioned geometry or select ambiguous alternatives.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'auto_xy_detector_rc16', ROOT / 'src/auto_xy_detector_rc16.py')
det = importlib.util.module_from_spec(spec)
spec.loader.exec_module(det)

EXPECTED = np.array([960.625, 546.875])
RADIUS = 151.25
CIRCLE = np.array([984.4, 511.9, 147.1])


class LocalAnnularTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.jpg'
        # A geometrically valid, normally lit synthetic full frame, no
        # capture of any original printer photo.
        frame = np.full((1080, 1920, 3), 98, dtype=np.uint8)
        cv2.circle(frame, (984, 512), 147, (200, 200, 200), 3)
        self.assertTrue(cv2.imwrite(str(self.path), frame))

    def detect_mocked(self, local_score=.239, global_score=.241,
                      local_offset=(8., 0.), hough=None,
                      detector_mode='two_stage_roi'):
        seen = []
        def coarse(gray, center, radius, search_radius_px=det.COARSE_SEARCH_RADIUS_PX):
            if search_radius_px == det.COARSE_SEARCH_RADIUS_PX:
                # A distant misleading global annular peak.
                return np.array([1052., 540.]), global_score
            seen.append(search_radius_px)
            return np.asarray(center, dtype=float) + local_offset, local_score
        def precision(gray, x, y, radius, **kw):
            # Global coarse rejects the Hough circle, but independent local
            # matching may corroborate it after geometric commissioning.
            if kw.get('coarse_center') is not None:
                return None
            return CIRCLE.copy() if hough is None else hough
        with patch.object(det, '_coarse_nozzle_location', side_effect=coarse), \
             patch.object(det, '_hough', side_effect=precision):
            result = det.detect(
                self.path, roi='auto', expected_center=EXPECTED,
                expected_radius=RADIUS, detector_mode=detector_mode)
        return result, seen

    def test_near_equal_local_annular_peak_can_rescue_unique_hough(self):
        value, calls = self.detect_mocked()
        np.testing.assert_allclose(value, CIRCLE)
        self.assertEqual(calls, [det.COARSE_MAX_HOUGH_ERROR_PX])

    def test_lower_scoring_local_peak_must_not_pass(self):
        with self.assertRaisesRegex(ValueError, 'No plausible nozzle rim'):
            self.detect_mocked(local_score=.202)

    def test_local_peak_outside_original_18px_guard_must_not_pass(self):
        with self.assertRaisesRegex(ValueError, 'No plausible nozzle rim'):
            self.detect_mocked(local_offset=(22., 0.))

    def test_existing_non_roi_two_stage_does_not_use_fallback(self):
        with self.assertRaisesRegex(ValueError, 'No plausible nozzle rim'):
            self.detect_mocked(detector_mode='two_stage')

    def test_true_ambiguous_hough_circles_must_still_fail_closed(self):
        def coarse(gray, center, radius, search_radius_px=det.COARSE_SEARCH_RADIUS_PX):
            return np.array([1052.,540.]), .241
        def precision(gray, x, y, radius, **kw):
            if kw.get('coarse_center') is not None:
                return None
            raise ValueError('Ambiguous camera ROI: 2 nozzle-sized circles detected')
        with patch.object(det, '_coarse_nozzle_location', side_effect=coarse), \
             patch.object(det, '_hough', side_effect=precision):
            with self.assertRaisesRegex(ValueError, 'Ambiguous camera ROI'):
                det.detect(self.path, expected_center=EXPECTED,
                           expected_radius=RADIUS, detector_mode='two_stage_roi')

    def test_real_annular_search_near_hough_returns_finite_local_match(self):
        frame = cv2.imread(str(self.path), cv2.IMREAD_GRAYSCALE)
        peak, score = det._coarse_nozzle_location(
            frame, np.array([984.4, 511.9]), RADIUS,
            search_radius_px=det.COARSE_MAX_HOUGH_ERROR_PX)
        self.assertLessEqual(np.linalg.norm(peak-np.array([984.4,511.9])),
                             det.COARSE_MAX_HOUGH_ERROR_PX)
        self.assertTrue(np.isfinite(score))

    def test_local_match_does_not_change_hough_precision_coordinates(self):
        hough = np.array([979.4, 509.4, 152.6])
        value, _ = self.detect_mocked(hough=hough)
        np.testing.assert_array_equal(value, hough)


if __name__ == '__main__':
    unittest.main()
