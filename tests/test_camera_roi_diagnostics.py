"""Full-frame optical diagnostics: never turn image evidence into nozzle selection."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_roi_diagnostics', ROOT / 'scripts/inspect_camera_roi.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class ROIInspectorTests(unittest.TestCase):
    def test_crop_is_refused_even_when_inside_camera_frame(self):
        self.assertEqual(diagnostics.parse_roi('auto', 1200, 900),
                         (0, 0, 1200, 900))
        with self.assertRaisesRegex(ValueError, 'do not tighten'):
            diagnostics.parse_roi('200,100,520,520', 1200, 900)

    def test_diagnostic_ring_contrast_distinguishes_high_from_low(self):
        image = np.full((900, 1200), 90, dtype=np.uint8)
        # Independent evidence signal: bright annulus around dark centre.
        cv2.circle(image, (600, 420), 132, 235, -1)
        cv2.circle(image, (600, 420), 57, 65, -1)
        true_ring = diagnostics.annular_contrast(image, 600., 420., 180.)
        # A flat background is not a real nozzle rim.
        image[:] = 90
        false_ring = diagnostics.annular_contrast(image, 600., 630., 180.)
        self.assertGreater(true_ring['annular_minus_center_gray'], 70.)
        self.assertEqual(false_ring['annular_minus_center_gray'], 0.)

    def test_multiple_candidates_remain_ambiguous_even_with_different_contrast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.jpg'
            image = np.full((900, 1200, 3), 85, dtype=np.uint8)
            cv2.imwrite(str(path), image)
            candidates = np.array(
                [[[600., 420., 180.], [620., 640., 176.]]], dtype=np.float32)
            with patch.object(diagnostics.cv2, 'HoughCircles',
                              return_value=candidates):
                _, result = diagnostics.inspect_image(path, roi='auto')
            self.assertEqual(result['candidate_count'], 2)
            self.assertEqual(result['status'], 'AMBIGUOUS')
            self.assertEqual(len(result['candidate_evidence_diagnostic_only']), 2)
            self.assertNotIn('selected_candidate', result)
            with self.assertRaisesRegex(ValueError, 'do not tighten'):
                diagnostics.inspect_image(path, roi='250,250,520,520')

    def test_diagnostic_writes_never_modify_source_or_existing_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            image = np.full((512, 512, 3), 120, dtype=np.uint8)
            source = Path(directory) / 'camera.jpg'
            preview = Path(directory) / 'camera_nozzle_candidates.jpg'
            cv2.imwrite(str(source), image)
            before = source.read_bytes()
            data = {'roi_xywh': [0, 0, 512, 512],
                    'candidates_xy_radius_px': []}
            diagnostics.annotate(image, data, preview)
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'will not overwrite'):
                diagnostics.annotate(image, data, preview)


if __name__ == '__main__':
    unittest.main()
