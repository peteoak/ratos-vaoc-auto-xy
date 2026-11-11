# AutoXY RC16 Mainsail-driven nozzle recommissioning.
# Uses stock RatOS _VAOC_* macros for all tool movement. No arbitrary G-code
# execution, homing, offset writes, or heater control from this extension.
import hashlib
import json
import logging
import os
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path


class NozzleRecommission:
    OPERATIONS = ('CAPTURE', 'PUBLISH')

    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.worker = Path(config.get(
            'worker', '/home/pi/auto_xy/workers/rc16_recommission_worker.py'))
        self.python = config.get(
            'worker_python', '/home/pi/auto-xy-venv/bin/python')
        self.reference_root = Path(config.get(
            'reference_root', '/home/pi/auto_xy/nozzle_references'))
        self.results_root = Path(config.get(
            'results_root', '/home/pi/auto_xy/print_gate_runs'))
        self.profile = Path(config.get(
            'profile', '/home/pi/auto_xy/camera_commission.json'))
        self.camera = config.get('camera_url',
                                 'http://127.0.0.1:8080/?action=snapshot')
        self.phase = 'IDLE'
        self.last_error = ''
        self.session = None
        self.current_tool = None
        self.groups = {0: [], 1: []}
        self.proc = None
        self.job = None
        self.captured_position = None
        self._poll_timer = None
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        for command, callback in (
            ('AUTOXY_RECOMMISSION_BEGIN', self.cmd_begin),
            ('AUTOXY_RECOMMISSION_POSITIONED', self.cmd_positioned),
            ('AUTOXY_RECOMMISSION_CAPTURE', self.cmd_capture),
            ('AUTOXY_RECOMMISSION_PUBLISH', self.cmd_publish),
            ('AUTOXY_RECOMMISSION_FINISH', self.cmd_finish),
            ('AUTOXY_RECOMMISSION_CANCEL', self.cmd_cancel),
            ('AUTOXY_RECOMMISSION_STATUS', self.cmd_status)):
            self.gcode.register_command(command, callback)

    def get_status(self, eventtime):
        return {
            'phase': self.phase,
            'busy': bool(self.proc is not None),
            't0_frames': len(self.groups[0]),
            't1_frames': len(self.groups[1]),
            'positioned_tool': self.current_tool,
            'last_error': self.last_error,
            'ready': (self.worker.is_file() and not self.worker.is_symlink()
                      and Path(self.python).is_file()),
        }

    def _notice(self, text):
        try:
            self.gcode.respond_info('AutoXY recommission | ' + text)
        except Exception:
            logging.exception('AutoXY recommission status message failed')

    def _reject(self, reason):
        raise RuntimeError('AutoXY recommission: ' + reason)

    def _gate(self):
        gate = self.printer.lookup_object('auto_xy_gate', None)
        if gate is None:
            self._reject('AutoXY gate missing')
        return gate

    def _sd_active(self):
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is None:
            return False
        return bool(sd.get_status(self.reactor.monotonic()).get('is_active', False))

    def _vaoc_active(self):
        vaoc = self.printer.lookup_object('gcode_macro _VAOC', None)
        if vaoc is None:
            self._reject('native RatOS VAOC unavailable')
        return bool(vaoc.get_status(self.reactor.monotonic()).get('is_started', False))

    def _tool_position(self):
        toolhead = self.printer.lookup_object('toolhead', None)
        if toolhead is None:
            self._reject('toolhead missing')
        status = toolhead.get_status(self.reactor.monotonic())
        homed = str(status.get('homed_axes', ''))
        if not all(axis in homed for axis in 'xyz'):
            self._reject('printer must have home XYZ; do not start capture')
        position = status.get('position')
        if position is None or len(position) < 3:
            self._reject('toolhead position unavailable')
        return tuple(float(p) for p in position[:3])

    def _preflight(self, require_vaoc):
        if self.printer.get_state_message()[1] != 'ready':
            self._reject('Klipper not Ready')
        if self.proc is not None:
            self._reject('capture/publish still running')
        stats = self.printer.lookup_object('print_stats', None)
        if stats is None:
            self._reject('print status unavailable')
        if str(stats.get_status(self.reactor.monotonic()).get('state', '')).lower() in (
                'printing', 'paused'):
            self._reject('printer busy or paused; no recommission during a print')
        if self._sd_active():
            self._reject('virtual-SD file still selected or active')
        gate = self._gate()
        if getattr(gate, 'busy', False) or getattr(gate, '_endurance_active', False):
            self._reject('automatic calibration active')
        held = getattr(gate, '_protected_session', None)
        if isinstance(held, dict) and held.get('phase') not in (
                'GUARD_CONSUMED', 'FINISHING'):
            self._reject('protected print recovery pending')
        # Recommissioning can be used to resolve a previous detection fault;
        # it does not clear any saved fault, in-progress or enable flags.
        for extruder in ('extruder', 'extruder1'):
            obj = self.printer.lookup_object(extruder, None)
            if obj is None:
                self._reject(extruder + ' unavailable')
            state = obj.get_status(self.reactor.monotonic())
            limit = float(getattr(gate, 'max_camera_nozzle_temp', 155.))
            target_limit = float(getattr(gate, 'max_camera_target_temp', 150.))
            if (float(state.get('temperature', 999.)) > limit or
                    float(state.get('target', 999.)) > target_limit):
                self._reject('nozzle temperature/target exceeds camera-safe limit')
        pos = self._tool_position()
        if require_vaoc != self._vaoc_active():
            self._reject('native VAOC state unexpected; use Mainsail Start/Finish')
        return pos

    def _library(self):
        root, results = self.reference_root, self.results_root
        if (root.is_symlink() or results.is_symlink()
                or root.resolve(strict=False) == results.resolve(strict=False)
                or root.resolve(strict=False) in results.resolve(strict=False).parents
                or results.resolve(strict=False) in root.resolve(strict=False).parents):
            self._reject('unsafe reference storage path')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            self._reject('reference library is unsafe')
        archive = root / 'archived'
        archive.mkdir(mode=0o700, exist_ok=True)
        if archive.is_symlink() or not archive.is_dir():
            self._reject('archive directory unsafe')
        return root, archive

    def _start_worker(self, operation, args, position=None):
        if operation not in self.OPERATIONS or self.proc is not None:
            self._reject('invalid or overlapping worker request')
        if not self.worker.is_file() or self.worker.is_symlink():
            self._reject('trusted recommission worker missing')
        if not Path(self.python).is_file():
            self._reject('vision interpreter missing')
        command = [
            self.python, str(self.worker), args['action'],
            '--session', str(self.session), '--camera-url', self.camera,
            '--reference-root', str(self.reference_root),
            '--results-root', str(self.results_root)]
        if operation == 'CAPTURE':
            command.extend(('--tool', str(args['tool']), '--capture-id', args['capture_id']))
        else:
            command.extend(('--t0', *self.groups[0], '--t1', *self.groups[1],
                            '--profile', str(self.profile),
                            '--backup-dir', str(self.reference_root / 'archived')))
        self.proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True)
        self.job = (operation, args.get('tool'))
        self.captured_position = position
        self.phase = 'CAPTURING_T%d' % args['tool'] if operation == 'CAPTURE' else 'PUBLISHING'
        self._poll_timer = self.reactor.register_timer(
            self._poll, self.reactor.monotonic() + .25)

    def _poll(self, eventtime):
        proc = self.proc
        if proc is None:
            return getattr(self.reactor, 'NEVER', float('inf'))
        if proc.poll() is None:
            return eventtime + .35
        operation, tool = self.job
        self.proc = None
        self.job = None
        self.captured_position = None if operation != 'CAPTURE' else self.captured_position
        try:
            out, err = proc.communicate(timeout=.1)
            if proc.returncode != 0:
                raise ValueError(err.decode('utf-8', 'replace')[:350].strip()
                                 or 'camera worker failed')
            result = json.loads(out.decode('utf-8'))
            if operation == 'CAPTURE':
                if (self.current_tool != tool or
                        not self._vaoc_active() or
                        self._tool_position() != self.captured_position):
                    raise ValueError('Tool or position changed during image capture')
                images = result.get('images')
                if not isinstance(images, list) or len(images) != 3:
                    raise ValueError('Expected exactly three captured images')
                root = self.session.resolve(strict=True)
                if any(Path(name).resolve(strict=True).parent != root
                       or Path(name).is_symlink() for name in images):
                    raise ValueError('Untrusted camera capture paths')
                if len(set(images)) != 3:
                    raise ValueError('Duplicate camera paths')
                self.groups[tool] = images
                self.phase = 'T%d_CAPTURED' % tool
                self._notice('T%d captured: 3 full-frame JPEGs. Position the other tool or Save.' % tool)
            else:
                if result.get('status') != 'COMMITTED_FULL_FRAME_REFERENCES':
                    raise ValueError('Profile not confirmed as committed')
                self.phase = 'PUBLISHED'
                self._notice('Both nozzle reference sets validated and saved; press Recommission Finish to exit VAOC')
        except Exception as exc:
            self.last_error = str(exc)
            self.phase = 'ERROR'
            self._notice('FAILED: ' + self.last_error +
                         '. Old profile remains available; inspect before retrying')
        return getattr(self.reactor, 'NEVER', float('inf'))

    def cmd_begin(self, gcmd):
        if self.phase not in ('IDLE', 'CANCELLED', 'COMPLETE', 'ERROR'):
            raise gcmd.error('Recommission already active; Finish or Cancel first')
        self._preflight(require_vaoc=False)
        root, _ = self._library()
        self.session = Path(tempfile.mkdtemp(prefix='session-', dir=str(root)))
        self.groups = {0: [], 1: []}
        self.current_tool = None
        self.last_error = ''
        self.phase = 'STARTING'
        gcmd.respond_info('AutoXY recommission started: native VAOC will open. '
                          'Manually position T0, capture; then T1, capture; '
                          'Save explicitly to replace references.')

    def cmd_positioned(self, gcmd):
        if self.phase not in ('STARTING', 'T0_POSITIONED', 'T1_POSITIONED',
                              'T0_CAPTURED', 'T1_CAPTURED', 'ERROR'):
            raise gcmd.error('Start recommissioning first')
        self._preflight(require_vaoc=True)
        tool = gcmd.get_int('TOOL', minval=0, maxval=1)
        self.current_tool = tool
        self.phase = 'T%d_POSITIONED' % tool
        gcmd.respond_info('T%d: manually adjust position in native VAOC, '
                          'then press Capture T%d.' % (tool, tool))

    def cmd_capture(self, gcmd):
        if self.phase not in ('T0_POSITIONED', 'T1_POSITIONED',
                              'T0_CAPTURED', 'T1_CAPTURED', 'ERROR'):
            raise gcmd.error('Position a tool before capturing')
        position = self._preflight(require_vaoc=True)
        tool = gcmd.get_int('TOOL', minval=0, maxval=1)
        if tool != self.current_tool:
            raise gcmd.error('Position the requested nozzle in native VAOC first')
        self._start_worker('CAPTURE', {'action': 'capture', 'tool': tool,
                                     'capture_id': secrets.token_hex(6)},
                           position)
        gcmd.respond_info('Capturing three independent full-frame T%d images in background' % tool)

    def cmd_publish(self, gcmd):
        if self.phase not in ('T0_CAPTURED', 'T1_CAPTURED'):
            raise gcmd.error('Capture T0 and T1 before Save')
        self._preflight(require_vaoc=True)
        if len(self.groups[0]) != 3 or len(self.groups[1]) != 3:
            raise gcmd.error('Both T0 and T1 must have three fresh images')
        if gcmd.get_int('CONFIRM', default=0, minval=0, maxval=1) != 1:
            raise gcmd.error('Confirm visually verified manual VAOC before Save')
        self._start_worker('PUBLISH', {'action': 'publish'})
        gcmd.respond_info('Validating six images and publishing a replacement profile; '
                          'do not exit VAOC until Save succeeds')

    def cmd_finish(self, gcmd):
        if self.phase != 'PUBLISHED':
            raise gcmd.error('Cannot Finish before successful Save; Cancel instead')
        self._preflight(require_vaoc=True)
        self.phase = 'COMPLETE'
        gcmd.respond_info('Recommission saved. Native VAOC will close; '
                          'all saved offsets and AutoXY enable/fault flags remain unchanged')

    def cmd_cancel(self, gcmd):
        if self.proc is not None:
            raise gcmd.error('Capture/Save still running; Cancel when operation has ended')
        if self.phase == 'PUBLISHED':
            raise gcmd.error('New profile already saved; use Finish to exit VAOC')
        self.phase = 'CANCELLED'
        self.current_tool = None
        self.groups = {0: [], 1: []}
        gcmd.respond_info('Recommission cancelled. Existing profile unchanged. '
                          'Native VAOC will close.')

    def cmd_status(self, gcmd):
        status = self.get_status(self.reactor.monotonic())
        gcmd.respond_info('AutoXY recommission: %s; T0=%d/3 T1=%d/3; %s' %
                          (status['phase'], status['t0_frames'],
                           status['t1_frames'], status['last_error']))

    def _shutdown(self, *args):
        # Never interrupt an atomic publish or delete uncommitted captures;
        # records survive for investigation if service restarts.
        self.phase = 'INTERRUPTED'
        self.current_tool = None


def load_config(config):
    return NozzleRecommission(config)
