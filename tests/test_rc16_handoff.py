"""RC16 worker + Klipper coordinator offline end-to-end contract tests.

Runs a real separate Python worker, but uses a simulated RatOS transformer,
virtual SD and reactor. No motors, camera or printer side effects.
"""
import importlib.util
import fcntl
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
    NEVER = float('inf')
    def register_timer(self, callback, when):
        return (callback, when)
    def update_timer(self, timer, when):
        return None
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
        self.file_path = None
        self.is_active = False
        self.resume_calls = 0
    def do_resume(self):
        self.resume_calls += 1
    def get_status(self, now):
        return {'is_active': self.is_active, 'file_path': self.file_path}
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        self.file_path = gcmd.get('FILENAME')
        self.is_active = True
        self.started.append(gcmd.get('FILENAME'))
    def finish(self):
        self.is_active = False
        self.file_path = None


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
        if (self.dual_carriage.mode in ('copy', 'mirror')
                and b'TOTAL_TOOLSHIFTS=5' in original):
            return False
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
        self.handlers = {'PROCESS_GCODE_FILE': lambda gcmd: None, 'SDCARD_PRINT_FILE_BASE': lambda gcmd: None, 'M23': lambda gcmd: None, 'M26': lambda gcmd: None, 'M24': lambda gcmd: None}
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
        if name == 'retention_enabled':
            return getattr(self, 'retention_enabled', False)
        return self.enabled
    def getint(self, name, default=None, **kwargs):
        return default
    def get(self, name, default=None):
        return default


class Printer:
    def __init__(self, root, mode='primary'):
        self.ratos = RatOS(root, mode)
        self.gate = Gate()
        self.gcode = Gcode()
        self.gcode.handlers['PROCESS_GCODE_FILE'] = self.native_process
        self.reactor = Reactor()
        self.handlers = {}
    def native_process(self, gcmd):
        name = gcmd.get('FILENAME')
        if self.ratos.process_gcode_file(name, self.ratos.enable_gcode_transform):
            return self.ratos.v_sd.cmd_SDCARD_PRINT_FILE(gcmd)

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

    def test_copy_and_mirror_use_unmodified_native_ratos_path(self):
        for mode in ('copy', 'mirror'):
            self.printer.ratos.dual_carriage.mode = mode
            self.source.write_bytes(SINGLE + b'G1 X10\n')
            before = set(self.root.glob('__AutoXY_RC16_*.gcode'))
            self.obj._wait_worker = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError('COPY/MIRROR must never invoke AutoXY worker'))
            self.obj.cmd_process(Gcmd())
            name = self.printer.ratos.v_sd.started[-1]
            self.assertEqual(name, 'part.gcode')
            self.assertEqual(before, set(self.root.glob('__AutoXY_RC16_*.gcode')))
            payload = (self.root / name).read_bytes()
            self.assertIn(b'\nSTART_PRINT ', payload)
            self.assertNotIn(b'AUTOXY_PRINT_CALIBRATE', payload)

    def test_copy_mirror_cannot_reprint_generated_protected_file(self):
        self.obj.cmd_process(Gcmd())
        generated = self.printer.ratos.v_sd.started[-1]
        self.printer.ratos.v_sd.finish()
        for mode in ('copy', 'mirror'):
            self.printer.ratos.dual_carriage.mode = mode
            with self.assertRaisesRegex(RuntimeError, 'Generated RC16 file'):
                self.obj.cmd_process(Gcmd(generated))
        self.assertEqual(len(self.printer.ratos.v_sd.started), 1)

    def test_copy_with_toolshift_refuses_in_native_ratos(self):
        self.printer.ratos.dual_carriage.mode = 'copy'
        # RatOS returns False and does not open virtual SD; no need for
        # the AutoXY handoff to reinterpret its native rejection.
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
        self.printer.ratos.v_sd.finish()
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
        self.printer.ratos.v_sd.finish()
        self.obj.cmd_process(Gcmd(name))
        new = self.printer.ratos.v_sd.started[-1]
        self.assertIn(b'BED_TEMP=65', (self.root / new).read_bytes())

    def test_generated_file_lock_blocks_cleanup_during_print_and_pause(self):
        self.obj.cmd_process(Gcmd())
        self.assertIsNotNone(self.obj._print_lock_fd)
        lock_path = self.obj.registry / mod.RETENTION_LOCK_NAME
        with lock_path.open('rb') as file:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.printer.ratos.v_sd.is_active = False
        self.obj._print_lock_tick(time.monotonic())
        self.assertIsNotNone(self.obj._print_lock_fd)
        self.printer.ratos.v_sd.finish()
        self.obj._print_lock_tick(time.monotonic())
        self.assertIsNone(self.obj._print_lock_fd)
        with lock_path.open('rb') as file:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    def test_second_print_refused_while_selected_file_owns_lock(self):
        self.obj.cmd_process(Gcmd())
        with self.assertRaisesRegex(RuntimeError, 'previous print file remains selected'):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(len(self.printer.ratos.v_sd.started), 1)

    def test_generated_file_lock_blocks_cleanup_during_print_and_pause(self):
        self.obj.cmd_process(Gcmd())
        self.assertIsNotNone(self.obj._print_lock_fd)
        lock_path = self.obj.registry / mod.RETENTION_LOCK_NAME
        with lock_path.open('rb') as file:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.printer.ratos.v_sd.is_active = False
        self.obj._print_lock_tick(time.monotonic())
        self.assertIsNotNone(self.obj._print_lock_fd)
        self.printer.ratos.v_sd.finish()
        self.obj._print_lock_tick(time.monotonic())
        self.assertIsNone(self.obj._print_lock_fd)

    def test_retention_timer_spawns_only_when_idle_and_never_waits(self):
        from unittest.mock import patch
        printer = Printer(self.root)
        config = Config(printer)
        config.retention_enabled = True
        controller = mod.AutoXYRC16Handoff(config)
        controller.worker = WORKER
        retain = self.root / 'rc16_retention.py'
        retain.write_text('pass\n')
        controller.retention_worker = retain
        controller.registry = self.root / 'registry'
        controller.registry.mkdir(exist_ok=True)
        scheduled = []
        printer.reactor.register_timer = lambda cb, event: scheduled.append((cb, event))
        class Stats:
            def get_status(self, now):
                return {'state': 'standby'}
        class SD:
            def get_status(self, now):
                return {'is_active': False, 'file_path': None}
        original_lookup = printer.lookup_object
        def lookup(name, default=None):
            return {'print_stats': Stats(), 'virtual_sdcard': SD()}.get(
                name, original_lookup(name, default))
        printer.lookup_object = lookup
        printer.handlers['klippy:ready']()
        self.assertEqual(len(scheduled), 1)
        with patch.object(mod.subprocess, 'Popen') as popen:
            controller._retention_tick(printer.reactor.monotonic() + 125.)
            self.assertTrue(popen.called)
            command = popen.call_args.args[0]
            self.assertIn('--auto', command)
            self.assertIn('--days', command)
            self.assertIn('30', command)

    def test_retention_never_starts_while_active_print_or_preparing(self):
        printer = Printer(self.root)
        controller = mod.AutoXYRC16Handoff(Config(printer))
        controller.registry = self.root / 'registry'
        controller.registry.mkdir(exist_ok=True)
        class Stats:
            def get_status(self, now):
                return {'state': 'printing'}
        class SD:
            def get_status(self, now):
                return {'is_active': True, 'file_path': 'active.gcode'}
        original_lookup = printer.lookup_object
        printer.lookup_object = lambda n, default=None: {
            'print_stats': Stats(), 'virtual_sdcard': SD()
        }.get(n, original_lookup(n, default))
        self.assertFalse(controller._retention_safe_to_start())
        controller.busy = True
        self.assertFalse(controller._retention_safe_to_start())

    def test_runtime_replacement_of_resume_guard_disarms_print_start(self):
        self.printer.ratos.v_sd.do_resume = lambda: None
        self.assertFalse(self.obj.get_status(time.monotonic())['resume_api_guarded'])
        with self.assertRaisesRegex(RuntimeError, 'resume guard was replaced'):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_runtime_replacement_of_sd_guard_disarms_print_start(self):
        self.printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE = lambda gcmd: None
        self.assertFalse(self.obj.get_status(time.monotonic())['sd_api_guarded'])
        with self.assertRaisesRegex(RuntimeError, 'guard was replaced'):
            self.obj.cmd_process(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])

    def test_direct_python_virtual_sd_open_cannot_bypass_coordinator(self):
        with self.assertRaisesRegex(RuntimeError, 'direct virtual-SD start blocked'):
            self.printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE(Gcmd())
        self.assertEqual(self.printer.ratos.v_sd.started, [])
        self.assertTrue(self.obj.get_status(time.monotonic())['sd_api_guarded'])

    def test_single_use_sd_open_authorisation_cannot_be_replayed(self):
        self.obj._authorised_sd_open(self.printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE,
                                     Gcmd('first.gcode'))
        self.assertEqual(self.printer.ratos.v_sd.started, ['first.gcode'])
        self.printer.ratos.v_sd.finish()
        with self.assertRaisesRegex(RuntimeError, 'direct virtual-SD start blocked'):
            self.printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE(Gcmd('second.gcode'))
        self.assertEqual(self.printer.ratos.v_sd.started, ['first.gcode'])

    def test_direct_python_resume_requires_guarded_release(self):
        sd = self.printer.ratos.v_sd
        self.printer.gate._protected_session = {
            'filename': 'part.gcode', 'phase': 'MANUAL_REQUIRED', 'invalid': False}
        with self.assertRaisesRegex(mod.CoordinatorError, 'direct do_resume bypass refused'):
            sd.do_resume()
        self.assertEqual(sd.resume_calls, 0)
        self.assertTrue(self.obj.get_status(time.monotonic())['resume_api_guarded'])
        self.printer.gate._protected_session['phase'] = 'MANUAL_AUTHORIZED'
        self.obj._native_m24 = lambda gcmd: sd.do_resume()
        self.obj.release_protected_m24(Gcmd())
        self.assertEqual(sd.resume_calls, 1)
        with self.assertRaisesRegex(mod.CoordinatorError, 'direct do_resume bypass refused'):
            sd.do_resume()
        self.assertEqual(sd.resume_calls, 1)

    def test_unprotected_native_resume_still_works(self):
        sd = self.printer.ratos.v_sd
        sd.do_resume()
        self.assertEqual(sd.resume_calls, 1)

    def test_held_print_blocks_direct_m24_but_permits_guarded_release(self):
        native_calls = []
        self.obj._native_m24 = lambda gcmd: native_calls.append('native M24')
        self.printer.gcode.handlers['M24'](Gcmd())
        self.assertEqual(native_calls, ['native M24'])
        self.printer.gate._protected_session = {
            'filename': 'part.gcode', 'phase': 'MANUAL_REQUIRED', 'invalid': False}
        with self.assertRaisesRegex(RuntimeError, 'M24 bypass refused'):
            self.printer.gcode.handlers['M24'](Gcmd())
        with self.assertRaisesRegex(RuntimeError, 'no authorised'):
            self.obj.release_protected_m24(Gcmd())
        self.printer.gate._protected_session['phase'] = 'MANUAL_AUTHORIZED'
        self.obj.release_protected_m24(Gcmd())
        self.assertEqual(native_calls, ['native M24', 'native M24'])
        with self.assertRaisesRegex(RuntimeError, 'M24 bypass refused'):
            self.printer.gcode.handlers['M24'](Gcmd())

    def test_failed_held_session_cannot_be_resumed_directly(self):
        self.printer.gate._protected_session = {
            'filename': 'part.gcode', 'phase': 'MANUAL_REQUIRED', 'invalid': True}
        with self.assertRaisesRegex(RuntimeError, 'M24 bypass refused'):
            self.printer.gcode.handlers['M24'](Gcmd())

    def test_direct_m23_m26_cannot_select_or_seek_past_protection(self):
        for name in ('M23', 'M26'):
            with self.assertRaisesRegex(RuntimeError, 'direct SD selection/seeking blocked'):
                self.printer.gcode.handlers[name](Gcmd())
        # Native M24 still exists for the protected manual-VAOC hold.
        self.assertIn('M24', self.printer.gcode.handlers)
        self.obj.cmd_process(Gcmd())
        self.assertEqual(len(self.printer.ratos.v_sd.started), 1)

    def test_retention_scheduler_failure_restores_native_handlers(self):
        import unittest.mock
        printer = Printer(self.root)
        coordinator = mod.AutoXYRC16Handoff(Config(printer))
        coordinator.worker = WORKER
        coordinator.worker_python = sys.executable
        coordinator.registry = self.root / 'failed-retention-registry'
        coordinator.retention_enabled = True
        coordinator.retention_worker = ROOT / 'src/tools/rc16_generated_retention.py'
        native = printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE
        original_resume = printer.ratos.v_sd.do_resume
        original_handlers = dict(printer.gcode.handlers)
        with unittest.mock.patch.object(printer.reactor, 'register_timer',
                                        side_effect=OSError('simulated timer failure')):
            with self.assertRaisesRegex(RuntimeError, 'initialise atomically'):
                printer.handlers['klippy:ready']()
        self.assertFalse(coordinator.installed)
        self.assertEqual(printer.gcode.handlers, original_handlers)
        self.assertEqual(printer.ratos.v_sd.cmd_SDCARD_PRINT_FILE, native)
        self.assertEqual(printer.ratos.v_sd.do_resume, original_resume)

    def test_missing_sd_selection_guard_refuses_arming_and_restores_handlers(self):
        printer = Printer(self.root)
        del printer.gcode.handlers['M26']
        controller = mod.AutoXYRC16Handoff(Config(printer))
        controller.worker = WORKER
        controller.registry = self.root / 'registry_m26_missing'
        with self.assertRaisesRegex(RuntimeError, 'Required Klipper virtual-SD command missing'):
            printer.handlers['klippy:ready']()
        self.assertFalse(controller.installed)
        self.assertIn('PROCESS_GCODE_FILE', printer.gcode.handlers)
        self.assertIn('SDCARD_PRINT_FILE_BASE', printer.gcode.handlers)

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

    def test_file_change_during_registry_commit_never_starts_print(self):
        original_wait = self.obj._wait_worker
        def injected_wait(action, root, filename, mode=None, expected_hash=None, original=None, source_hash=None):
            result = original_wait(action, root, filename, mode, expected_hash, original, source_hash)
            if action == 'register':
                with open(Path(root) / filename, 'ab') as output:
                    output.write(b'; changed after registry commit\n')
            return result
        self.obj._wait_worker = injected_wait
        with self.assertRaisesRegex(RuntimeError, 'changed after reprint registration'):
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
