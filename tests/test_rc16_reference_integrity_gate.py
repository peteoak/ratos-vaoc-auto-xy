"""RC16 current photo evidence is immutable across calibration and storage cleanup."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location(
    'rc16_reference_gate',ROOT/'src/auto_xy_gate_rc16.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ActiveReferenceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'nozzle_references'
        self.root.mkdir()
        self.session=self.root/'session-abc123ab'
        self.session.mkdir()
        self.gate=mod.AutoXYGate.__new__(mod.AutoXYGate)
        self.gate.nozzle_reference_root=str(self.root)
        self.profile={'reference_schema':'rc16-full-frame-raw-jpeg-v1'}
        for tool in (0,1):
            refs=[]
            for index in range(3):
                path=self.session/('t%d_abcabcabcabc_%d.jpg'%(tool,index+1))
                path.write_bytes(('raw photo %d-%d'%(tool,index)).encode())
                refs.append({'path':str(path),
                             'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            self.profile['tool%d'%tool]={'reference_images':refs}

    def test_six_reference_files_validated_without_mutation(self):
        old={p:p.read_bytes() for p in self.session.iterdir()}
        self.gate._verify_nozzle_reference_library(self.profile)
        self.assertEqual({p:p.read_bytes() for p in self.session.iterdir()},old)

    def test_missing_or_modified_reference_refuses_calibration(self):
        first=Path(self.profile['tool0']['reference_images'][0]['path'])
        first.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.gate._verify_nozzle_reference_library(self.profile)
        first.unlink()
        with self.assertRaises(OSError):
            self.gate._verify_nozzle_reference_library(self.profile)

    def test_symlink_and_duplicate_references_refused(self):
        first=Path(self.profile['tool0']['reference_images'][0]['path'])
        link=self.session/'t0_abcabcabcabc_1-link.jpg'
        link.symlink_to(first)
        self.profile['tool0']['reference_images'][0]['path']=str(link)
        with self.assertRaisesRegex(ValueError,'Unsafe'):
            self.gate._verify_nozzle_reference_library(self.profile)
        self.profile['tool0']['reference_images'][0]['path']=str(first)
        self.profile['tool1']['reference_images'][0]=dict(
            self.profile['tool0']['reference_images'][0])
        with self.assertRaisesRegex(ValueError,'duplicated'):
            self.gate._verify_nozzle_reference_library(self.profile)

    def test_photo_matching_requires_uncropped_full_camera_frame(self):
        source = (ROOT / 'src/auto_xy_gate_rc16.py').read_text()
        self.assertIn("str(self.roi).strip().lower()!='auto'", source)
        self.assertIn('photographic reference requires full camera frame', source)

    def test_legacy_profile_without_reference_schema_stays_compatible(self):
        legacy={'tool0':{'radius_px':140.},'tool1':{'radius_px':145.}}
        self.gate._verify_nozzle_reference_library(legacy)


if __name__=='__main__':
    unittest.main()
