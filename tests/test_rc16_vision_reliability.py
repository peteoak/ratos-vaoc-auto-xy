"""RC16 geometry-first photo tie-break, transient camera retry and scale recheck."""
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def module(alias, path):
    spec = importlib.util.spec_from_file_location(alias, ROOT/path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


signatures = module('rc16_test_signatures','src/auto_xy_reference_signature_rc16.py')
vision = module('rc16_test_vision','src/auto_xy_vision_worker_rc16.py')
detector = module('rc16_test_detector','src/auto_xy_detector.py')
gate = module('rc16_test_gate','src/auto_xy_gate_rc16.py')


class Camera:
    def __init__(self, content):
        self.content = content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, count):
        return self.content


class ImagePolicyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        image = np.full((900,1200), 80, dtype=np.uint8)
        cv2.circle(image, (600,440), 146, 210, -1)
        cv2.circle(image, (600,440), 51, 60, -1)
        self.path = self.root/'sample.jpg'
        cv2.imwrite(str(self.path), image)
        ok, buf = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        self.jpeg = buf.tobytes()

    def args(self):
        class Args:
            url='http://example.invalid/snapshot'
            image=''
            roi='auto'
            timeout=3.
            detector_mode='two_stage_roi'
            expected_center='600,440'
            expected_radius=146.
            registration_anchor=None
            registration_anchor_center=None
            expected_signature='{"format":"radial-v1","values":[0,0,0,0,0,0]}'
        a=Args()
        a.image=str(self.root/'capture.jpg')
        return a

    def test_unique_geometric_circle_ignores_bad_reference_signature(self):
        a=self.args()
        with patch.object(vision,'urlopen',return_value=Camera(self.jpeg)):
            with patch.object(vision,'detect',return_value=np.array([600.,440.,146.])) as detect:
                with patch.object(vision,'select_ambiguous_pair',
                                  side_effect=AssertionError('photo must not run')):
                    result=vision.capture(a)
        self.assertEqual(result['center_px'],[600.,440.])
        self.assertFalse(result['appearance_evidence']['used_for_selection'])
        self.assertEqual(result['appearance_evidence']['photo_weight'],0.)
        self.assertIn('ambiguous_selector',detect.call_args.kwargs)

    def test_camera_http_500_retry_uses_fresh_request_each_attempt(self):
        a=self.args()
        server_error=lambda: HTTPError(a.url, 500, 'Server Error', {}, io.BytesIO())
        with patch.object(vision,'urlopen',
                          side_effect=[server_error(),server_error(),Camera(self.jpeg)]) as fetching:
            with patch.object(vision,'detect',return_value=np.array([600.,440.,146.])):
                result=vision.capture(a)
        self.assertEqual(fetching.call_count,3)
        self.assertEqual(result['camera_http_retries'],2)
        self.assertTrue((self.root/'capture.jpg').is_file())

    def test_camera_http_500_three_failures_stops(self):
        a=self.args()
        with patch.object(vision,'urlopen',side_effect=HTTPError(
                a.url,500,'Server Error',{},io.BytesIO())) as fetching:
            with self.assertRaises(HTTPError):
                vision.capture(a)
        self.assertEqual(fetching.call_count,3)
        self.assertFalse((self.root/'capture.jpg').exists())

    def test_unique_detector_ignores_photographic_selector(self):
        image=np.zeros((540,540),np.uint8)
        with patch.object(detector.cv2,'HoughCircles',
                          return_value=np.array([[[270.,270.,146.]]],np.float32)):
            result=detector._hough(image,0,0,(115,185),
                 expected_center=np.array([270.,270.]),expected_radius=146.,
                 coarse_center=np.array([270.,270.]),
                 ambiguous_selector=lambda *args: (_ for _ in ()).throw(
                     AssertionError('Photo selector not allowed')))
        self.assertAlmostEqual(float(result[0]),270.)

    def test_two_candidate_photo_selection_requires_geometry(self):
        image=np.zeros((540,540),np.uint8)
        candidates=np.array([[[270.,270.,146.],[281.,271.,148.]]],np.float32)
        called=[]
        with patch.object(detector.cv2,'HoughCircles',return_value=candidates):
            result=detector._hough(image,0,0,(115,185),
                expected_center=np.array([270.,270.]),expected_radius=146.,
                coarse_center=np.array([273.,271.]),
                ambiguous_selector=lambda circles,*args:(called.append(len(circles)) or 0))
        self.assertEqual(called,[2])
        self.assertAlmostEqual(float(result[0]),270.)

    def test_two_candidate_likeness_is_25_percent_not_a_veto(self):
        circles=[[600.,440.,146.],[609.,441.,146.]]
        with patch.object(signatures,'rim_likeness_score',side_effect=[.5,.1]):
            chosen, evidence=signatures.select_ambiguous_pair(
                self.path,circles,{},[601.,440.],[600.,440.],146.)
        self.assertEqual(chosen,0)
        self.assertEqual(evidence['photo_weight'],.25)

    def test_inconclusive_photo_pair_refuses(self):
        circles=[[600.,440.,146.],[609.,441.,146.]]
        with patch.object(signatures,'rim_likeness_score',side_effect=[.3,.29]):
            with self.assertRaisesRegex(ValueError,'inconclusive'):
                signatures.select_ambiguous_pair(
                    self.path,circles,{},[601.,440.],[600.,440.],146.)


class ScaleRecheckTests(unittest.TestCase):
    def setUp(self):
        obj=gate.AutoXYGate.__new__(gate.AutoXYGate)
        obj.tol=.03
        obj.report={}
        obj._scale_axis_baselines={
            0:{'y':{'zero':[945.,540.]}},
            1:{'y':{'zero':[963.,541.]}}
        }
        obj.record=lambda kind,**kw: obj.report.setdefault(kind,[]).append(kw)
        obj.samples=[]
        def capture(tool,axis,label_prefix='cal'):
            obj.samples.append((tool,axis,label_prefix))
            # Both tools converge on a stable -296 px/mm X scale.
            slope=[-296., 0.] if tool==0 else [-297.,0.]
            zero=obj._scale_axis_baselines[tool]['y']['zero']
            return {'slope':slope,'zero':zero,'residual':[[0.,0.]]}
        obj._capture_scale_axis=capture
        self.obj=obj
        self.m0=[[-262.5,6.25],[-6.25,-296.875]]
        self.m1=[[-296.875,3.125],[3.125,-293.75]]

    def test_only_suspect_axis_is_recaptured_and_fused(self):
        initial=self.obj._scale_disagreement(self.m0,self.m1)
        self.assertGreater(initial[0],.12)
        self.assertLess(initial[1],.12)
        zero,one,after=self.obj._targeted_scale_recheck((self.m0,self.m1),initial)
        self.assertEqual(len(self.obj.samples),4)
        self.assertTrue(all(axis=='x' for _,axis,_ in self.obj.samples))
        self.assertAlmostEqual(zero[0][0],-296.)
        self.assertEqual(self.m0[0][0],-262.5)
        self.assertLess(max(after),.12)

    def test_disagreeing_repeat_sweeps_fail_without_offset_write(self):
        index=[0]
        def variable_capture(tool,axis,label_prefix='cal'):
            index[0]+=1
            slope=-260. if index[0]%2 else -300.
            return {'slope':[slope,0.],
                    'zero':self.obj._scale_axis_baselines[tool]['y']['zero'],
                    'residual':[[0.,0.]]}
        self.obj._capture_scale_axis=variable_capture
        with self.assertRaisesRegex(gate.GateAbort,'not repeatable'):
            self.obj._targeted_scale_recheck(
                (self.m0,self.m1),self.obj._scale_disagreement(self.m0,self.m1))


if __name__ == '__main__':
    unittest.main()
