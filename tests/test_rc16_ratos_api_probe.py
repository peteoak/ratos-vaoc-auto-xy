"""Offline tests for the source-only, read-only RatOS API probe."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_api_probe', ROOT / 'scripts/rc16_ratos_api_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class APIProbeTests(unittest.TestCase):
    def test_extracts_public_method_and_command_literals(self):
        source = """
class VirtualSD:
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
    def do_resume(self):
        pass
"""
        shape = probe.inspect_source(source)
        self.assertTrue(shape['functions']['cmd_SDCARD_PRINT_FILE'])
        self.assertTrue(shape['functions']['do_resume'])
        self.assertTrue(shape['command_literals']['SDCARD_PRINT_FILE_BASE'])
        self.assertFalse(shape['command_literals']['PROCESS_GCODE_FILE'])

    def test_does_not_expose_file_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'ratos.py'
            source.write_text('password = "secret_not_for_output"\n'
                              'def process_gcode_file(): pass\n')
            result = probe.inspect_file(source)
            self.assertEqual(result['status'], 'PARSED')
            self.assertNotIn('secret_not_for_output', str(result))
            self.assertTrue(result['functions']['process_gcode_file'])

    def test_missing_file_is_reported_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / 'missing.py'
            self.assertEqual(probe.inspect_file(location)['status'], 'NOT_FOUND')
            self.assertFalse(location.exists())


if __name__ == '__main__':
    unittest.main()
