"""No-motion camera commissioning regressions for independent nozzle rims."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import commission_camera as legacy
import rc16_commission_camera as commission


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
        with patch.object(legacy, 'detect', side_effect=detect):
            profile = commission.create_profile(
                self.t0, self.t1, 'auto', 'local-test-camera', True)
        self.assertEqual(profile['roi'], 'auto')
        self.assertEqual(profile['tool0']['radius_px'], 121.0)
        self.assertEqual(profile['tool1']['radius_px'], 177.0)
        self.assertEqual(profile['tool0']['num_images'], 3)
        self.assertEqual(profile['tool1']['num_images'], 3)

    def test_rc16_requires_full_frame_roi(self):
        with self.assertRaisesRegex(ValueError, 'requires --roi auto'):
            commission.create_profile(
                self.t0, self.t1, '100,100,500,500',
                'local-test-camera', True)

    def test_ambiguous_nozzle_remains_fail_closed_without_crop_advice(self):
        def detect(path, roi=None):
            raise ValueError(
                'Ambiguous camera ROI: 2 nozzle-sized circles detected; '
                'commission a tighter image crop rather than guessing')
        with patch.object(legacy, 'detect', side_effect=detect):
            with self.assertRaises(ValueError) as error:
                commission.create_profile(
                    self.t0, self.t1, 'auto', 'local-test-camera', True)
        message = str(error.exception)
        self.assertIn('2 nozzle-sized circles', message)
        self.assertIn('Keep the complete camera frame', message)
        self.assertNotIn('tighter', message.lower())
        self.assertIn('rim wear or shape', message.lower())


if __name__ == '__main__':
    unittest.main()
