"""RC16 offline read-only G-code scanner regression tests."""
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rc16_scan',
    ROOT / 'src/tools/rc16_streaming_inspect.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class StreamingInspectorTests(unittest.TestCase):
    def test_streams_large_file_without_transforming(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = (b'; slicer header\n'
                    b'START_PRINT BED_TEMP=60 INITIAL_TOOL=0 TOTAL_TOOLSHIFTS=3\n'
                    + b'G1 X123.45 Y67.89 E0.015\n' * 25000)
            path = root / 'part.gcode'
            path.write_bytes(data)
            result = worker.inspect(root, 'part.gcode', chunk_size=4096)
            self.assertEqual(result['status'], 'INSPECTED_NOT_AUTHORIZED')
            self.assertEqual(result['start_print_count'], 1)
            self.assertEqual(result['start_print_params']['TOTAL_TOOLSHIFTS'], '3')
            self.assertEqual(result['bytes_read'], len(data))
            self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(path.read_bytes(), data)

    def test_multiple_starts_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b'START_PRINT BED_TEMP=60\nSTART_PRINT BED_TEMP=70\n')
            with self.assertRaisesRegex(worker.InspectError, 'Multiple START_PRINT'):
                worker.inspect(td, 'p.gcode')

    def test_traversal_and_absolute_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b'START_PRINT\n')
            for bad in ('../p.gcode', '/tmp/f.gcode', 'p.txt'):
                with self.assertRaises(worker.InspectError):
                    worker.source_path(td, bad)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            (path / 'p.gcode').write_bytes(b'START_PRINT\n')
            (path / 'sym.gcode').symlink_to(path / 'p.gcode')
            with self.assertRaisesRegex(worker.InspectError, 'Symlinked'):
                worker.inspect(path, 'sym.gcode')
            (path / 'folder').mkdir()
            (path / 'alias').symlink_to(path / 'folder')
            with self.assertRaisesRegex(worker.InspectError, 'Symlinked'):
                worker.source_path(path, 'alias/p.gcode')

    def test_oversized_line_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b';' + b'X' * 70000 + b'\n')
            with self.assertRaisesRegex(worker.InspectError, 'line exceeds'):
                worker.inspect(td, 'p.gcode')

    def test_duplicate_start_param_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b'START_PRINT BED_TEMP=60 BED_TEMP=65\n')
            with self.assertRaisesRegex(worker.InspectError, 'Duplicate'):
                worker.inspect(td, 'p.gcode')

    def test_empty_gcode_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b'')
            with self.assertRaisesRegex(worker.InspectError, 'Empty'):
                worker.inspect(td, 'p.gcode')

    def test_missing_start_does_not_authorise_print(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.gcode').write_bytes(b';START_PRINT\nG1 X5\n')
            result = worker.inspect(td, 'p.gcode')
            self.assertEqual(result['start_print_count'], 0)
            self.assertEqual(result['status'], 'INSPECTED_NOT_AUTHORIZED')


if __name__ == '__main__':
    unittest.main()
