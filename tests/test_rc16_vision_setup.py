"""Offline tests for RC16 vision environment safeguards.

No network calls, no actual pip installation, no printer access.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vision_env_rc16', ROOT / 'scripts/vision_env.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class VisionSetupTests(unittest.TestCase):
    def test_trial_pins_are_abi_compatible_pair(self):
        self.assertEqual(setup.requirement_versions(),
                         {'numpy': '1.26.4', 'opencv-python-headless': '4.11.0.86'})

    def test_refuse_unreviewed_or_unpinned_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'requirements.txt'
            for bad in ('numpy>=1.24,<3\nopencv-python-headless==4.11.0.86\n',
                        'numpy==2.0.2\nopencv-python-headless==4.11.0.86\n',
                        'numpy==1.26.4\nnumpy==1.26.4\n'):
                p.write_text(bad)
                with self.assertRaises(setup.VisionSetupError):
                    setup.requirement_versions(p)

    def test_host_rejects_unsupported_arch_and_python(self):
        for kw in ({'version': (3, 8), 'machine': 'aarch64', 'system': 'Linux'},
                   {'version': (3, 11), 'machine': 'i686', 'system': 'Linux'},
                   {'version': (3, 11), 'machine': 'aarch64', 'system': 'Windows'}):
            with self.assertRaises(setup.VisionSetupError):
                setup.host_check(**kw)

    def test_host_accepts_supported_candidate_matrix(self):
        for py in ((3, 9), (3, 10), (3, 11), (3, 12)):
            for arch in ('aarch64', 'armv7l', 'x86_64'):
                self.assertIn('architecture', setup.host_check(py, arch, 'Linux'))

    def test_refuses_existing_venv_before_subprocess_or_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / 'auto-xy-venv'
            venv.mkdir()
            with patch.object(setup, 'host_check', return_value={}):
                with patch.object(setup, 'run_checked') as execute:
                    with self.assertRaisesRegex(setup.VisionSetupError, 'Existing vision venv'):
                        setup.install_new_venv(venv)
                    execute.assert_not_called()
            self.assertTrue(venv.exists())

    def test_smoke_checks_versions_not_just_successful_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / 'venv'
            (venv / 'bin').mkdir(parents=True)
            (venv / 'pyvenv.cfg').write_text('home = /usr/bin\n')
            (venv / 'bin/python').write_text('')
            with patch.object(setup, 'run_checked', return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps({'numpy': '2.0.2',
                                       'opencv-python-headless': '4.11.0.86'}), '')):
                with self.assertRaisesRegex(setup.VisionSetupError, 'mismatch'):
                    setup.verify_vision_env(venv)

    def test_smoke_accepts_exact_compatible_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / 'venv'
            (venv / 'bin').mkdir(parents=True)
            (venv / 'pyvenv.cfg').write_text('home = /usr/bin\n')
            (venv / 'bin/python').write_text('')
            with patch.object(setup, 'run_checked', return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(setup.EXPECTED), '')):
                self.assertEqual(setup.verify_vision_env(venv), setup.EXPECTED)


if __name__ == '__main__':
    unittest.main()
