"""Offline acceptance checks for sanitized no-write RC16 printer preflight."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_printer_preflight', ROOT / 'scripts/rc16_read_only_preflight.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReadOnlyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        for name in ('printer_data/config', 'auto_xy/klipper', 'klipper/klippy/extras'):
            (self.home / name).mkdir(parents=True)
        self.printer = self.home / 'printer_data/config/printer.cfg'
        self.user = self.home / 'printer_data/config/auto_xy.cfg'
        self.gate = self.home / 'auto_xy/klipper/auto_xy_gate.py'
        self.link = self.home / 'klipper/klippy/extras/auto_xy_gate.py'
        self.printer.write_text('[include auto_xy.cfg]\n# secret serial: abcdef\n')
        self.user.write_text('[auto_xy_gate]\nprotected_print_enabled: False\ncamera_url: http://private.example\n')
        self.gate.write_bytes((ROOT / 'src/auto_xy_gate.py').read_bytes())
        self.link.symlink_to(self.gate)

    def test_detects_valid_exact_rc15_without_exposing_data(self):
        before = sorted(p.relative_to(self.home) for p in self.home.rglob('*'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = module.inspect(self.home, use_network=False)
        after = sorted(p.relative_to(self.home) for p in self.home.rglob('*'))
        self.assertEqual(before, after)
        self.assertEqual(result['rc15_version'], 'EXACT_RC15_DEV6')
        self.assertEqual(result['protected_print_enabled'], 'FALSE')
        self.assertEqual(result['rc16_include'], 'ABSENT')
        self.assertNotIn('abcdef', out.getvalue())
        self.assertNotIn('private.example', out.getvalue())

    def test_user_modified_runtime_is_reported_not_overwritten(self):
        self.gate.write_bytes(b'custom runtime\n')
        result = module.file_summary(self.home)
        self.assertEqual(result['rc15_version'], 'OTHER_OR_MODIFIED')
        self.assertEqual(self.gate.read_bytes(), b'custom runtime\n')

    def test_unexpected_missing_registration_is_reported(self):
        self.link.unlink()
        self.assertEqual(module.file_summary(self.home)['rc15_files'], 'UNEXPECTED_LAYOUT')

    def test_symlinked_user_config_is_never_opened(self):
        outside = self.home / 'private.txt'
        outside.write_text('secret\n')
        self.user.unlink()
        self.user.symlink_to(outside)
        self.assertEqual(module.file_summary(self.home)['rc15_files'], 'MISSING_OR_UNSAFE')

    def test_idle_and_cool_requires_positive_idle_timeout_state(self):
        import json
        from unittest.mock import MagicMock
        sample = {
            'print_stats': {'state': 'complete'},
            'virtual_sdcard': {'is_active': False, 'file_path': ''},
            'idle_timeout': {'state': 'Idle'},
            'gcode_macro _VAOC': {'is_started': False},
            'save_variables': {'variables': {
                'idex_xoffset': 0, 'idex_yoffset': 0, 'idex_zoffset': 0}},
            'heater_bed': {'target': 0, 'temperature': 22},
            'extruder': {'target': 0, 'temperature': 22},
            'extruder1': {'target': 0, 'temperature': 22},
        }
        for idle_state, expected in [('Idle', 'YES'), ('Ready', 'YES'),
                                     ('Printing', 'NO'), (None, 'NO')]:
            with self.subTest(idle_state=idle_state):
                sample['idle_timeout']['state'] = idle_state
                response = MagicMock()
                response.__enter__.return_value = io.BytesIO(
                    json.dumps({'result': {'status': sample}}).encode())
                with patch.object(module, 'urlopen', return_value=response):
                    result = module.runtime_summary()
                self.assertEqual(result['idle_and_cool'], expected)

    def test_vision_accepts_normal_virtualenv_python_symlink(self):
        import sys
        env = self.home / 'auto-xy-venv/bin'
        env.mkdir(parents=True)
        (env / 'python').symlink_to(Path(sys.executable).resolve())
        fake = type('Completed', (), {'returncode': 0,
                 'stdout': '1.26.4/4.8.0\n'})()
        with patch.object(module.subprocess, 'run', return_value=fake) as run:
            self.assertEqual(module.vision_summary(self.home), '1.26.4/4.8.0')
            self.assertEqual(run.call_args.args[0][0], str(env / 'python'))

    def test_vision_environment_missing_is_a_status_not_a_crash(self):
        self.assertEqual(module.vision_summary(self.home), 'NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
