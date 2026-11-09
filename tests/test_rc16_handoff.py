"""RC16 worker + Klipper coordinator offline end-to-end contract tests.

Runs a real separate Python worker, but uses a simulated RatOS transformer,
virtual SD and reactor. No motors, camera or printer side effects.
"""
import importlib.util
import hashlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'src/tools/rc16_prepare_worker.py'
COORDINATOR = ROOT / 'src/auto_xy_ratos_handoff_rc16.py'
spec = importlib.util.spec_from_file_location('rc16_handoff', COORDINATOR)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

HEADER = b'; processed by RatOS.PostProcessor 1.0.0 on 2026-11-09 at 09:00 UTC idex\n'
META = b'; ratos_meta begin 3\n; reviewed fixture\n; ratos_meta end 3\n'
START = (b'START_PRINT EXTRUDER_TEMP=210,205 EXTRUDER_OTHER_LAYER_TEMP=200,195 '
         b'BED_TEMP=60 TOTAL_TOOLSHIFTS=5 INITIAL_TOOL=0\n')
SINGLE = START.replace(b'TOTAL_TOOLSHIFTS=5', b'TOTAL_TOOLSHIFTS=0')


class Reactor:
    def monotonic(self):
        return time.monotonic()
    def pause(self, until):
        time.sleep(min(0.01, max(0, until - time.monotonic())))
        return time.monotonic()


class Gcmd:
    def __init__(self, filename='part.gcode'):
        self.filename = filename
        self.messages = []
    def get(self, k, default=None, *args, **kwargs):
        return self.filename if k == 'FILENAME' else default
    def error(self, msg):
        return RuntimeError(msg)
    def respond_info(self, msg):
        self.messages.append(msg)


class VSD:
    def __init__(self):
        self.started = []
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        self.started.append(gcmd.get('FILENAME'))


class DC:
    mode = 'primary'
    def get_status(self, timestamp):
        return {'carriage_1': self.mode}


class RatOS:
    bypass_post_processing = False
    enable_gcode_transform = True
    def __init__(self, root, mode='primary'):
        self.sdcard_dirname = str(root)
        self.v_sd = VSD()
        self.dual_carriage = DC()
        self.dual_carriage.mode = mode
        self.processed = []
        self.fail = False
    def process_gcode_file(self, name, transform):
        self.processed.append(name)
        if self.fail:
            return False
        path = Path(self.sdcard_dirname) / name
        original = path.read_bytes()
        if original.startswith(b'; processed by RatOS.PostProcessor'):
            raise AssertionError('Accidental double-processing')
        path.write_bytes(HEADER + META + original)
        return True


class Gate:
    busy = False
    protected_print_enabled = True
    def enabled(self):
        return True
    def flag(self, key):
        return False


class Gcode:
    def __init__(self):
        self.handlers = {'PROCESS_GCODE_FILE': lambda gcmd: None, 'SDCARD_PRINT_FILE_BASE': lambda gcmd: None}
        self.commands = []
    def register_command(self, name, value):
        if value is None:
            return self.handlers.pop(name, None)
        self.handlers[name] = value
    def run_script_from_command(self, command):
        self.commands.append(command)


class Config:
    def __init__(self, printer, enabled=True):
        self.printer = printer
        self.enabled = enabled
    def get_printer(self):
        return self.printer
    def getboolean(self, name, default=False):
        return self.enabled
    def get(self, name, default=None):
        return default


class Printer:
    def __init__(self, root, mode='primary'):
        self.ratos = RatOS(root, mode)
        self.gate = Gate()
        self.gcode = Gcode()
        self.reactor = Reactor()
        self.handlers = {}
    def lookup_object(self, name, default=None):
        return {'ratos': self.ratos, 'auto_xy_gate': self.gate,
                'gcode': self.gcode}.get(name, default)
    def get_reactor(self):
        return self.reactor
    def register_event_handler(self, name, cb):
        self.handlers[name] = cb
    def config_error(self, msg):
        return RuntimeError(msg)


class RC16HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'part.gcode'
        self.source.write_bytes(START + b'G1 X10 Y20\n' * 1000)
        self.printer = Printer(self.root)
        self.obj = mod.AutoXYRC16Handoff(Config(self.printer))
        self.obj.worker = WORKER
        self.obj.worker_python = sys.executable
        self.obj.registry = self.root / 'registry'
        self.printer.handlers['klippy:ready']()
        self.assertTrue(self.obj.installed)

    def test_independent_two_tool_is_protected_after_ratos(self):
        before = self.source.read_bytes()
        self.obj.cmd_process(Gcmd())
        sd = self.printer.ratos.v_sd.started
        self.assertEqual(len(sd), 1)
        payload = (self.root / sd[0]).read_bytes()
        self.assertIn(b'AUTOXY_PRINT_CALIBRATE', payload)
        self.assertIn(b'AUTOXY_PRINT_CHECK', payload)
        self.assertNotIn(b'\nSTART_PRINT ', payload)
        self.assertEqual(len(self.printer.ratos.processed), 1)
        self.assertEqual(self.source.read_bytes(), before)

    def test_single_tool_keeps_native_ratos_start(self):
        self.source.write_bytes(SINGLE + b'G1 X10\n')
        self.obj.cmd_process(Gcmd())
        sd = self.printer.ratos.v_sd.started
        self.assertEqual(len(sd), 1)
        payload = (self.root / sd[0]).read_bytes()
        self.assertIn(b'\nSTART_PRINT ', payload)
        self.assertNotIn(b'AUTOXY_PRINT_CALIBRATE', payload)

    def test_copy_and_mirror_keep_native_start(self):
        for mode in ('copy', 'mirror'):
            self.printer.ratos.dual_carriage.mode = mode
            self.source.write_bytes(SINGLE + b'G1 X10\n')
            self.obj.cmd_process(Gcmd())
            name = self.printer.ratos.v_sd.started[-1]
            payload = (self.root / name).read_bytes()
            self.assertIn(b'\nSTART_PRINT ', payload)
            self.assertNotIn(b'AUTOXY_PRINT_CALIBRATE', payload)

    def test_copy_with_toolshift_refuses_to_print(self):
        self.printer.ratos.dual_carriage.mode = 'copy'
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_unrecognised_mode_refuses(self):
        self.printer.ratos.dual_carriage.mode = 'bad'
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_ratos_failure_refuses(self):
        self.printer.ratos.fail = True
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_preprocessed_file_skips_double_ratos_processing(self):
        original = HEADER + META + START + b'G1 X10\n'
        self.source.write_bytes(original)
        self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.processed, [])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertIn(b'AUTOXY_PRINT_CALIBRATE',
                      (self.root / self.printer.ratos.v_sd.started[0]).read_bytes())

    def test_generated_file_reprint_reprocesses_original(self):
        before = self.source.read_bytes()
        self.obj.cmd_process(Gcmd())
        previous = self.printer.ratos.v_sd.started[-1]
        self.obj.cmd_process(Gcmd(previous))
        current = self.printer.ratos.v_sd.started[-1]
        self.assertNotEqual(previous, current)
        self.assertEqual(len(self.printer.ratos.processed), 2)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIn(b'AUTOXY_PRINT_CALIBRATE',
                      (self.root / current).read_bytes())

    def test_generated_file_changed_reprint_refused(self):
        self.obj.cmd_process(Gcmd())
        name = self.printer.ratos.v_sd.started[-1]
        with open(self.root / name, 'ab') as stream:
            stream.write(b'; tampered\n')
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd(name))
        self.assertEqual(len(self.printer.ratos.v_sd.started), 1)

    def test_original_modified_before_reprint_uses_new_source(self):
        self.obj.cmd_process(Gcmd())
        name = self.printer.ratos.v_sd.started[-1]
        self.source.write_bytes(START.replace(b'BED_TEMP=60', b'BED_TEMP=65') + b'G1 X77\n')
        self.obj.cmd_process(Gcmd(name))
        new = self.printer.ratos.v_sd.started[-1]
        self.assertIn(b'BED_TEMP=65', (self.root / new).read_bytes())

    def test_direct_base_is_guarded_without_interfering_with_normal_start(self):
        self.assertIn('SDCARD_PRINT_FILE_BASE', self.printer.gcode.handlers)
        with self.assertRaisesRegex(RuntimeError, 'normal SDCARD_PRINT_FILE'):
            self.printer.gcode.handlers['SDCARD_PRINT_FILE_BASE'](Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])
        self.obj.cmd_process(Gcmd())
        self.assertEqual(len(self.printer.ratos.v_sd.started), 1)

    def test_bypass_or_transform_disabled_refuses(self):
        for key in ('bypass_post_processing', 'enable_gcode_transform'):
            setattr(self.printer.ratos, key, key == 'bypass_post_processing')
            with self.assertRaises(RuntimeError):
                self.obj.cmd_process(Gcmd())
            setattr(self.printer.ratos, key, key != 'bypass_post_processing')
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_file_change_detected_after_last_check(self):
        original_wait = self.obj._wait_worker
        def injected_wait(action, root, filename, mode=None, expected_hash=None, original=None, source_hash=None):
            result = original_wait(action, root, filename, mode, expected_hash, original, source_hash)
            if action == 'verify':
                with open(Path(root) / filename, 'ab') as out:
                    out.write(b'; tampered\n')
            return result
        self.obj._wait_worker = injected_wait
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_worker_failure_never_starts_print(self):
        self.source.write_bytes(b'START_PRINT\n'+b'X'*80000)
        with self.assertRaises(RuntimeError):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_disarmed_coordinator_does_not_intercept_ratos(self):
        printer = Printer(self.root)
        obj = mod.AutoXYRC16Handoff(Config(printer, enabled=False))
        original = printer.gcode.handlers['PROCESS_GCODE_FILE']
        printer.handlers['klippy:ready']()
        self.assertIs(printer.gcode.handlers['PROCESS_GCODE_FILE'], original)
        self.assertFalse(obj.installed)


if __name__ == '__main__':
    unittest.main()
