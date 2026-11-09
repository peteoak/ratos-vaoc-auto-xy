"""RC16 EXPERIMENTAL: cooperative print-file coordinator, not installed by RC15.

Owns only the PROCESS_GCODE_FILE request. RatOS's native postprocessor runs
unmodified at normal priority and ALREADY uses reactor.pause() while waiting.
CPU-heavy AutoXY file operations run in an external, lower-priority worker.

UNQUALIFIED: no installer/preflight or full printer-start bypass verification.
DO NOT ENABLE on a real printer until the release safety matrix is completed.
"""
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

STAGED = re.compile(r'^__AutoXY_RC16_[0-9a-f]{24}\.gcode$')
WORKER_RESULT_LIMIT = 4096


class CoordinatorError(ValueError):
    pass


class FilenameProxy:
    def __init__(self, original, filename):
        self.original, self.filename = original, filename

    def get(self, field, default=None, *a, **kw):
        if field.upper() == 'FILENAME':
            return self.filename
        return self.original.get(field, default, *a, **kw)

    def __getattr__(self, key):
        return getattr(self.original, key)


class AutoXYRC16Handoff:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.enabled = config.getboolean('enabled', False)
        self.worker = Path(config.get('worker', '/home/pi/auto_xy/workers/rc16_prepare_worker.py'))
        self.worker_python = config.get('worker_python', sys.executable)
        self.installed = False
        self.busy = False
        self.gcode.register_command('AUTOXY_RC16_HANDOFF_STATUS', self.cmd_status)
        self.printer.register_event_handler('klippy:ready', self.on_ready)

    def cmd_status(self, gcmd):
        gcmd.respond_info('AutoXY RC16 handoff prototype: enabled=%s armed=%s busy=%s' %
                          (self.enabled, self.installed, self.busy))

    def on_ready(self):
        if not self.enabled:
            return
        ratos = self.printer.lookup_object('ratos', None)
        gate = self.printer.lookup_object('auto_xy_gate', None)
        if (ratos is None or gate is None or not getattr(gate, 'protected_print_enabled', False)):
            raise self.printer.config_error('RC16 requires native RatOS and enabled AutoXY protected gate')
        if not hasattr(ratos, 'process_gcode_file') or not hasattr(ratos, 'v_sd'):
            raise self.printer.config_error('RatOS process API does not match audited version')
        if not self.worker.is_file() or self.worker.is_symlink():
            raise self.printer.config_error('RC16 worker not installed at a trusted regular path')
        # RatOS 2.1 macro: SDCARD_PRINT_FILE -> PROCESS_GCODE_FILE,
        # while SDCARD_PRINT_FILE_BASE is the lower-level renamed primitive.
        # Direct use of that primitive must not bypass protected printing.
        old_base = self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
        if old_base is None:
            raise self.printer.config_error(
                'RatOS SDCARD_PRINT_FILE_BASE command not present; cannot guard bypass')
        try:
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', self.cmd_direct_base)
        except Exception:
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', old_base)
            raise
        old_processor = self.gcode.register_command('PROCESS_GCODE_FILE', None)
        if old_processor is None:
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', old_base)
            raise self.printer.config_error('Native RatOS PROCESS_GCODE_FILE command missing')
        try:
            self.gcode.register_command('PROCESS_GCODE_FILE', self.cmd_process)
        except Exception:
            self.gcode.register_command('PROCESS_GCODE_FILE', old_processor)
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', old_base)
            raise
        self._native_base = old_base
        self.ratos, self.gate = ratos, gate
        self.installed = True

    def cmd_direct_base(self, gcmd):
        # A direct primitive invocation skips RatOS and hides IDEX mode.
        # Reject instead of silently running an independent two-tool job.
        # Normal Mainsail starts go through the public RatOS macro and are
        # unchanged for single-tool, COPY and MIRROR files.
        raise gcmd.error('RC16 protected mode: use normal SDCARD_PRINT_FILE through RatOS, not SDCARD_PRINT_FILE_BASE')

    def _wait_worker(self, action, root, filename, mode=None, expected_hash=None):
        argv = [self.worker_python, str(self.worker), action,
                '--root', str(root), '--source', filename]
        if mode is not None:
            argv.extend(['--mode', mode])
        if expected_hash is not None:
            argv.extend(['--expected-sha256', expected_hash])
        # Never inherit RatOS's resource settings: only the worker lowers its
        # OWN priority, and RatOS is called separately from the coordinator.
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        reactor = self.printer.get_reactor()
        finish = reactor.monotonic() + 180.0
        try:
            while proc.poll() is None:
                when = reactor.monotonic()
                if when >= finish:
                    raise CoordinatorError('AutoXY worker timed out')
                reactor.pause(min(finish, when + .05))
            if proc.returncode != 0:
                raise CoordinatorError('AutoXY worker rejected file; source remains unprinted')
            # Worker writes a single bounded JSON response, so pipe never
            # fills; reads occur only AFTER the child has exited.
            raw = proc.stdout.read(WORKER_RESULT_LIMIT + 1)
            if len(raw) > WORKER_RESULT_LIMIT:
                raise CoordinatorError('AutoXY worker returned oversized report')
            result = json.loads(raw)
            expected = {'stage': 'STAGED_NOT_AUTHORIZED',
                        'prepare': 'PREPARED_NOT_AUTHORIZED',
                        'verify': 'VERIFIED_NOT_AUTHORIZED'}[action]
            if result.get('status') != expected:
                raise CoordinatorError('Unrecognised AutoXY worker result')
            return result
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def _mode(self):
        status = self.ratos.dual_carriage.get_status(
            self.printer.get_reactor().monotonic())
        carriage = str(status.get('carriage_1', '')).lower()
        if carriage in ('primary', 'inactive'):
            return 'independent'
        if carriage in ('copy', 'mirror'):
            return carriage
        raise CoordinatorError('Unrecognised IDEX mode')

    def cmd_process(self, gcmd):
        if not self.installed or self.busy:
            raise gcmd.error('RC16 handoff not armed or another request is running')
        self.busy = True
        stage_name = None
        handed_to_sd = False
        try:
            # Native RatOS bypass/transform options cannot guarantee safe
            # independent IDEX preparation; do not guess an unseen print mode.
            if self.ratos.bypass_post_processing or not self.ratos.enable_gcode_transform:
                raise CoordinatorError('Native RatOS transform or postprocessor bypass is enabled')
            root = Path(self.ratos.sdcard_dirname)
            original = gcmd.get('FILENAME', '')
            # Reprint of generated RC16 file needs registry resolution and must
            # NEVER be sent through the postprocessor again as a new source.
            if Path(original).name.startswith('__AutoXY_RC16_'):
                raise CoordinatorError('Generated file reprint needs verified source registry; select original')
            staged = self._wait_worker('stage', root, original)
            stage_name = staged.get('stage_name')
            if not isinstance(stage_name, str) or not STAGED.fullmatch(stage_name):
                raise CoordinatorError('Worker returned invalid staged filename')
            self.gcode.run_script_from_command(
                'SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=first_x VALUE=-1')
            self.gcode.run_script_from_command(
                'SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=first_y VALUE=-1')
            if staged.get('already_ratos_processed') is not True:
                # RatOS's own implementation uses reactor.pause() while its
                # unrestricted subprocess runs; preserve that behaviour.
                if not self.ratos.process_gcode_file(stage_name, self.ratos.enable_gcode_transform):
                    raise CoordinatorError('Native RatOS processor rejected staged file')
            mode = self._mode()
            prepared = self._wait_worker('prepare', root, stage_name, mode)
            if prepared.get('stage_name') != stage_name or prepared.get('mode') != mode:
                raise CoordinatorError('Worker result identity or mode mismatch')
            protected = prepared.get('protected')
            if type(protected) is not bool:
                raise CoordinatorError('Worker did not establish protection state')
            if protected:
                if (self.gate.busy or not self.gate.enabled()
                        or self.gate.flag('auto_xy_v2_fault')
                        or self.gate.flag('auto_xy_v2_in_progress')):
                    raise CoordinatorError('AutoXY cannot authorise independent dual-tool print')
            elif mode == 'independent':
                gcmd.respond_info('AutoXY RC16: independent single-tool print; native RatOS path')
            final_hash = prepared.get('output_sha256')
            if not isinstance(final_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', final_hash):
                raise CoordinatorError('Missing trusted output checksum')
            verified = self._wait_worker('verify', root, stage_name, mode,
                                         expected_hash=final_hash)
            if (verified.get('stage_name') != stage_name or
                    verified.get('mode') != mode or
                    verified.get('protected') is not protected or
                    verified.get('sha256') != final_hash):
                raise CoordinatorError('Final prepared file verification mismatch')
            # Only a short metadata check is done in Klipper itself; hashing
            # and parsing already happened in the low-priority worker.
            path = root / stage_name
            st = path.stat()
            fingerprint = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
            if fingerprint != verified.get('fingerprint') or path.is_symlink():
                raise CoordinatorError('Prepared file changed before virtual-SD handoff')
            # Arm the one-use protected controller ONLY for eligible dual-tool
            # independent prints (the six injected macros enforce the hold).
            handed_to_sd = True
            result = self.ratos.v_sd.cmd_SDCARD_PRINT_FILE(
                FilenameProxy(gcmd, stage_name))
            return result
        except Exception as exc:
            logging.exception('AutoXY RC16 guarded print preparation refused')
            raise gcmd.error('AutoXY RC16 print not started: ' + str(exc))
        finally:
            self.busy = False
            # Keep staged files, even on failure: a timed-out worker can still
            # be exiting, and cleanup/ownership is not yet release-qualified.
            if stage_name and not handed_to_sd:
                logging.info('AutoXY RC16 rejected candidate retained; not opened by virtual SD')


def load_config(config):
    return AutoXYRC16Handoff(config)
