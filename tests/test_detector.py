#!/usr/bin/env python3
"""Offline, real-photograph regression; run with /home/pi/auto-xy-venv/bin/python."""
import sys
from pathlib import Path
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from auto_xy_detector import detect

class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.t0=dict(expected_center=[957.2916666666666,544.375],expected_radius=149.875)
        self.t1=dict(expected_center=[961.4583333333334,545.2083333333334],expected_radius=153.625)
        self.photos=ROOT/'tests/fixtures'
    def test_recover_ambiguous_nozzle_without_guessing_nearest_profile(self):
        f=self.photos/'099_endurance_024_t0_b_attempt_2.jpg'
        with self.assertRaisesRegex(ValueError,'Ambiguous camera ROI'):
            detect(f,detector_mode='legacy',**self.t0)
        best=detect(f,detector_mode='two_stage',**self.t0)
        self.assertTrue(935 < best[0] < 950)
        self.assertTrue(552 < best[1] < 577)
        self.assertTrue(138 < best[2] < 166)
    def test_precision_centres_identical_on_accepted_images(self):
        cases=[('094_endurance_022_t0_a_attempt_1.jpg',self.t0),
               ('096_endurance_023_t1_a_attempt_1.jpg',self.t1),
               ('098_endurance_024_t0_a_attempt_2.jpg',self.t0)]
        for name,profile in cases:
            with self.subTest(image=name):
                a=detect(self.photos/name,detector_mode='legacy',**profile)
                b=detect(self.photos/name,detector_mode='two_stage',**profile)
                self.assertTrue(np.array_equal(a,b),f'{name} changed precision centre')
    def test_roi_crop_exact_precision_on_saved_photographs(self):
        cases=[('094_endurance_022_t0_a_attempt_1.jpg',self.t0),
               ('096_endurance_023_t1_a_attempt_1.jpg',self.t1),
               ('098_endurance_024_t0_a_attempt_2.jpg',self.t0),
               ('099_endurance_024_t0_b_attempt_2.jpg',self.t0)]
        for name,ref in cases:
            with self.subTest(name=name):
                original=detect(self.photos/name,detector_mode='two_stage',**ref)
                cropped=detect(self.photos/name,detector_mode='two_stage_roi',**ref)
                self.assertTrue(np.array_equal(original,cropped),name+' precision mismatch')
    def test_grid_alignment_and_fallback(self):
        from auto_xy_detector import _aligned_precision_crop
        img=np.zeros((1080,1920),dtype=np.uint8)
        crop,x,y=_aligned_precision_crop(img,np.array([942.,562.]),(115,185))
        self.assertEqual((x%5,y%5),(0,0))
        self.assertEqual(crop.shape,(520,520))
        fallback,x,y=_aligned_precision_crop(img,np.array([100.,100.]),(115,185))
        self.assertIs(fallback,img)
        self.assertEqual((x,y),(0,0))
    def test_fail_closed_without_profile(self):
        with self.assertRaisesRegex(ValueError,'commissioned'):
            detect(self.photos/'094_endurance_022_t0_a_attempt_1.jpg',
                   detector_mode='two_stage')
    def test_fail_closed_wrong_commission_reference(self):
        f=self.photos/'094_endurance_022_t0_a_attempt_1.jpg'
        with self.assertRaises(ValueError):
            detect(f,detector_mode='two_stage',expected_center=[200,150],expected_radius=149.875)
    def test_fail_closed_invalid_detector_mode(self):
        with self.assertRaisesRegex(ValueError,'Unknown detector'):
            detect(self.photos/'094_endurance_022_t0_a_attempt_1.jpg',detector_mode='invalid')
    def test_full_optical_sweep_when_archives_available(self):
        """Optional full replay with paths from supplied printer archive env var."""
        import os,re
        folder=os.getenv('AUTOXY_REPLAY_DIR')
        if not folder:self.skipTest('AUTOXY_REPLAY_DIR not set')
        files=sorted(p for p in Path(folder).rglob('*.jpg') if not p.name.startswith('detected_'))
        self.assertGreaterEqual(len(files),30,'Archive too small for full replay')
        unchanged=recovered=0
        for image in files:
            m=re.search(r'(?:^|_)t([01])_',image.stem)
            if not m:continue
            profile=self.t0 if m.group(1)=='0' else self.t1
            try:legacy=detect(image,detector_mode='legacy',**profile)
            except ValueError:legacy=None
            try:dual=detect(image,detector_mode='two_stage',**profile)
            except ValueError:dual=None
            if legacy is not None:
                self.assertIsNotNone(dual,'New rejection on '+str(image))
                self.assertTrue(np.array_equal(legacy,dual),'Changed centre '+str(image))
                unchanged+=1
            elif dual is not None:recovered+=1
        self.assertGreaterEqual(unchanged,25)
        print('FULL REPLAY: legacy unchanged',unchanged,'recovered',recovered)

if __name__=='__main__':unittest.main(verbosity=2)
