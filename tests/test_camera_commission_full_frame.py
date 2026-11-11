"""No-motion camera commissioning regressions for independent nozzle rims."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'commission_camera_test', ROOT / 'scripts/commission_camera.py')
commission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commission)


class CameraCommissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.t0 = [root / ('t0_%d.jpg' % i) for i in range(3)]
        self.t1 = [root / ('t1_%d.jpg' % i) for i in range(3)]
        for i, path in enumerate(self.t0 + self.t1):
            path.write_bytes(('unique camera frame %d' % i).encode())

    def test_two_tools_can_have_distinct_valid_radii_with_full_frame(self):
        radius = {str(p): 121.0 for p in self.t0}
        radius.update({str(p): 177.0 for p in self.t1})
        def detect(path, roi=None):
            self.assertIsNone(roi, 'Commissioning must use the full camera frame')
            return (700.0, 550.0, radius[str(path)])
        with patch.object(commission, 'detect', side_effect=detect):
            profile = commission.create_profile(
                self.t0, self.t1, 'auto', 'local-test-camera', True)
        self.assertEqual(profile['roi'], 'auto')
        self.assertEqual(profile['tool0']['radius_px'], 121.0)
        self.assertEqual(profile['tool1']['radius_px'], 177.0)
        self.assertEqual(profile['tool0']['num_images'], 3)
        self.assertEqual(profile['tool1']['num_images'], 3)

    def test_ambiguous_nozzle_remains_fail_closed_without_crop_advice(self):
        def detect(path, roi=None):
            raise ValueError(
                'Ambiguous full-frame nozzle detection: 2 plausible rim circles')
        with patch.object(commission, 'detect', side_effect=detect):
            with self.assertRaises(ValueError) as error:
                commission.create_profile(
                    self.t0, self.t1, 'auto', 'local-test-camera', True)
        message = str(error.exception)
        self.assertIn('tool0 image t0_0.jpg', message)
        self.assertIn('Keep the full camera frame', message)
        self.assertNotIn('tighter', message.lower())
        self.assertIn('nozzle rim condition', message.lower())


if __name__ == '__main__':
    unittest.main()
