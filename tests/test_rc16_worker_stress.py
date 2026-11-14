"""RC16 G-code worker stress and non-blocking safety acceptance (offline only)."""
import hashlib
import importlib.util
import os
import tempfile
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_worker_stress', ROOT / 'src/tools/rc16_prepare_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
HEADER = b'; processed by RatOS.PostProcessor 1.0.0 test on machine idex\n'
META = b'; ratos_meta begin 1\n; verified fixture\n; ratos_meta end 1\n'
START = (b'START_PRINT EXTRUDER_TEMP=210,210 EXTRUDER_OTHER_LAYER_TEMP=200,200 '
         b'BED_TEMP=60 TOTAL_TOOLSHIFTS=5 INITIAL_TOOL=0\n')


class WorkerStressTests(unittest.TestCase):
    def test_streams_multi_megabyte_without_memory_scaling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'original.gcode'
            with source.open('wb') as output:
                output.write(START)
                for i in range(160000):
                    output.write(b'G1 X124.625 Y125.275 E0.00374 F10000\n')
            self.assertGreater(source.stat().st_size, 5_000_000)
            tracemalloc.start()
            try:
                result = worker.stage(root, 'original.gcode')
                stage = root / result['stage_name']
                self.assertEqual(result['bytes_read'], source.stat().st_size)
                # Fake RatOS processing while preserving its own subprocess API
                # in the coordinator tests: only prepend known fixture metadata.
                tmp = root / 'ratostmp'
                with tmp.open('wb') as stream:
                    stream.write(HEADER + META)
                    with stage.open('rb') as reader:
                        for chunk in iter(lambda: reader.read(128*1024), b''):
                            stream.write(chunk)
                tmp.replace(stage)
                prepared = worker.prepare(root, stage.name, 'independent')
                verified = worker.verify(root, stage.name, 'independent',
                                         prepared['output_sha256'])
                self.assertTrue(prepared['protected'])
                self.assertTrue(verified['protected'])
                self.assertEqual(verified['sha256'], prepared['output_sha256'])
            finally:
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            self.assertLess(peak, 8 * 1024 * 1024,
                            'Worker allocations grew unexpectedly with G-code size')
            self.assertTrue(source.read_bytes().startswith(START))

    def test_stage_rejects_symlinked_origin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'true.gcode').write_bytes(START)
            (root / 'alias.gcode').symlink_to(root / 'true.gcode')
            with self.assertRaises(worker.Refuse):
                worker.stage(root, 'alias.gcode')

    def test_stage_rejects_unsafe_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(worker.Refuse):
                worker.stage(td, '../outside.gcode')

    def test_preprocessed_header_is_not_double_processed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'preprocessed.gcode').write_bytes(HEADER + META + START)
            staged = worker.stage(root, 'preprocessed.gcode')
            self.assertTrue(staged['already_ratos_processed'])

    def test_malformed_ratos_header_is_refused_not_reprocessed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'unsupported.gcode').write_bytes(
                b'; processed by RatOS.PostProcessor 2.0.0 test idex\n' + START)
            with self.assertRaises(worker.Refuse):
                worker.stage(root, 'unsupported.gcode')


if __name__ == '__main__':
    unittest.main()
