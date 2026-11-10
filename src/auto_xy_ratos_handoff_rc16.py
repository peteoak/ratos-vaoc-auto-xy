"""RC16 EXPERIMENTAL: cooperative print-file coordinator, not installed by RC15.

Owns only the PROCESS_GCODE_FILE request. RatOS's native postprocessor runs
unmodified at normal priority and ALREADY uses reactor.pause() while waiting.
CPU-heavy AutoXY file operations run in an external, lower-priority worker.

UNQUALIFIED: no installer/preflight or full printer-start bypass verification.
DO NOT ENABLE on a real printer until the release safety matrix is completed.
"""
import json
import fcntl
import stat
import time
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

STAGED = re.compile(r'^__AutoXY_RC16_[0-9a-f]{24}\.gcode$')
WORKER_RESULT_LIMIT = 4096
RETENTION_LOCK_NAME = '.rc16_retention.lock'


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
        self.registry = Path(config.get('registry_directory', '/home/pi/auto_xy/rc16_registry'))
        # This is an opt-in RC16 development extension. When the handoff is
        # eventually enabled, retention is enabled by default as well.
        self.retention_enabled = config.getboolean('retention_enabled', True)
        self.retention_days = config.getint('retention_days', 30, minval=7)
        self.retention_interval = config.getint('retention_interval_hours', 24, minval=1)
        self.retention_worker = Path(config.get(
            'retention_worker', '/home/pi/auto_xy/workers/rc16_generated_retention.py'))
        self._retention_proc = None
        self._retention_next = 0.0
        self._retention_timer = None
        self.installed = False
        self.busy = False
        self._native_sd_open = None
        self._sd_open_permitted = False
        self._native_do_resume = None
        self._protected_resume_granted = False
        # Shared nonblocking flock guards the entire prepared-file lifetime,
        # including M25/manual-VAOC pauses. The cleanup worker takes EX lock.
        self._print_lock_fd = None
        self._print_lock_timer = None
        self.gcode.register_command('AUTOXY_RC16_HANDOFF_STATUS', self.cmd_status)
        self.printer.register_event_handler('klippy:ready', self.on_ready)

    def get_status(self, eventtime):
        # Read-only Klipper status for installer verification. 'armed' becomes
        # True only AFTER native processing and bypass handlers are installed.
        return {'enabled': bool(self.enabled),
                'armed': bool(self.installed),
                'sd_api_guarded': bool(self.installed and self._native_sd_open and
                                       self.ratos.v_sd.cmd_SDCARD_PRINT_FILE == self._guard_sd_open),
                'resume_api_guarded': bool(self.installed and self._native_do_resume and
                                           self.ratos.v_sd.do_resume == self._guard_do_resume),
                'busy': bool(self.busy),
                'retention_enabled': bool(self.retention_enabled)}

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
        # Check all activation prerequisites before changing ANY Klipper
        # command ownership; a missing retention executable must fail closed.
        if (self.retention_enabled and
                (not self.retention_worker.is_file() or
                 self.retention_worker.is_symlink())):
            raise self.printer.config_error(
                'RC16 automatic retention worker missing or unsafe')
        # Prepare the lock before intercepting print commands. A symlinked
        # registry or non-regular lock is never trusted.
        try:
            self.registry.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.registry.is_dir() or self.registry.is_symlink():
                raise ValueError('Untrusted RC16 registry directory')
            lock_path = self.registry / RETENTION_LOCK_NAME
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR |
                         getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('Untrusted RC16 retention lock')
            finally:
                os.close(fd)
        except (OSError, ValueError) as exc:
            raise self.printer.config_error('RC16 retention lock preflight refused: ' + str(exc))
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
        # M23/M26 can bypass file preparation. M24 must also be guarded
        # during a held AutoXY recovery; unrelated native paused prints may
        # still use it. The gate calls release_protected_m24 only after it
        # verifies the held-file recovery state.
        installed = []
        try:
            for name in ('M23', 'M26', 'M24'):
                old_handler = self.gcode.register_command(name, None)
                if old_handler is None:
                    raise self.printer.config_error(
                        'Required Klipper virtual-SD command missing: ' + name)
                installed.append((name, old_handler))
                self.gcode.register_command(
                    name, self.cmd_m24 if name == 'M24'
                    else self.cmd_direct_selection)
        except Exception:
            for name, old_handler in reversed(installed):
                self.gcode.register_command(name, None)
                self.gcode.register_command(name, old_handler)
            self.gcode.register_command('PROCESS_GCODE_FILE', None)
            self.gcode.register_command('PROCESS_GCODE_FILE', old_processor)
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
            self.gcode.register_command('SDCARD_PRINT_FILE_BASE', old_base)
            raise
        self._native_base = old_base
        self._native_processor = old_processor
        self._native_m24 = next(handler for name, handler in installed if name == 'M24')
        self.ratos, self.gate = ratos, gate
        # Guard direct Python/API virtual-SD file opens as well as G-code
        # command routes. Only the coordinator can issue a one-use SD open.
        # Fail startup if the target API cannot be wrapped; never claim armed.
        self._native_sd_open = ratos.v_sd.cmd_SDCARD_PRINT_FILE
        self._native_do_resume = ratos.v_sd.do_resume
        try:
            ratos.v_sd.cmd_SDCARD_PRINT_FILE = self._guard_sd_open
            ratos.v_sd.do_resume = self._guard_do_resume
            if (ratos.v_sd.cmd_SDCARD_PRINT_FILE != self._guard_sd_open or
                    ratos.v_sd.do_resume != self._guard_do_resume):
                raise CoordinatorError('Virtual SD API interception not retained')
            if self.retention_enabled:
                reactor = self.printer.get_reactor()
                self._retention_next = reactor.monotonic() + 120.
                self._retention_timer = reactor.register_timer(
                    self._retention_tick, self._retention_next)
        except Exception as exc:
            # Startup is transactional: never retain half-installed G-code
            # handlers or a partly wrapped virtual-SD instance after failure.
            try:
                ratos.v_sd.cmd_SDCARD_PRINT_FILE = self._native_sd_open
                ratos.v_sd.do_resume = self._native_do_resume
            finally:
                for name, old_handler in reversed(installed):
                    self.gcode.register_command(name, None)
                    self.gcode.register_command(name, old_handler)
                self.gcode.register_command('PROCESS_GCODE_FILE', None)
                self.gcode.register_command('PROCESS_GCODE_FILE', old_processor)
                self.gcode.register_command('SDCARD_PRINT_FILE_BASE', None)
                self.gcode.register_command('SDCARD_PRINT_FILE_BASE', old_base)
            raise self.printer.config_error(
                'RC16 handoff could not initialise atomically: ' + str(exc))
        self.installed = True

    def _retention_safe_to_start(self):
        # A short in-memory status check ONLY; the cleanup worker makes its
        # own independent Moonraker checks immediately before every unlink.
        if self.busy or self._retention_proc is not None:
            return False
        if not self.registry.is_dir() or self.registry.is_symlink():
            return False
        ps = self.printer.lookup_object('print_stats', None)
        vsd = self.printer.lookup_object('virtual_sdcard', None)
        if ps is None or vsd is None:
            return False
        now = self.printer.get_reactor().monotonic()
        try:
            stats = ps.get_status(now)
            current = vsd.get_status(now)
            return (stats.get('state') in ('standby', 'complete', 'cancelled', 'error')
                    and current.get('is_active') is False
                    and current.get('file_path') in (None, ''))
        except Exception:
            logging.exception('RC16 retention idle check failed; deferring')
            return False

    def _retention_tick(self, eventtime):
        # Never wait, scan, hash or delete in the reactor timer. At most a
        # process poll and a low-priority spawn. No RatOS process is changed.
        if self._retention_proc is not None:
            if self._retention_proc.poll() is None:
                return eventtime + 30.
            result = self._retention_proc.returncode
            self._retention_proc = None
            if result != 0:
                logging.warning('RC16 automatic retention worker declined cleanup')
            self._retention_next = eventtime + self.retention_interval * 3600.
        if eventtime >= self._retention_next and self._retention_safe_to_start():
            try:
                self._retention_proc = subprocess.Popen([
                    self.worker_python, str(self.retention_worker),
                    '--auto', '--root', str(self.ratos.sdcard_dirname),
                    '--registry', str(self.registry), '--days',
                    str(self.retention_days)
                ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
            except OSError:
                logging.exception('RC16 automatic retention spawn failed')
                self._retention_next = eventtime + 3600.
        return eventtime + 30.

    def _release_print_lock(self):
        if self._print_lock_fd is not None:
            fd, self._print_lock_fd = self._print_lock_fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _print_lock_tick(self, eventtime):
        """Reap only AFTER virtual SD has closed the selected file.

        Paused prints retain file_path and are never released. This is a
        constant-time status check; it performs no filesystem scanning.
        """
        if self._print_lock_fd is None:
            return self.printer.get_reactor().NEVER
        if self.busy:
            return eventtime + 2.
        try:
            status = self.ratos.v_sd.get_status(eventtime)
            if status.get('is_active') is False and status.get('file_path') in (None, ''):
                self._release_print_lock()
                return self.printer.get_reactor().NEVER
        except Exception:
            logging.exception('RC16 cannot verify virtual-SD closure; keeping print file locked')
        return eventtime + 2.

    def _acquire_print_lock(self, gcmd):
        if self._print_lock_fd is not None:
            # A previous print can finish immediately before the next start,
            # even if its two-second reaper has not fired yet. Check the
            # actual native virtual-SD state synchronously, without waiting.
            try:
                status = self.ratos.v_sd.get_status(
                    self.printer.get_reactor().monotonic())
                if (status.get('is_active') is False and
                        status.get('file_path') in (None, '')):
                    self._release_print_lock()
            except Exception:
                logging.exception('RC16 cannot prove previous file is closed')
        if self._print_lock_fd is not None:
            raise gcmd.error('RC16: previous print file remains selected; reset/unload it first')
        path = self.registry / RETENTION_LOCK_NAME
        fd = None
        try:
            fd = os.open(str(path), os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CoordinatorError('Invalid retention ownership lock')
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._print_lock_fd = fd
            return
        except (OSError, CoordinatorError) as exc:
            if fd is not None:
                os.close(fd)
            raise gcmd.error('RC16 print delayed while retention owns files: ' + str(exc))

    def _schedule_print_lock_reaper(self):
        if self._print_lock_fd is None:
            return
        reactor = self.printer.get_reactor()
        when = reactor.monotonic() + 2.
        if self._print_lock_timer is None:
            self._print_lock_timer = reactor.register_timer(self._print_lock_tick, when)
        else:
            reactor.update_timer(self._print_lock_timer, when)

    def _guard_sd_open(self, gcmd):
        if not self.installed or not self._sd_open_permitted:
            raise gcmd.error('RC16: direct virtual-SD start blocked; use RatOS PROCESS_GCODE_FILE')
        # One-use permission: a second open requires fresh authorisation.
        self._sd_open_permitted = False
        return self._native_sd_open(gcmd)

    def _authorised_sd_open(self, callback, gcmd):
        if self._sd_open_permitted:
            raise CoordinatorError('Nested virtual-SD file-open authorisation')
        self._sd_open_permitted = True
        try:
            return callback(gcmd)
        finally:
            self._sd_open_permitted = False

    def _protected_resume_pending(self):
        session = getattr(self.gate, '_protected_session', None)
        return (isinstance(session, dict)
                and (session.get('invalid') or session.get('phase') in (
                    'PREPARING', 'RETRY_REQUIRED', 'MANUAL_REQUIRED',
                    'MANUAL_ACTIVE', 'MANUAL_AUTHORIZED', 'AUTO_VERIFIED')))

    def _guard_do_resume(self, *args, **kwargs):
        # Klipper or extensions may call this Python API directly rather
        # than using M24; enforce the SAME held-file release permission.
        if self._protected_resume_pending():
            if not self._protected_resume_granted:
                raise CoordinatorError('RC16 held print: direct do_resume bypass refused')
            self._protected_resume_granted = False
        return self._native_do_resume(*args, **kwargs)

    def cmd_m24(self, gcmd):
        if self._protected_resume_pending():
            raise gcmd.error(
                'RC16 held print: M24 bypass refused; use guarded '
                'AUTOXY_PRINT_CONTINUE after recovery')
        return self._native_m24(gcmd)

    def release_protected_m24(self, gcmd):
        """Invoke native M24 only for the exact gate-approved held session."""
        session = getattr(self.gate, '_protected_session', None)
        if (not self.installed or not isinstance(session, dict)
                or session.get('invalid')
                or session.get('phase') not in ('MANUAL_AUTHORIZED',
                                                'AUTO_VERIFIED')):
            raise gcmd.error('RC16: no authorised held-print continuation')
        if self.ratos.v_sd.do_resume != self._guard_do_resume:
            raise gcmd.error('RC16 resume API guard was replaced; continuation refused')
        self._protected_resume_granted = True
        try:
            return self._native_m24(gcmd)
        finally:
            self._protected_resume_granted = False

    def cmd_direct_base(self, gcmd):
        # A direct primitive invocation skips RatOS and hides IDEX mode.
        # Reject instead of silently running an independent two-tool job.
        # Normal Mainsail starts go through the public RatOS macro and are
        # unchanged for single-tool, COPY and MIRROR files.
        raise gcmd.error('RC16 protected mode: use normal SDCARD_PRINT_FILE through RatOS, not SDCARD_PRINT_FILE_BASE')

    def cmd_direct_selection(self, gcmd):
        raise gcmd.error('RC16 protected mode: direct SD selection/seeking blocked; use normal RatOS file start')

    def _wait_worker(self, action, root, filename, mode=None, expected_hash=None, original=None, source_hash=None):
        argv = [self.worker_python, str(self.worker), action,
                '--root', str(root), '--source', filename]
        if mode is not None:
            argv.extend(['--mode', mode])
        if expected_hash is not None:
            argv.extend(['--expected-sha256', expected_hash])
        if action in ('stage', 'prepare', 'register', 'resolve'):
            argv.extend(['--registry', str(self.registry)])
        if original is not None:
            argv.extend(['--original', original])
        if source_hash is not None:
            argv.extend(['--source-sha256', source_hash])
        # Never inherit RatOS's resource settings: only the worker lowers its
        # OWN priority, and RatOS is called separately from the coordinator.
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
                        'verify': 'VERIFIED_NOT_AUTHORIZED',
                        'register': 'REGISTERED_NOT_AUTHORIZED',
                        'resolve': 'RESOLVED_NOT_AUTHORIZED'}[action]
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
            # Do not leak pipes into a long-running Klipper process.
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()

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
        if self.ratos.v_sd.cmd_SDCARD_PRINT_FILE != self._guard_sd_open:
            raise gcmd.error('RC16 direct virtual-SD guard was replaced; print refused')
        # A cleanup worker may be deleting an expired generated file. Refuse
        # the new start rather than racing its file lifecycle. Retry normally.
        if self._retention_proc is not None and self._retention_proc.poll() is None:
            raise gcmd.error('RC16 automatic retention in progress; retry print start')
        self.busy = True
        stage_name = None
        handed_to_sd = False
        owns_print_lock = False
        try:
            # COPY/MIRROR cannot require independent nozzle XY alignment.
            # Preserve native RatOS processing end-to-end with no AutoXY file
            # copies, digests or subprocesses. Classify BEFORE RatOS startup
            # temporarily resets the carriage to single mode.
            initial_mode = self._mode()
            if initial_mode in ('copy', 'mirror'):
                # Previously generated RC16 files may carry independent
                # two-tool calibration macros. Never allow a mode change to
                # bypass their original-source verification.
                original_name = gcmd.get('FILENAME', '')
                if Path(original_name).name.startswith('__AutoXY_RC16_'):
                    raise CoordinatorError(
                        'Generated RC16 file requires independent source verification; choose original G-code')
                return self._authorised_sd_open(self._native_processor, gcmd)
            # This shared lock is held for the *entire* generated-file print,
            # not just staging. Retention takes an exclusive lock and cannot
            # delete a selected/paused/printing generated G-code file.
            self._acquire_print_lock(gcmd)
            owns_print_lock = True
            # Native RatOS bypass/transform options cannot guarantee safe
            # independent IDEX preparation; do not guess an unseen print mode.
            if self.ratos.bypass_post_processing or not self.ratos.enable_gcode_transform:
                raise CoordinatorError('Native RatOS transform or postprocessor bypass is enabled')
            root = Path(self.ratos.sdcard_dirname)
            original = gcmd.get('FILENAME', '')
            # Reprints of generated files must be independently verified and
            # mapped back to the ORIGINAL, which is freshly prepared.
            if Path(original).name.startswith('__AutoXY_RC16_'):
                resolved = self._wait_worker('resolve', root, original)
                if resolved.get('stage_name') != original:
                    raise CoordinatorError('Reprint registry resolution mismatch')
                original = resolved.get('source')
                if not isinstance(original, str) or not original:
                    raise CoordinatorError('Missing safe reprint origin')
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
            if mode != initial_mode:
                raise CoordinatorError('IDEX mode changed during file preparation')
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
            # Establish reprint identity in the low-priority worker BEFORE SD
            # opens the file; reject if registry persistence fails.
            registered = self._wait_worker(
                'register', root, stage_name, expected_hash=final_hash,
                original=original, source_hash=staged.get('source_sha256'))
            if registered.get('stage_name') != stage_name or registered.get('output_sha256') != final_hash:
                raise CoordinatorError('Registry entry mismatch; no print')
            # The registry worker has exited. Recheck the exact inode and
            # generation immediately before opening virtual SD, not merely
            # before registry persistence (which can take time on a busy Pi).
            final_stat = path.stat()
            if (path.is_symlink() or
                    [final_stat.st_dev, final_stat.st_ino, final_stat.st_size,
                     final_stat.st_mtime_ns, final_stat.st_ctime_ns] != verified.get('fingerprint')):
                raise CoordinatorError('Prepared file changed after reprint registration')
            # Arm the one-use protected controller ONLY for eligible dual-tool
            # independent prints (the six injected macros enforce the hold).
            result = self._authorised_sd_open(
                self.ratos.v_sd.cmd_SDCARD_PRINT_FILE,
                FilenameProxy(gcmd, stage_name))
            handed_to_sd = True
            self._schedule_print_lock_reaper()
            return result
        except Exception as exc:
            logging.exception('AutoXY RC16 guarded print preparation refused')
            raise gcmd.error('AutoXY RC16 print not started: ' + str(exc))
        finally:
            self.busy = False
            # If native SD never accepted the file, there is no held print.
            # If it did accept despite an exception, retain the lock and use
            # the timer/status gate; never guess based on a return value alone.
            if owns_print_lock and not handed_to_sd:
                try:
                    status = self.ratos.v_sd.get_status(
                        self.printer.get_reactor().monotonic())
                    if status.get('is_active') is False and status.get('file_path') in (None, ''):
                        self._release_print_lock()
                    else:
                        self._schedule_print_lock_reaper()
                except Exception:
                    logging.exception('RC16 could not prove rejected SD was closed; keeping lock')
            # Keep staged files, even on failure: a timed-out worker can still
            # be exiting, and cleanup/ownership is not yet release-qualified.
            if stage_name and not handed_to_sd:
                logging.info('AutoXY RC16 rejected candidate retained; not opened by virtual SD')


def load_config(config):
    return AutoXYRC16Handoff(config)
