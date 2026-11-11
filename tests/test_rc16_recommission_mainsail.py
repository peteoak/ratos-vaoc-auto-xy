"""RC16 Mainsail nozzle recommission coordinator: non-motion safety and integration."""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_recommission_controller', ROOT / 'src/auto_xy_recommission_rc16.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Reactor:
    def monotonic(self):
        return 100.
    def register_timer(self, callback, at):
        return ('timer', at)
    NEVER = float('inf')


class Gcode:
    def __init__(self):
        self.handlers = {}
        self.messages = []
    def register_command(self, name, callback, **kwargs):
        self.handlers[name] = callback
    def respond_info(self, message):
        self.messages.append(message)


class Printer:
    def __init__(self):
        self.gcode = Gcode()
        self.reactor = Reactor()
        self.handlers = {}
    def get_reactor(self):
        return self.reactor
    def lookup_object(self, name, default=None):
        return self.gcode if name == 'gcode' else default
    def register_event_handler(self, name, callback):
        self.handlers[name] = callback


class Config:
    def __init__(self, printer, reference_root):
        self.printer = printer
        self.reference_root = reference_root
    def get_printer(self):
        return self.printer
    def get(self, name, default=None):
        if name == 'reference_root':
            return str(self.reference_root)
        return default


class Command:
    def __init__(self, args=None):
        self.args = args or {}
        self.replies = []
    def get_int(self, key, default=None, minval=None, maxval=None):
        value = int(self.args.get(key, default))
        if ((minval is not None and value < minval) or
                (maxval is not None and value > maxval)):
            raise ValueError('value out of range')
        return value
    def respond_info(self, message):
        self.replies.append(message)
    def error(self, message):
        return RuntimeError(message)


class RecommissionControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'references'
        self.printer = Printer()
        self.coordinator = mod.load_config(Config(self.printer, self.root))
        self.coordinator.worker = ROOT / 'src/tools/rc16_recommission_worker.py'
        self.coordinator.python = __import__('sys').executable

    def test_begin_never_homes_or_opens_vaoc_in_python(self):
        with patch.object(self.coordinator, '_preflight', return_value=(1., 2., 3.)):
            self.coordinator.cmd_begin(Command())
        self.assertEqual(self.coordinator.phase, 'STARTING')
        self.assertTrue(self.coordinator.session.is_dir())
        self.assertEqual(len(self.coordinator.groups[0]), 0)
        self.assertEqual(len(self.coordinator.groups[1]), 0)
        self.assertIn('AUTOXY_RECOMMISSION_CANCEL', self.printer.gcode.handlers)

    def test_capture_is_asynchronous_and_requires_explicit_position(self):
        with patch.object(self.coordinator, '_preflight', return_value=(1., 2., 3.)):
            self.coordinator.cmd_begin(Command())
            with self.assertRaisesRegex(RuntimeError, 'Position a tool'):
                self.coordinator.cmd_capture(Command({'TOOL': 0}))
            self.coordinator.cmd_positioned(Command({'TOOL': 0}))
            with patch.object(self.coordinator, '_start_worker') as worker:
                self.coordinator.cmd_capture(Command({'TOOL': 0}))
                args = worker.call_args
                self.assertEqual(args.args[0], 'CAPTURE')
                self.assertEqual(args.args[1]['tool'], 0)

    def test_save_rejects_incomplete_images_and_no_confirm(self):
        with patch.object(self.coordinator, '_preflight', return_value=(1., 2., 3.)):
            self.coordinator.cmd_begin(Command())
            self.coordinator.phase = 'T0_CAPTURED'
            self.coordinator.groups[0] = ['a', 'b', 'c']
            with self.assertRaisesRegex(RuntimeError, 'Both T0 and T1'):
                self.coordinator.cmd_publish(Command({'CONFIRM': 1}))
            self.coordinator.groups[1] = ['d', 'e', 'f']
            with self.assertRaisesRegex(RuntimeError, 'Confirm'):
                self.coordinator.cmd_publish(Command({'CONFIRM': 0}))
            with patch.object(self.coordinator, '_start_worker') as worker:
                self.coordinator.cmd_publish(Command({'CONFIRM': 1}))
                worker.assert_called_once()

    def test_cancel_refuses_while_worker_is_running(self):
        self.coordinator.proc = object()
        with self.assertRaisesRegex(RuntimeError, 'Capture/Save still running'):
            self.coordinator.cmd_cancel(Command())

    def test_mainsail_macros_use_existing_native_vaoc_and_do_not_double_load_t0(self):
        file = (ROOT / 'config/auto_xy_recommission_rc16_macros.cfg').read_text()
        expected = {
            'AUTO_XY_RECOMMISSION_NOZZLES',
            'AUTO_XY_RECOMMISSION_T0', 'AUTO_XY_RECOMMISSION_T1',
            'AUTO_XY_RECOMMISSION_CAPTURE_T0',
            'AUTO_XY_RECOMMISSION_CAPTURE_T1',
            'AUTO_XY_RECOMMISSION_SAVE', 'AUTO_XY_RECOMMISSION_FINISH',
            'AUTO_XY_RECOMMISSION_CANCEL', 'AUTO_XY_RECOMMISSION_STATUS'}
        declared = set(re.findall(r'^\[gcode_macro (.*?)\]$', file, re.M))
        self.assertEqual(expected, declared)
        self.assertIn('    _VAOC_START', file)
        self.assertIn('    _VAOC_END', file)
        self.assertIn('printer["auto_xy_recommission_rc16"].phase != "STARTING"', file)
        self.assertIn('AUTOXY_RECOMMISSION_POSITIONED TOOL=0', file)
        self.assertIn('AUTOXY_RECOMMISSION_PUBLISH CONFIRM=1', file)
        self.assertNotIn('SAVE_VARIABLE', file)
        self.assertNotIn('SET_GCODE_OFFSET', file)

    def test_installation_requires_both_kipper_extension_and_macro_config(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        import rc16_active_migration as active
        cfg = active.active_handoff_config(Path('/home/pi'))
        self.assertIn('[include auto_xy_recommission_rc16_macros.cfg]', cfg)
        self.assertIn('[auto_xy_recommission_rc16]', cfg)
        self.assertIn('rc16_recommission_worker.py', cfg)


if __name__ == '__main__':
    unittest.main()
