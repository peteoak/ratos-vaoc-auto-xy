"""Nozzle photo signatures are independent from XY position measurements."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
for name,path in [
    ('rc16_signatures','src/auto_xy_reference_signature_rc16.py'),
    ('rc16_photo_vision','src/auto_xy_vision_worker_rc16.py')]:
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    instance=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    globals()[name]=instance


class ReferenceSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths=[]
        for index in range(3):
            path=Path(self.temp.name)/('nozzle%d.png'%index)
            image=np.full((900,1200),80,dtype=np.uint8)
            cv2.circle(image,(600,440),146,210+index,-1)
            cv2.circle(image,(600,440),51,62+index,-1)
            cv2.imwrite(str(path),image)
            self.paths.append(path)

    def test_three_frames_produce_stable_signature_and_match(self):
        circles=[(600.,440.,146.)]*3
        reference=rc16_signatures.signature_from_frames(self.paths,circles)
        result=rc16_signatures.compare(
            self.paths[2], (600.,440.),146.,reference)
        self.assertTrue(result['matched'])
        self.assertEqual(reference['format'],'radial-v1')
        self.assertEqual(len(reference['values']),6)

    def test_different_optical_rim_must_refuse(self):
        reference=rc16_signatures.signature_from_frames(
            self.paths,[(600.,440.,146.)]*3)
        altered=Path(self.temp.name)/'changed.png'
        image=np.full((900,1200),210,dtype=np.uint8)
        cv2.circle(image,(600,440),146,65,-1)
        cv2.circle(image,(600,440),51,250,-1)
        cv2.imwrite(str(altered),image)
        with self.assertRaisesRegex(ValueError,'outer rim lacks commissioned likeness'):
            rc16_signatures.compare(altered,(600.,440.),146.,reference)

    def test_hot_nozzle_with_central_ooze_keeps_outer_rim_match(self):
        # Model hot ooze obscuring the centre, not the outer Hough rim.
        reference=rc16_signatures.signature_from_frames(
            self.paths,[(600.,440.,146.)]*3)
        noisy=Path(self.temp.name)/'oozing.png'
        original=cv2.imread(str(self.paths[1]),cv2.IMREAD_GRAYSCALE)
        cv2.circle(original,(600,440),90,5,-1)
        cv2.imwrite(str(noisy),original)
        evidence=rc16_signatures.compare(
            noisy,(600.,440.),146.,reference)
        self.assertTrue(evidence['matched'])
        self.assertEqual(evidence['method'],'outer-rim-v1')

    def test_flat_or_missing_outer_rim_is_rejected(self):
        reference=rc16_signatures.signature_from_frames(
            self.paths,[(600.,440.,146.)]*3)
        flat=Path(self.temp.name)/'flat.png'
        cv2.imwrite(str(flat),
                    np.full((900,1200),80,dtype=np.uint8))
        with self.assertRaisesRegex(ValueError,'contrast'):
            rc16_signatures.compare(flat,(600.,440.),146.,reference)

    def test_existing_commissioned_signature_format_is_preserved(self):
        reference=rc16_signatures.signature_from_frames(
            self.paths,[(600.,440.,146.)]*3)
        self.assertEqual(reference['format'],'radial-v1')
        evidence=rc16_signatures.compare(
            self.paths[1],(600.,440.),146.,reference)
        self.assertTrue(evidence['matched'])
        self.assertIn('full_face_difference_diagnostic',evidence)

    def test_vision_worker_only_checks_signature_after_tuned_hough(self):
        source=(ROOT/'src/auto_xy_vision_worker_rc16.py').read_text()
        self.assertLess(source.index('circle = detect('),
                        source.index('appearance = compare_nozzle_signature('))
        self.assertIn("result['appearance_evidence'] = appearance",source)
        self.assertIn("'expected_signature'",source)

    def test_missing_or_bad_signature_refuses(self):
        with self.assertRaisesRegex(ValueError,'Unknown'):
            rc16_signatures.compare(
                self.paths[0],(600.,440.),146.,{'format':'unknown'})


if __name__=='__main__':
    unittest.main()
