# AutoXY v0.5.0-rc15-development unified RatOS 2.1 IDEX calibration and guarded recovery controller.
# Based on the validated Stage 6.3.7 camera/offset engine and the verified manual-recovery flow guarded print path.
# GPLv3-compatible extension: no third-party dependencies in Klipper Python.
# Camera/OpenCV execution occurs in a separate venv subprocess while the
# Klipper reactor is yielded, so MCU communications remain serviced.
import json, math, os, subprocess, time, logging, sys, csv, io, html

RELEASE_VERSION = "0.5.0-rc15-dev5"

class GateAbort(Exception):
    pass


class VisionWorkerError(Exception):
    """Transport/protocol failure, not a printer-safety event."""
    pass


def vecsub(a,b): return [a[i]-b[i] for i in range(2)]
def vecadd(a,b): return [a[i]+b[i] for i in range(2)]
def vecmul(a,k): return [x*k for x in a]
def maximum(a): return max(abs(a[0]),abs(a[1]))
def norm(a): return math.hypot(a[0],a[1])
def det(m): return m[0][0]*m[1][1]-m[0][1]*m[1][0]
def solve(m,v):
    d=det(m)
    if abs(d)<100.: raise GateAbort('Camera matrix nearly singular')
    return [(v[0]*m[1][1]-v[1]*m[0][1])/d,
            (m[0][0]*v[1]-m[1][0]*v[0])/d]
def avg(vs): return [sum(x[i] for x in vs)/len(vs) for i in (0,1)]


class _RetryGcmd:
    """Reuse the same tested gate with an explicit idle mode and real GCode response."""
    def __init__(self, source, mode='IDLE'):
        self.source = source
        self.mode = mode
    def get(self, key, default=None):
        return self.mode if key == 'MODE' else self.source.get(key, default)
    def respond_info(self, message):
        return self.source.respond_info(message)
    def error(self, message):
        return self.source.error(message)


def slicer_nozzle_targets(text, reductions, max_target):
    """Return per-tool nozzle targets; reject missing/unsafe slicer metadata.

    Do not silently clamp to max_target, as that would defeat the agreed
    reduction relative to each tool's own printing temperature.
    """
    parts=str(text).split(',')
    if len(parts)!=2:
        raise ValueError('Need two EXTRUDER_OTHER_LAYER_TEMP values (T0,T1)')
    try:
        printing=[float(x.strip()) for x in parts]
    except ValueError:
        raise ValueError('Invalid slicer printing temperature values')
    if not all(math.isfinite(x) and 100.<=x<=350. for x in printing):
        raise ValueError('Slicer printing temperatures outside plausible range')
    target=[printing[i]-reductions[i] for i in (0,1)]
    if any(x<60. or x>max_target for x in target):
        raise ValueError('Derived camera target outside 60-%.1f C; adjust reductions or camera cap' % max_target)
    return printing,target


class AutoXYGate:
    cmd_AUTO_XY_GATE_help='Run gated IDEX camera alignment only in independent mode'
    cmd_AUTO_XY_CAPTURE_MODE_help='Capture RatOS print mode and toolshift count before carriage reset'
    cmd_AUTO_XY_HEALTH_help='Check AutoXY Klipper integration and saved enable flag'
    cmd_AUTO_XY_RETRY_ONCE_help='Retry ONE recoverable calibration failure in the same active VAOC session after print abort'
    cmd_AUTO_XY_ENDURANCE_RUN_help='Supervised auto-calibration followed by diagnostic T0/T1 endurance; never prints' 
    def __init__(self,config):
        self.printer=config.get_printer()
        self.reactor=self.printer.get_reactor()
        self.gcode=self.printer.lookup_object('gcode')
        self.python=config.get('python','/home/pi/auto-xy-venv/bin/python')
        self.worker=config.get('worker','/home/pi/auto_xy/vision/auto_xy_vision_worker.py')
        self.results=os.path.expanduser(config.get('results','/home/pi/auto_xy/print_gate_runs'))
        self.camera=config.get('camera_url','http://127.0.0.1:8080/?action=snapshot')
        self.roi=config.get('roi','auto')
        # RC15: opt-in to persistent subprocess. Legacy spawning is the safe default.
        self.camera_worker_mode=config.get('camera_worker_mode','legacy').strip().lower()
        # RC15 dev4: dual independent detector, strictly opt-in and fail-closed.
        self.camera_detector_mode=config.get('camera_detector_mode','legacy').strip().lower()
        if self.camera_detector_mode not in ('legacy','two_stage','two_stage_roi'):
            raise config.error('camera_detector_mode must be legacy, two_stage or two_stage_roi')
        if self.camera_worker_mode not in ('legacy','persistent'):
            raise config.error('camera_worker_mode must be legacy or persistent')
        self._vision_proc=None
        self._vision_request_id=0
        self._vision_session_invalid=False
        # Protected print mode; enabled only after supervised validation.
        self.camera_registration_enabled=config.getboolean('camera_registration_enabled',False)
        self.protected_print_enabled=config.getboolean('protected_print_enabled',False)
        self._protected_session=None
        self._protected_inside_gate=False
        # Display-only progress messages.  Never used by safety decisions.
        self._progress_stage=None
        # the verified manual-recovery flow: UI-only supervised manual-VAOC return prompt.  No motion,
        # heaters, offsets, queue release or calibration are performed by this
        # timer; the existing the earlier protected-print revision gate still controls the held file.
        if self.protected_print_enabled:
            self.reactor.register_timer(self._protected_manual_popup_tick,
                                        self.reactor.monotonic()+2.)
        self.tol=config.getfloat('tolerance',.030,above=.009,below=.061)
        self.max_correct=config.getint('max_corrections',10,minval=1,maxval=10)
        self.max_verify=config.getint('max_verifications',6,minval=2,maxval=6)
        # No Mainsail G-code prompt can be serviced while START_PRINT's
        # synchronous gate owns the G-code command queue. Permit precisely
        # one recoverable in-place replay instead; never loop indefinitely.
        self.inline_print_retry=config.getboolean('inline_print_retry',False)
        self.max_step=config.getfloat('max_step',.05,above=.005,maxval=.05)
        self.first_y_max_step=config.getfloat('first_y_max_step',.020,above=.001,maxval=.05)
        self.max_total=config.getfloat('max_total_change',.3,above=.05,maxval=.3)
        self.settle=config.getfloat('settle',.8,above=.2,maxval=3)
        # RC14: experimental positioning choices, default to the proven RC13 path.
        self.settling_path=config.get('settling_path','original').strip().lower()
        if self.settling_path not in ('original','diagonal','dogleg'):
            raise config.error('settling_path must be original, diagonal or dogleg')
        self.camera_pair_gap=config.getfloat('camera_pair_gap',.8,above=.2,maxval=3.)
        # RC15 dev2: report-only timestamp tracing; never changes motion, delays or acceptance.
        self.camera_diagnostics_enabled=config.getboolean('camera_diagnostics_enabled',False)
        self._diag_last_motion_completed_s=None
        self._diag_previous_snapshot_started_s=None
        self._endurance_request=None
        self._endurance_active=False
        self.commissioned_detector_guard=config.getboolean('commissioned_detector_guard',False)
        if self.camera_detector_mode in ('two_stage','two_stage_roi') and not self.commissioned_detector_guard:
            raise config.error('two_stage detector requires commissioned_detector_guard: True')
        self.allow_experimental_alignment_writes=config.getboolean('allow_experimental_alignment_writes',False)
        # Verify repeatable tool seating BEFORE any saved-offset write.
        # Each measurement performs T0 -> T1 -> T0; a stable consecutive pair
        # is required, with a bounded budget to fail safely if not settled.
        self.preflight_settling_checks=config.getint('preflight_settling_checks',4,minval=2,maxval=6)
        self.preflight_settling_tolerance=config.getfloat('preflight_settling_tolerance',.015,above=.004,maxval=.03)
        self.timeout=config.getfloat('snapshot_timeout',7.,above=2.,maxval=12.)
        self.max_camera_nozzle_temp=config.getfloat('max_camera_nozzle_temp',155.,above=100.,maxval=250.)
        self.max_camera_target_temp=config.getfloat('max_camera_target_temp',150.,above=100.,maxval=250.)
        # Optional thermal-equivalence commissioning for RatOS configurations
        # which explicitly preheat BOTH nozzles; never issue heater commands.
        self.preflight_thermal_wait_seconds=config.getfloat('preflight_thermal_wait_seconds',90.,minval=0.,maxval=300.)
        # Explicit opt-in: slicer printing temperatures MINUS each tool's own
        # configured reduction. No unapproved heating in standard RatOS mode.
        self.thermal_mode=config.get('thermal_mode','ratos').strip().lower()
        if self.thermal_mode not in ('ratos','slicer_derived'):
            raise config.error('thermal_mode must be ratos or slicer_derived')
        self.thermal_reductions=[config.getfloat('thermal_reduction_t0',50.,minval=20.,maxval=120.),
                                 config.getfloat('thermal_reduction_t1',50.,minval=20.,maxval=120.)]
        self.thermal_band=config.getfloat('thermal_stability_band_c',2.,above=.5,maxval=5.)
        self.thermal_stable_seconds=config.getfloat('thermal_stable_seconds',5.,minval=3.,maxval=15.)
        self.thermal_wait_seconds=config.getfloat('thermal_wait_seconds',300.,minval=30.,maxval=600.)
        self.commission_required=config.getboolean('commission_required',True)
        # Required for fresh installs; older hardware-validated RC7 configs can
        # retain their existing photo profile during a guarded upgrade.
        self.manual_vaoc_confirmation_required=config.getboolean('manual_vaoc_confirmation_required',False)
        self.commission_profile=os.path.expanduser(config.get('commission_profile','/home/pi/auto_xy/camera_commission.json'))
        self._commission_data=None
        self._slicer_printing_temps=None
        self._slicer_camera_targets=None
        self._original_heater_targets=None
        self._thermal_snapshot=None

        self.require_matched_camera_temps=config.getboolean('require_matched_camera_temps',False)
        self.camera_temp_pair_tolerance=config.getfloat('camera_temp_pair_tolerance',2.,above=.2,maxval=10.)
        # Optional in-RAM reuse of a verified calibration. Thermal limits are
        # deliberately strict: a cold manual measurement cannot be reused
        # after a hot bed heatsoak without a new camera verification.
        self.reuse_max_bed_delta=config.getfloat('reuse_max_bed_temp_delta',5.,minval=0.,maxval=15.)
        self.reuse_max_nozzle_delta=config.getfloat('reuse_max_nozzle_temp_delta',10.,minval=0.,maxval=25.)
        # Persistent log/image retention is independent of the calibration state.
        self.retention_success_days=config.getint('retention_success_images_days',7,minval=1,maxval=3650)
        self.retention_failed_days=config.getint('retention_failed_images_days',30,minval=1,maxval=3650)
        self.retention_report_days=config.getint('retention_reports_days',90,minval=1,maxval=3650)
        if not (self.retention_success_days <= self.retention_failed_days <= self.retention_report_days):
            raise config.error('Require success image days <= failure image days <= report days')
        self._retention_proc=None
        # Klipper reactor timer only launches a stdlib-only maintenance process
        # when printer is idle. Never runs or blocks a camera measurement.
        self.reactor.register_timer(self._retention_tick, self.reactor.monotonic()+300.)
        self.temp_guard_active=False
        self.busy=False
        self._retry_precommitted=False
        self._retry_context=None  # RAM-only: invalidated by Klipper restart
        self.pending_print_mode=None  # one-use START_PRINT mode + toolshift count, pre RatOS _IDEX_SINGLE
        self.pending_toolshifts=None
        self.active_print_mode=None  # used solely to validate the immediate inline retry
        self.verified_session=None # RAM ONLY: never trust save_variables for calibration validity
        self.pending_reuse_candidate=False
        self.reuse_invalid_reason='No completed verified calibration in this Klipper process'
        self._reuse_motor_callbacks_ready=False
        # Klipper does not clear homed_axes on every individually disabled
        # motor. Invalidate on physical disable as well as rehome/restart.
        for event, handler in (
            ('stepper_enable:motor_off',self._on_reuse_invalidation),
            ('homing:home_rails_begin',self._on_reuse_invalidation),
            ('gcode:request_restart',self._on_reuse_invalidation),
            ('klippy:shutdown',self._on_reuse_invalidation),
            ('klippy:ready',self._register_motor_callbacks)):
            register=getattr(self.printer,'register_event_handler',None)
            if register is not None:
                register(event,handler)
        self.phase='IDLE'
        self.last_error=''
        self.gcode.register_command('AUTO_XY_CAPTURE_MODE',self.cmd_AUTO_XY_CAPTURE_MODE,desc=self.cmd_AUTO_XY_CAPTURE_MODE_help)
        self.gcode.register_command('AUTO_XY_GATE',self.cmd_AUTO_XY_GATE,desc=self.cmd_AUTO_XY_GATE_help)
        self.gcode.register_command('AUTO_XY_HEALTH',self.cmd_AUTO_XY_HEALTH,desc=self.cmd_AUTO_XY_HEALTH_help)
        self.gcode.register_command('AUTO_XY_RETRY_ONCE',self.cmd_AUTO_XY_RETRY_ONCE,desc=self.cmd_AUTO_XY_RETRY_ONCE_help)
        self.gcode.register_command('AUTO_XY_ENDURANCE_RUN',self.cmd_AUTO_XY_ENDURANCE_RUN,desc=self.cmd_AUTO_XY_ENDURANCE_RUN_help)
        # Protected pre-Beacon two-phase G-code-file flow.
        # No alteration of normal RatOS START_PRINT or the previous calibration engine behavior.
        for name,fn in (
            ('AUTOXY_PRINT_BEGIN',self.cmd_AUTOXY_PRINT_BEGIN),
            ('AUTOXY_PRINT_CALIBRATE',self.cmd_AUTOXY_PRINT_CALIBRATE),
            ('AUTOXY_PRINT_HOLD',self.cmd_AUTOXY_PRINT_HOLD),
            ('AUTOXY_PRINT_RETRY',self.cmd_AUTOXY_PRINT_RETRY),
            ('AUTOXY_PRINT_CHECK',self.cmd_AUTOXY_PRINT_CHECK),
            ('AUTOXY_MANUAL_START',self.cmd_AUTOXY_MANUAL_START),
            ('AUTOXY_MANUAL_ACCEPT',self.cmd_AUTOXY_MANUAL_ACCEPT),
            ('AUTOXY_MANUAL_ACCEPT_CONTINUE',self.cmd_AUTOXY_MANUAL_ACCEPT_CONTINUE),
            ('AUTOXY_RECOVERY_SHOW',self.cmd_AUTOXY_RECOVERY_SHOW),
            ('AUTOXY_PRINT_CONTINUE',self.cmd_AUTOXY_PRINT_CONTINUE),
            ('AUTOXY_PRINT_FINISH_GATE',self.cmd_AUTOXY_PRINT_FINISH_GATE),
            ('AUTOXY_PRINT_STATUS',self.cmd_AUTOXY_PRINT_STATUS),
            ('AUTOXY_RELEASE_STATUS',self.cmd_AUTOXY_RELEASE_STATUS)):
            self.gcode.register_command(name,fn)


    def _invalidate_verified_session(self, reason):
        self.verified_session=None
        self.pending_reuse_candidate=False
        self.reuse_invalid_reason=reason

    def _on_reuse_invalidation(self,*args):
        self._invalidate_verified_session('Motor disable, rehome, restart or shutdown')
        self._invalidate_held_recovery('Motor disable, rehome, restart or shutdown')
        if self.busy:
            self._vision_session_invalid=True
        # Shutdown/rehome/motor-off invalidates all outstanding camera replies.
        self._stop_vision_process()

    def _invalidate_held_recovery(self,reason):
        # Homing during PREPARING is normal; after calibration starts, any
        # motor-off or rehome destroys the reference used for guarded release.
        s=getattr(self,'_protected_session',None)
        if s and s.get('phase') not in ('PREPARING','FINISHING','GUARD_CONSUMED'):
            s['invalid']=True
            s['invalidation_reason']=reason

    def _on_individual_motor_state(self, print_time, is_enabled):
        if not is_enabled:
            if self.busy:
                self._vision_session_invalid=True
                self._stop_vision_process()
            self._invalidate_verified_session('A motion stepper was disabled')
            self._invalidate_held_recovery('Motion stepper disabled')

    def _register_motor_callbacks(self,*args):
        if self._reuse_motor_callbacks_ready:
            return
        obj=self.printer.lookup_object('stepper_enable',None)
        if obj is None or not hasattr(obj,'lookup_enable'):
            return
        # Subscribe to all actual axis motors, including independent IDEX
        # carriage motors. If any callback cannot be registered, reuse is
        # disabled; normal calibration continues unaffected.
        try:
            steppers=obj.get_status(self.reactor.monotonic())['steppers']
            names=[name for name in steppers if name.startswith('stepper_')]
            if not names:
                return
            for name in names:
                obj.lookup_enable(name).register_state_callback(self._on_individual_motor_state)
            self._reuse_motor_callbacks_ready=True
        except Exception:
            logging.exception('Auto XY: cannot monitor motors for calibration reuse')
            self._reuse_motor_callbacks_ready=False

    def _motion_motor_signature(self):
        # Merely checking homed_axes is unsafe: SET_STEPPER_ENABLE can turn
        # off one stepper without updating Klipper's homing status.
        if not self._reuse_motor_callbacks_ready:
            return None
        status=self.sts('stepper_enable').get('steppers',{})
        motion={name:bool(on) for name,on in status.items() if name.startswith('stepper_')}
        if not motion or not all(motion.values()):
            return None
        return tuple(sorted(motion))

    def _reuse_thermal_state(self):
        bed=float(self.sts('heater_bed')['temperature'])
        t0=float(self.sts('extruder')['temperature'])
        t1=float(self.sts('extruder1')['temperature'])
        if not all(math.isfinite(x) for x in (bed,t0,t1)):
            raise GateAbort('Nonfinite temperature for reuse')
        return (bed,t0,t1)

    def _store_verified_session(self):
        # Called only after complete calibration, two verified alignment
        # measurements, safe VAOC exit and cleared persistent fault markers.
        self._invalidate_verified_session('No reusable stepper/thermal proof')
        if not self._reuse_motor_callbacks_ready:
            return
        try:
            homed=str(self.sts('toolhead').get('homed_axes',''))
            motors=self._motion_motor_signature()
            if not all(axis in homed for axis in 'xyz') or motors is None:
                return
            if self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
                return
            self.verified_session={'offsets':tuple(self.offsets()),
                                   'motors':motors,
                                   'temps':self._reuse_thermal_state(),
                                   'completed_at':time.time(),
                                   'report':self.directory}
            self.reuse_invalid_reason=''
        except Exception:
            logging.exception('Auto XY could not create verified-session proof; reuse unavailable')

    def _reuse_check(self, check_thermal=True):
        token=self.verified_session
        if not token:
            return False,self.reuse_invalid_reason
        try:
            if self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
                self._invalidate_verified_session('Auto XY fault/interrupted run')
                return False,self.reuse_invalid_reason
            if not all(a in str(self.sts('toolhead').get('homed_axes','')) for a in 'xyz'):
                self._invalidate_verified_session('XYZ no longer homed')
                return False,self.reuse_invalid_reason
            if self._motion_motor_signature()!=token['motors']:
                self._invalidate_verified_session('Motor enable state changed')
                return False,self.reuse_invalid_reason
            if any(abs(a-b)>.0001 for a,b in zip(self.offsets(),token['offsets'])):
                self._invalidate_verified_session('Saved XY/Z offsets changed')
                return False,self.reuse_invalid_reason
            if check_thermal:
                temps=self._reuse_thermal_state()
                if abs(temps[0]-token['temps'][0])>self.reuse_max_bed_delta:
                    return False,'Bed temperature changed since calibration'
                if any(abs(temps[i]-token['temps'][i])>self.reuse_max_nozzle_delta for i in (1,2)):
                    return False,'Nozzle temperature changed since calibration'
            return True,'Verified calibration and live conditions unchanged'
        except (Exception,KeyboardInterrupt) as exc:
            self._invalidate_verified_session('Required reuse state unavailable: '+str(exc))
            return False,self.reuse_invalid_reason

    def reuse_enabled(self):
        return self.flag('auto_xy_v2_reuse_enabled')

    def _retention_tick(self, eventtime):
        interval=24*3600.
        try:
            if self.busy:
                return eventtime+3600.
            stats=self.sts('print_stats')
            if str(stats.get('state','')).lower() in ('printing','paused'):
                return eventtime+3600.
            sd=self.printer.lookup_object('virtual_sdcard',None)
            if sd and sd.get_status(eventtime).get('is_active',False):
                return eventtime+3600.
            if self._retention_proc and self._retention_proc.poll() is None:
                return eventtime+3600.
            script=os.path.join(os.path.dirname(self.worker),'auto_xy_retention.py')
            if not os.path.isfile(script):
                logging.warning('Auto XY retention worker missing: %s',script)
                return eventtime+interval
            self._retention_proc=subprocess.Popen([sys.executable,script,'--run','--results',self.results,
                '--success-days',str(self.retention_success_days),
                '--failed-days',str(self.retention_failed_days),
                '--report-days',str(self.retention_report_days)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
        except Exception:
            logging.exception('Auto XY retention could not start; calibration continues unchanged')
        return eventtime+interval

    def ensure_commissioned(self,gcmd):
        """Early no-motion profile check; never equate image quality with VAOC physical alignment."""
        if not self.commission_required:return
        try:
            with open(self.commission_profile,'r') as f:profile=json.load(f)
            if profile.get('status')!='COMMISSIONED_FROM_IMAGES' or profile.get('format')!=1:
                raise ValueError('Unrecognised camera commissioning profile')
            if self.manual_vaoc_confirmation_required and profile.get('manual_vaoc_confirmed') is not True:
                raise ValueError('Operator has not confirmed successful native manual VAOC')
            if profile.get('camera_url')!=self.camera or profile.get('roi')!=self.roi:
                raise ValueError('Camera URL/ROI changed since commissioning')
            for tool in ('tool0','tool1'):
                entry=profile[tool]
                if entry['num_images']<3 or not (115.<=float(entry['radius_px'])<=185.):
                    raise ValueError('Invalid '+tool+' commissioning data')
                if self.manual_vaoc_confirmation_required:
                    center=entry.get('registration_center_px')
                    if (not isinstance(center,(list,tuple)) or len(center)!=2 or
                            not all(math.isfinite(float(v)) for v in center)):
                        raise ValueError('First-use profile lacks local nozzle centre: '+tool)
            self._commission_data=profile
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise gcmd.error('AUTO XY camera commissioning REQUIRED before automatic measurement: '+str(exc)+
                '; see docs/CAMERA_COMMISSIONING.md. No calibration started.')

    def cmd_AUTO_XY_CAPTURE_MODE(self,gcmd):
        """Capture BOTH original IDEX mode and total toolchanges at START_PRINT.

        RatOS's START_PRINT uses TOTAL_TOOLSHIFTS>0 to determine whether both
        heads will print in independent mode. The active carriage at the
        later calibration hook is always independent, even for COPY/MIRROR.
        For missing TOTAL_TOOLSHIFTS we match RatOS's default(0), logging
        the distinction; an absent count must never trigger a calibration.
        """
        if self.busy:
            raise gcmd.error('Cannot capture IDEX mode while Auto XY calibration is busy')
        # RatOS calls this hook BEFORE bed heating. Fail early for an
        # unresolved calibration fault even if the operator switched AutoXY OFF.
        if self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
            raise gcmd.error('AUTO XY safety fault/interrupted calibration: recover and acknowledge BEFORE heating the bed')
        self.pending_print_mode=None
        self.pending_toolshifts=None
        self.pending_reuse_candidate=False
        # Console-started START_PRINT and native _VAOC_START must not impersonate
        # a sliced file, even when RatOS supplies TOTAL_TOOLSHIFTS=1.
        # Only virtual-SD print jobs participate in automatic gating.
        if not self.virtual_sd_active():
            gcmd.respond_info('AUTO XY: START_PRINT is not an active virtual-SD print; skipping automatic calibration')
            return
        status=str(self.sts('dual_carriage').get('carriage_1','')).lower()
        if status not in ('copy','mirror','inactive','primary','parked'):
            if not self.enabled():
                gcmd.respond_info('AUTO XY OFF: unknown carriage state ignored; no capture needed')
                return
            raise gcmd.error('Unknown RatOS dual_carriage mode at START_PRINT: '+status)
        raw=gcmd.get('TOTAL_TOOLSHIFTS','0')
        try:
            toolshifts=int(raw)
        except (ValueError, TypeError):
            raise gcmd.error('AUTO XY: invalid TOTAL_TOOLSHIFTS value: '+str(raw))
        if toolshifts<0:
            raise gcmd.error('AUTO XY: negative TOTAL_TOOLSHIFTS is invalid')
        if status in ('copy','mirror'):
            requested=status.upper()
        elif toolshifts==0:
            requested='SINGLE_TOOL'
        else:
            requested='INDEPENDENT'
        if requested=='INDEPENDENT' and self.enabled():
            # Safe by design: ordinary slicer START_PRINT cannot enter the
            # legacy inline auto-retry/abort path, which cannot offer native
            # manual VAOC while preserving the original virtual-SD file.
            # A converted protected G-code calls AUTOXY_PRINT_BEGIN first.
            if self.protected_print_enabled:
                session=self._protected_session
                if (not session or session.get('invalid') or
                    session.get('phase')!='PREPARING'):
                    raise gcmd.error(
                        'AutoXY: unprotected two-tool G-code refused before bed heat. '
                        'Convert the original slicer file using '
                        'src/tools/convert_two_tool_print.py. '
                        'No calibration was started; original file was not made resumable.')
            self.ensure_commissioned(gcmd)
        self.pending_print_mode=requested
        self.pending_toolshifts=toolshifts
        self._slicer_printing_temps=None
        self._slicer_camera_targets=None
        if requested=='INDEPENDENT' and self.thermal_mode=='slicer_derived' and self.enabled():
            try:
                printing,targets=slicer_nozzle_targets(
                    gcmd.get('EXTRUDER_OTHER_LAYER_TEMP',''),self.thermal_reductions,
                    self.max_camera_target_temp)
                if max(targets)+self.thermal_band>self.max_camera_nozzle_temp:
                    raise ValueError('Measured-nozzle cap is below derived target plus stability band')
                if self.require_matched_camera_temps and abs(targets[0]-targets[1])>self.camera_temp_pair_tolerance:
                    raise ValueError('Slicer-derived independent targets conflict with require_matched_camera_temps')
                self._slicer_printing_temps=printing
                self._slicer_camera_targets=targets
            except ValueError as exc:
                self.pending_print_mode=None
                self.pending_toolshifts=None
                raise gcmd.error('AUTO XY thermal setup: %s. Refusing print BEFORE heatsoak.'%exc)
        if requested=='INDEPENDENT' and self.reuse_enabled() and self.enabled() and not self.flag('auto_xy_v2_measure_only'):
            candidate,_=self._reuse_check(check_thermal=False)
            self.pending_reuse_candidate=candidate
            if candidate:
                gcmd.respond_info('AUTO XY: prior verified calibration eligible for reuse; will recheck after heatsoak')
        gcmd.respond_info('AUTO XY: START_PRINT captured %s; TOTAL_TOOLSHIFTS=%d%s' % (
            requested,toolshifts,' (single-tool bypass)' if requested=='SINGLE_TOOL' else ''))

    def sts(self,name):
        obj=self.printer.lookup_object(name,None)
        if obj is None: raise GateAbort('Klipper object missing: '+name)
        return obj.get_status(self.reactor.monotonic())
    def virtual_sd_active(self):
        sd=self.printer.lookup_object('virtual_sdcard',None)
        return bool(sd and sd.get_status(self.reactor.monotonic()).get('is_active',False))
    def variables(self): return self.sts('save_variables')['variables']
    def offsets(self):
        v=self.variables()
        return [float(v[k]) for k in ('idex_xoffset','idex_yoffset','idex_zoffset')]
    def enabled(self):
        value=self.variables().get('auto_xy_v2_enabled',0)
        return str(value).strip().lower() in ('1','1.0','true')
    def cp(self):
        v=self.variables()
        return [float(v[k]) for k in ('idex_xcontrolpoint','idex_ycontrolpoint','idex_zcontrolpoint')]
    def xyz(self): return [float(x) for x in self.sts('gcode_move')['gcode_position'][:3]]
    def tool(self):
        mode=str(self.sts('dual_carriage').get('carriage_1','')).lower()
        if mode=='primary': return 1
        if mode in ('inactive','parked'): return 0
        raise GateAbort('Expected independent IDEX mode, found '+mode)
    def run_gcode(self,script):
        # Unlike Moonraker /printer/gcode/script this runs from the current
        # Klipper command, so there is no API round-trip / queue deadlock.
        self.gcode.run_script_from_command(script+'\nM400')
    def pause(self,seconds):
        deadline=self.reactor.monotonic()+seconds
        while self.reactor.monotonic()<deadline:
            self.reactor.pause(min(deadline,self.reactor.monotonic()+.15))
    def ensure_ok(self):
        if self._vision_session_invalid:
            raise GateAbort('Motor disabled, rehome or Klipper invalidated active calibration')
        if self.printer.get_state_message()[1]!='ready': raise GateAbort('Klipper no longer ready')
        if getattr(self,'deadline',float('inf'))<self.reactor.monotonic():
            raise GateAbort('Endurance watchdog elapsed' if getattr(self,'phase',None)=='ENDURANCE' else '15-minute calibration watchdog elapsed')
        if not self.enabled(): raise GateAbort('AUTO_XY_OFF selected during calibration')
        if self.temp_guard_active:
            self.check_camera_temps()
        axes=str(self.sts('toolhead')['homed_axes'])
        if not all(a in axes for a in 'xyz'): raise GateAbort('Axes no longer homed')
    def nozzle_states(self):
        # Read current and requested nozzle temperatures. Reject unexpected
        # final-temperature heating even if the metal has not heated up yet.
        return {name: {
                    'temperature': float(self.sts(name)['temperature']),
                    'target': float(self.sts(name)['target'])}
                for name in ('extruder', 'extruder1')}
    def nozzle_temps(self):
        return {name: state['temperature'] for name, state in self.nozzle_states().items()}
    def check_camera_temps(self):
        states=self.nozzle_states()
        for name,state in states.items():
            if not all(math.isfinite(value) for value in state.values()):
                raise GateAbort('Invalid hotend temperature status: '+name)
            if state['temperature']>self.max_camera_nozzle_temp:
                raise GateAbort('%s exceeds camera temperature cap %.1f C: %.1f C' %
                                (name,self.max_camera_nozzle_temp,state['temperature']))
            if state['target']>self.max_camera_target_temp:
                raise GateAbort('%s heating target exceeds camera preheat cap %.1f C: %.1f C' %
                                (name,self.max_camera_target_temp,state['target']))
        if self.require_matched_camera_temps:
            delta=abs(states['extruder']['temperature']-states['extruder1']['temperature'])
            target_delta=abs(states['extruder']['target']-states['extruder1']['target'])
            if max(delta,target_delta)>self.camera_temp_pair_tolerance:
                raise GateAbort('Camera nozzle temperatures no longer matched: actual delta %.1f C, target delta %.1f C'%(delta,target_delta))
        return states
    def start_slicer_thermal_preflight(self,gcmd):
        """Own two temporary M104 targets, wait for five seconds in band.

        Before VAOC, before busy/in-progress flags, and reversible even on a
        timeout. Only a true PRINT context with opt-in derived mode may enter.
        """
        if self.thermal_mode!='slicer_derived' or self._slicer_camera_targets is None:
            raise gcmd.error('AUTO XY: missing slicer-derived preflight targets; refusing heater changes')
        targets=list(self._slicer_camera_targets)
        states=self.nozzle_states()
        original=[states[name]['target'] for name in ('extruder','extruder1')]
        self._original_heater_targets=original
        stable_since=None
        start=self.reactor.monotonic()
        deadline=start+self.thermal_wait_seconds
        try:
            for i,target in enumerate(targets):
                self.run_gcode('M104 T%d S%.2f'%(i,target))
            while True:
                now=self.reactor.monotonic()
                if self.printer.get_state_message()[1]!='ready':
                    raise gcmd.error('AUTO XY: Klipper not ready during thermal preflight')
                if not all(a in str(self.sts('toolhead').get('homed_axes','')) for a in 'xyz'):
                    raise gcmd.error('AUTO XY: printer must be homed first (G28); no calibration started')
                states=self.nozzle_states()
                temps=[states[name]['temperature'] for name in ('extruder','extruder1')]
                wanted=[states[name]['target'] for name in ('extruder','extruder1')]
                if not all(math.isfinite(t) for t in temps+wanted):
                    raise gcmd.error('AUTO XY: invalid hotend reading during thermal preflight')
                if any(abs(wanted[i]-targets[i])>.5 for i in (0,1)):
                    raise gcmd.error('AUTO XY: other controller changed a heater target during thermal preflight')
                within=all(abs(temps[i]-targets[i])<=self.thermal_band for i in (0,1))
                if within:
                    if stable_since is None:stable_since=now
                    if now-stable_since>=self.thermal_stable_seconds:
                        self._thermal_snapshot={
                            'mode':'slicer_derived','print_temps':list(self._slicer_printing_temps),
                            'camera_targets':list(targets),'measured':temps,
                            'reduction':list(self.thermal_reductions),
                            'stability_seconds':self.thermal_stable_seconds,
                            'stability_band_c':self.thermal_band,
                            'wait_seconds':round(now-start,2),
                            'original_targets':original}
                        gcmd.respond_info('AUTO XY thermal ready: T0 %.1f/%.1fC; T1 %.1f/%.1fC; stable %.1fs'%(
                            temps[0],targets[0],temps[1],targets[1],self.thermal_stable_seconds))
                        return
                else:
                    stable_since=None
                if now>=deadline:
                    raise gcmd.error('AUTO XY: thermal prerequisite timed out: T0 %.1f/%.1fC, T1 %.1f/%.1fC; no calibration fault latched.'%(
                        temps[0],targets[0],temps[1],targets[1]))
                self.reactor.pause(min(deadline,now+.5))
        except Exception:
            self.restore_preflight_heaters()
            raise

    def restore_preflight_heaters(self):
        """Restore RatOS heater targets once after the FINAL calibration attempt."""
        original=self._original_heater_targets
        self._original_heater_targets=None
        if original is None:return
        self.temp_guard_active=False
        for i,target in enumerate(original):
            try:self.run_gcode('M104 T%d S%.2f'%(i,target))
            except Exception:
                logging.exception('AUTO XY could not restore RatOS T%d target %.1fC',i,target)
                raise

    def wait_for_safe_camera_preflight(self,gcmd):
        """Bounded, no-motion/no-heating cooldown before the in-progress marker.

        The 155C measured cap and 150C target cap remain absolute. Owners who
        configure both nozzle targets can opt in to a matched actual and target
        temperature check. We NEVER heat the other hotend automatically.
        """
        deadline=self.reactor.monotonic()+self.preflight_thermal_wait_seconds
        while True:
            if self.printer.get_state_message()[1]!='ready':
                raise gcmd.error('AUTO XY: Klipper no longer ready during thermal preflight')
            if not all(axis in str(self.sts('toolhead').get('homed_axes','')) for axis in 'xyz'):
                raise gcmd.error('AUTO XY: printer must be homed first (G28); no calibration started')
            states=self.nozzle_states()
            if not all(math.isfinite(x) for state in states.values() for x in state.values()):
                raise gcmd.error('AUTO XY: invalid preflight hotend reading')
            for name,state in states.items():
                if state['target']>self.max_camera_target_temp:
                    raise gcmd.error('AUTO XY: %s target %.1fC exceeds camera preheat cap %.1fC; no calibration started'%(
                        name,state['target'],self.max_camera_target_temp))
            hot=[name for name,state in states.items() if state['temperature']>self.max_camera_nozzle_temp]
            delta=abs(states['extruder']['temperature']-states['extruder1']['temperature'])
            target_delta=abs(states['extruder']['target']-states['extruder1']['target'])
            unmatched=(self.require_matched_camera_temps and
                (delta>self.camera_temp_pair_tolerance or target_delta>self.camera_temp_pair_tolerance))
            if not hot and not unmatched:
                return
            if self.reactor.monotonic()>=deadline:
                issue=('hot nozzle(s): '+','.join(hot)) if hot else (
                    'nozzles differ by %.1fC (target difference %.1fC)'%(delta,target_delta))
                raise gcmd.error('AUTO XY: thermal preflight timeout: '+issue+
                    '; adjust RatOS preheat/standby settings and retry. No calibration started or fault latched.')
            self.reactor.pause(min(deadline,self.reactor.monotonic()+.5))

    def prepare_camera_at_preheat(self,gcmd):
        # Keep RatOS's existing preheat state; no heater commands are emitted.
        # This runs only with AUTO XY ON, prior to final print temperature.
        state=self.check_camera_temps()
        self.report['camera_temperature_entry_c']={k:v['temperature'] for k,v in state.items()}
        self.report['camera_temperature_start_c']={k:v['temperature'] for k,v in state.items()}
        self.report['camera_target_start_c']={k:v['target'] for k,v in state.items()}
        self.report['heating_targets_preserved']=self._original_heater_targets is None
        self.save_report()
        self.temp_guard_active=True
        gcmd.respond_info('AUTO XY: %s camera targets monitored (target cap %.1f C; measured cap %.1f C)' %
                          (self.thermal_mode,self.max_camera_target_temp,self.max_camera_nozzle_temp))

    def progress_stage(self,gcmd,stage,detail):
        """Best-effort human-readable status, without modifying motion or gate state.

        Changing the stage emits a single console milestone. Native RatOS
        diagnostics remain available for troubleshooting. A UI failure must
        never change calibration safety behavior.
        """
        if getattr(self,'_progress_stage',None)==stage:
            return
        self._progress_stage=stage
        try:
            gcmd.respond_info('AutoXY | %s: %s' % (stage,detail))
        except Exception:
            logging.exception('AutoXY progress message could not be displayed')

    def save_report(self):
        if self.directory:
            temp=os.path.join(self.directory,'report.json.tmp')
            with open(temp,'w') as f: json.dump(self.report,f,indent=2)
            os.replace(temp,os.path.join(self.directory,'report.json'))
    def save_report_best_effort(self):
        try:self.save_report()
        except Exception:logging.exception('Auto XY could not write failure report; fault latching still required')
    def record(self,kind,**kwargs):
        self.report.setdefault(kind,[]).append(kwargs);self.save_report()
    # -------- RC15 dev2 non-controlling motion/camera diagnostics --------
    def _diag_event(self,kind,**details):
        if not self.camera_diagnostics_enabled:
            return
        # In-memory until the existing report write; no extra camera or motion I/O.
        self.report.setdefault('diagnostic_events',[]).append({
            'kind':kind,'monotonic_s':round(time.monotonic(),6),
            'phase':getattr(self,'phase',None),**details})

    def _diag_motion_complete(self,kind,**details):
        if not self.camera_diagnostics_enabled:
            return
        self._diag_last_motion_completed_s=time.monotonic()
        self._diag_event(kind,tool=self.active,**details)

    # -------- RC15 bounded, fresh-frame persistent subprocess protocol --------
    def _stop_vision_process(self):
        proc=self._vision_proc
        self._vision_proc=None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                try: proc.stdin.close()  # EOF allows normal --serve exit.
                except OSError: pass
            if proc.poll() is None:
                # Nonblocking termination. Do not stall the Klipper reactor.
                try: proc.terminate()
                except OSError: pass
                if proc.poll() is None:
                    try: proc.kill()
                    except OSError: pass
            # Bounded 30ms reap after signalling: avoid lingering zombies but
            # never block the Klipper reactor for a full camera timeout.
            try: proc.wait(timeout=.03)
            except subprocess.TimeoutExpired:
                logging.warning('AutoXY vision child %s termination deferred',proc.pid)
        finally:
            for pipe in (proc.stdout,proc.stderr):
                if pipe is not None:
                    try: pipe.close()
                    except OSError: pass

    def _start_vision_process(self):
        proc=self._vision_proc
        if proc is not None and proc.poll() is None:
            return proc
        self._stop_vision_process()
        try:
            proc=subprocess.Popen([self.python,self.worker,'--serve'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,bufsize=0,close_fds=True)
            os.set_blocking(proc.stdin.fileno(),False)
            os.set_blocking(proc.stdout.fileno(),False)
        except (OSError,ValueError) as exc:
            if 'proc' in locals() and proc is not None:
                self._vision_proc=proc
                self._stop_vision_process()
            raise VisionWorkerError('Could not start persistent vision worker: '+str(exc))
        self._vision_proc=proc
        return proc

    def _persistent_capture(self,cmd,image):
        # Uses the same CLI arguments as the proven legacy worker; convert
        # explicit flags without changing ROI, registration or detector values.
        options={'url':self.camera,'image':image,'roi':self.roi,
                 'timeout':self.timeout, 'detector_mode':self.camera_detector_mode}
        flags={'--detector-mode':'detector_mode',
               '--expected-center':'expected_center',
               '--expected-radius':'expected_radius',
               '--registration-anchor':'registration_anchor',
               '--registration-anchor-center':'registration_anchor_center'}
        for index in range(0,len(cmd)):
            if cmd[index] in flags:
                options[flags[cmd[index]]]=cmd[index+1]
        self._vision_request_id+=1
        request_id=self._vision_request_id
        options['request_id']=request_id
        wire=(json.dumps(options,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
        if len(wire)>8192:
            raise VisionWorkerError('Persistent vision request too long')
        proc=self._start_vision_process()
        deadline=self.reactor.monotonic()+self.timeout+getattr(self,'_vision_grace_s',10.)
        pos=0
        reply=bytearray()
        while self.reactor.monotonic()<deadline:
            # Preserves motor/homing/heater/watchdog interruption behaviour.
            self.ensure_ok()
            if proc.poll() is not None and pos<len(wire):
                raise VisionWorkerError('Persistent vision worker exited before request')
            if pos<len(wire):
                try:
                    n=os.write(proc.stdin.fileno(),wire[pos:])
                    if n<=0:raise VisionWorkerError('Vision worker stdin closed')
                    pos+=n
                except BlockingIOError:pass
                except (BrokenPipeError,OSError) as exc:
                    raise VisionWorkerError('Vision worker request pipe failed: '+str(exc))
            else:
                try:chunk=os.read(proc.stdout.fileno(),4096)
                except BlockingIOError:chunk=None
                except OSError as exc:
                    raise VisionWorkerError('Vision worker reply pipe failed: '+str(exc))
                if chunk==b'':
                    raise VisionWorkerError('Persistent vision worker exited without a complete reply')
                if chunk:
                    reply.extend(chunk)
                    if len(reply)>65536:
                        raise VisionWorkerError('Vision worker reply exceeded size limit')
                    if b'\n' in reply:
                        row,extra=reply.split(b'\n',1)
                        if extra:
                            raise VisionWorkerError('Unexpected extra persistent-worker output')
                        try:obj=json.loads(row.decode('utf-8'))
                        except (UnicodeError,ValueError) as exc:
                            raise VisionWorkerError('Invalid persistent-worker JSON: '+str(exc))
                        if not isinstance(obj,dict) or obj.get('request_id')!=request_id:
                            raise VisionWorkerError('Stale/mismatched persistent-worker reply ID')
                        if obj.get('ok') is not True:
                            raise VisionWorkerError('Vision detection failed: '+str(obj.get('error','unknown'))[:400])
                        data=obj.get('result')
                        if not isinstance(data,dict):
                            raise VisionWorkerError('Persistent-worker result is missing')
                        return data
                if proc.poll() is not None:
                    raise VisionWorkerError('Persistent vision worker exited mid-reply')
            self.reactor.pause(min(deadline,self.reactor.monotonic()+.05))
        raise VisionWorkerError('Persistent vision worker timed out')

    def worker_capture(self,label,registration_anchor=None,wait_before=None):
        self.ensure_ok()
        if self.tool()!=self.active: raise GateAbort('Unexpected selected tool')
        t_capture=time.monotonic()
        configured_wait=self.settle if wait_before is None else wait_before
        self.pause(configured_wait)
        t_after_wait=time.monotonic()
        self.count+=1
        failures=[]
        for attempt in range(2):
            self.ensure_ok()
            # Preserve a failed first frame for troubleshooting: never let the
            # worker's second attempt overwrite its JPEG.
            image=os.path.join(self.directory,'%03d_%s_attempt_%d.jpg'%(self.count,label,attempt+1))
            cmd=[self.python,self.worker,'--url',self.camera,'--image',image,
                 '--roi',self.roi,'--timeout',str(self.timeout),
                 '--detector-mode',self.camera_detector_mode]
            if self.commissioned_detector_guard:
                if not self._commission_data:
                    raise GateAbort('Commissioned detector guard requires an accepted camera profile')
                tool_entry=self._commission_data['tool%d'%self.active]
                center=tool_entry.get('registration_center_px')
                radius=tool_entry.get('radius_px')
                if center is None or radius is None:
                    raise GateAbort('Incomplete commissioning profile for detector guard')
                cmd.extend(['--expected-center',','.join(str(v) for v in center),
                            '--expected-radius',str(radius)])
            if registration_anchor is not None:
                anchor_path,anchor_center=registration_anchor
                cmd.extend(['--registration-anchor',anchor_path,
                            '--registration-anchor-center',','.join(str(x) for x in anchor_center)])
            proc=None
            t_worker_call=time.monotonic()
            try:
                if self.camera_worker_mode=='persistent':
                    data=self._persistent_capture(cmd,image)
                else:
                    try:
                        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                              close_fds=True)
                    except OSError as e: raise GateAbort('Cannot start vision worker: '+str(e))
                    deadline=self.reactor.monotonic()+self.timeout+10.
                    while proc.poll() is None and self.reactor.monotonic()<deadline:
                        self.reactor.pause(self.reactor.monotonic()+.15)
                    if proc.poll() is None:
                        proc.kill();proc.communicate()
                        raise VisionWorkerError('Worker timed out')
                    out,err=proc.communicate()
                    if proc.returncode:
                        raise VisionWorkerError(err.decode('utf8','replace')[:500])
                    data=json.loads(out.decode('utf8'))
                point=[float(x) for x in data['center_px']]
                if len(point)!=2 or not all(math.isfinite(x) for x in point):
                    raise ValueError('Invalid image centre')
                if self._commission_data:
                    tool_entry=self._commission_data['tool%d'%self.active]
                    if abs(float(data['radius_px'])-float(tool_entry['radius_px']))>18.:
                        raise ValueError('Nozzle rim radius differs from commissioned camera profile; recommission required')
                    # A nozzle can move during calibration sweeps. Compare with a
                    # generous allowance relative to the radius learned from
                    # THIS printer, never a hard-coded global screen coordinate.
                    local_center=tool_entry.get('registration_center_px')
                    if local_center is not None:
                        if norm(vecsub(point,[float(v) for v in local_center]))>max(90.,1.2*float(tool_entry['radius_px'])):
                            raise ValueError('Nozzle candidate too far from this printer\'s commissioned camera location')
                if self.camera_worker_mode=='persistent' and isinstance(data.get('timing_s'),dict):
                    self.report.setdefault('vision_stage_timings',[]).append(
                        {'label':label,'attempt':attempt+1,**data['timing_s']})
                capture_diag=None
                if self.camera_diagnostics_enabled:
                    worker_timeline=data.get('snapshot_timeline_monotonic_s')
                    if not isinstance(worker_timeline,dict):
                        worker_timeline=None  # Older standalone workers remain supported.
                    snap_start=(worker_timeline or {}).get('request_started')
                    last_move=self._diag_last_motion_completed_s
                    capture_diag={
                        'clock':'Linux monotonic seconds since boot (parent and worker on same Pi)',
                        'capture_call_monotonic_s':round(t_capture,6),
                        'configured_wait_s':configured_wait,
                        'wait_completed_monotonic_s':round(t_after_wait,6),
                        'worker_call_monotonic_s':round(t_worker_call,6),
                        'last_motion_completed_monotonic_s':round(last_move,6) if last_move is not None else None,
                        'worker_snapshot_monotonic_s':worker_timeline,
                        'seconds_motion_to_http_request':round(snap_start-last_move,6) if isinstance(snap_start,(int,float)) and last_move is not None else None,
                        'seconds_previous_snapshot_to_http_request':round(snap_start-self._diag_previous_snapshot_started_s,6) if isinstance(snap_start,(int,float)) and self._diag_previous_snapshot_started_s is not None else None,
                        'seconds_wait_actual':round(t_after_wait-t_capture,6),
                        'tool':self.active,
                        'movement_reference':'last AutoXY completed _VAOC_MOVE or _VAOC_LOAD_TOOL; M400 waited',
                    }
                    if isinstance(snap_start,(int,float)):
                        self._diag_previous_snapshot_started_s=snap_start
                self.record('captures',label=label,tool=self.active,
                            center_px=point,radius_px=data['radius_px'],
                            image=image,sha256=data['sha256'],attempt=attempt+1,
                            registration=data.get('registration'),
                            **({'camera_diagnostics':capture_diag} if capture_diag is not None else {}))
                self.report.setdefault('timings',[]).append(
                    {'kind':'capture','label':label,
                     'seconds':round(time.monotonic()-t_capture,4),
                     'attempt':attempt+1})
                return point
            except (ValueError,KeyError,UnicodeError,VisionWorkerError) as exc:
                failures.append('Bad worker output: '+str(exc))
                if self.camera_worker_mode=='persistent':
                    self._stop_vision_process() # fresh process for retry
            finally:
                if proc is not None and proc.poll() is None:
                    proc.kill();proc.communicate()
            self.record('capture_failures',label=label,attempt=attempt+1,image=image,
                        message=failures[-1] if failures else 'worker returned no valid centre')
            if attempt==0: self.pause(1.0) # no movement before retry
        raise GateAbort('Camera/nozzle detection failed at '+label+': '+'; '.join(failures))
    def verify_pose(self):
        self.ensure_ok()
        p=self.xyz(); cp=self.base
        desired=[cp[0]+self.delta[0],cp[1]+self.delta[1],cp[2]]
        if max(abs(p[i]-desired[i]) for i in range(3))>.065:
            raise GateAbort('Position mismatch: '+str((p,desired)))
        if any(abs(self.delta[i])>.251 for i in range(2)):
            raise GateAbort('Camera envelope exceeded')
        if self.tool()!=self.active: raise GateAbort('Tool mismatch')
    def move(self,axis,target):
        if abs(target)>.250001: raise GateAbort('Unsafe XY sweep request')
        a=0 if axis=='x' else 1
        while abs(target-self.delta[a])>1e-7:
            diff=max(-.25,min(.25,target-self.delta[a]))
            args=[0.,0.];args[a]=diff
            self.run_gcode('_VAOC_MOVE X=%.6f Y=%.6f'%tuple(args))
            self.delta[a]+=diff
            self.verify_pose()
            self._diag_motion_complete('xy_move_complete',axis=axis,target_mm=target,step_mm=diff,delta_mm=list(self.delta))
    def move_xy_target(self,x,y):
        # Bound BOTH axes absolutely; native RatOS _VAOC_MOVE only bounds
        # positive values. Never allow a diagonal endpoint outside the sweep.
        if not all(math.isfinite(v) and abs(v)<=.250001 for v in (x,y)):
            raise GateAbort('Unsafe XY settling waypoint')
        dx,dy=x-self.delta[0],y-self.delta[1]
        if max(abs(dx),abs(dy))>.250001:
            raise GateAbort('Unsafe XY settling step')
        if max(abs(dx),abs(dy))<1e-7:return
        self.verify_pose()
        self.run_gcode('_VAOC_MOVE X=%.6f Y=%.6f'%(dx,dy))
        self.delta=[x,y]
        self.verify_pose()
        self._diag_motion_complete('xy_move_complete',axis='xy',target_mm=[x,y],step_mm=[dx,dy],delta_mm=list(self.delta))

    def approach(self):
        started=time.monotonic()
        # The RC13 original path remains byte-for-byte equivalent in motion.
        if self.settling_path=='original':
            self.move('x',-.25);self.move('x',0.)
            self.move('y',-.25);self.move('y',0.)
            steps=4
        else:
            if maximum(self.delta)>1e-7:
                raise GateAbort('Experimental settling requires reference pose')
            waypoints = ([(-.20,-.12),(0.,0.)] if self.settling_path=='diagonal'
                         else [(-.20,0.),(-.20,-.12),(0.,0.)])
            for x,y in waypoints:self.move_xy_target(x,y)
            steps=len(waypoints)
        if maximum(self.delta)>1e-7:
            raise GateAbort('Settling did not return to reference pose')
        self.report.setdefault('timings',[]).append(
            {'kind':'settling','path':self.settling_path,'movements':steps,
             'seconds':round(time.monotonic()-started,4)})
    def select(self,tool):
        if maximum(self.delta)>1e-6:raise GateAbort('Cannot change tool away from VAOC reference')
        if tool!=self.active:
            self._diag_event('tool_select_start',from_tool=self.active,to_tool=tool)
            self.run_gcode('_VAOC_LOAD_TOOL T=%d'%tool)
            self.active=tool;self.delta=[0.,0.]
            self.verify_pose()
            self._diag_motion_complete('tool_select_complete',to_tool=tool)
    def reference(self,label):
        self.approach()
        samples=[self.worker_capture(label+'_a'),self.worker_capture(label+'_b',wait_before=self.camera_pair_gap)]
        mean=avg(samples)
        scatter=max(norm(vecsub(s,mean)) for s in samples)
        return mean,scatter
    def calibrate(self,tool):
        self.select(tool)
        self.approach()
        cols=[]; intercept=[]; residual=[]
        for axis in ('x','y'):
            points=[]
            # Re-establish an independent Hough-located first image for EACH
            # axis, then register each subsequent image against the nearest
            # accepted point (0.2 mm step instead of a 0.4+ mm jump).
            # All returned centres remain ABSOLUTE coordinates in the single
            # stationary camera image. Do not sum incremental displacements.
            registration_anchor=None
            self.move(axis,-.25)
            for d in (-.2,0.,.2):
                self.move(axis,d)
                label='cal_t%d_%s_%+.1f'%(tool,axis,d)
                if registration_anchor is not None:
                    c=self.worker_capture(label,registration_anchor=registration_anchor)
                else:
                    c=self.worker_capture(label)
                if self.camera_registration_enabled:
                    capture=self.report['captures'][-1]
                    # The matching worker reports the coordinate directly
                    # in the fixed camera frame. Each anchor is an actual
                    # accepted image, never a projected or synthetic frame.
                    registration_anchor=(capture['image'],list(c))
                points.append((d,c))
            self.move(axis,0.);self.approach()
            xs=[x for x,y in points]
            ys=[y for x,y in points]
            xm=sum(xs)/3.
            denom=sum((x-xm)**2 for x in xs)
            slopes=[sum((xs[k]-xm)*(ys[k][j]-sum(t[j] for t in ys)/3.) for k in range(3))/denom for j in (0,1)]
            zeros=[sum(y[j] for y in ys)/3.-slopes[j]*xm for j in (0,1)]
            cols.append(slopes);intercept.append(zeros)
            for x,y in points: residual.append(vecsub(y,vecadd(zeros,vecmul(slopes,x))))
        matrix=[[cols[0][0],cols[1][0]],[cols[0][1],cols[1][1]]]
        if abs(det(matrix))<100:raise GateAbort('Camera matrix invalid')
        if not all(130<=norm(c)<=700 for c in cols):raise GateAbort('Camera scale implausible')
        fit=max(norm(solve(matrix,r)) for r in residual)
        zero_gap=norm(solve(matrix,vecsub(intercept[0],intercept[1])))
        self.record('calibrations',tool=tool,matrix=matrix,fit_mm=fit,zero_gap_mm=zero_gap)
        if fit>self.tol or zero_gap>self.tol:
            raise GateAbort('Camera scale fit/zero repeatability failed (%.4f/%.4f mm)'%(fit,zero_gap))
        return matrix
    def measure(self,m,label):
        centres=[];scatter=[]
        for tool,tag in ((0,'t0_start'),(1,'t1'),(0,'t0_return')):
            self.select(tool)
            c,sc=self.reference(label+'_'+tag)
            centres.append(c);scatter.append(sc)
        start,mid,finish=centres
        if self.camera_diagnostics_enabled:
            self.report.setdefault('diagnostic_references',[]).append({
                'measurement':label,'tool_order':[0,1,0],
                'centres_px':[list(c) for c in centres],
                'pair_scatter_px':list(scatter)})
        drift=solve(m,vecsub(finish,start))
        err=solve(m,vecsub(mid,avg([start,finish])))
        dispersion=max(scatter)/min(norm(m[0]),norm(m[1]))
        self.record('measurements',label=label,error_mm=err,t0_drift_mm=drift,
                    scatter_estimate_mm=dispersion,saved_offsets=self.offsets())
        if maximum(drift)>self.tol or dispersion>self.tol:
            raise GateAbort('Camera return/scatter exceeds tolerance')
        if maximum(err)>.300: raise GateAbort('Alignment exceeds 0.300 mm safety limit')
        return err
    def settle_toolshifts(self,m,gcmd):
        """Require repeatable T0/T1/T0 returns before the first correction.

        Settling measurements never call _VAOC_SET_TOOL or change saved offsets.
        A failure stops calibration rather than trying to correct an unstable
        carriage/camera reference. Post-write measurement checks remain active, but an unusual gain
        cannot override two independent, stable in-tolerance readings.
        """
        last=None
        self.phase='SETTLING_TOOLSHIFTS'
        self.progress_stage(gcmd,'SETTLING','Checking T0/T1 positioning after tool changes')
        self.report['settling_status']='MEASURING'
        self.report['settling_limit_mm']=self.preflight_settling_tolerance
        self.report['settling_max_checks']=self.preflight_settling_checks
        self.save_report()
        for index in range(1,self.preflight_settling_checks+1):
            self.ensure_ok()
            reading=self.measure(m,'settle_%02d'%index)
            if last is not None:
                delta=vecsub(reading,last)
                self.record('settling_checks',check=index,error_mm=reading,
                            change_mm=delta,stable=maximum(delta)<=self.preflight_settling_tolerance)
                if maximum(delta)<=self.preflight_settling_tolerance:
                    self.report['settling_status']='STABLE'
                    self.report['settling_checks_used']=index
                    self.save_report()
                    gcmd.respond_info('AUTO XY: toolshift settling stable after %d no-write checks (X drift %.4f, Y drift %.4f mm)'%(
                        index,abs(delta[0]),abs(delta[1])))
                    return
            else:
                self.record('settling_checks',check=index,error_mm=reading,change_mm=None,stable=False)
            last=reading
        self.report['settling_status']='FAILED'
        self.report['settling_checks_used']=self.preflight_settling_checks
        self.save_report()
        raise GateAbort('Initial toolshift settling failed: repeatability exceeds %.3f mm after %d no-write checks'%(
            self.preflight_settling_tolerance,self.preflight_settling_checks))

    def confirm_anomalous_alignment(self, matrix, cycle, initial_error,
                                    anomalous, used_verifications, gcmd):
        """Verify the *alignment*, not the apparent camera response gain.

        A surprisingly large (but bounded and correctly directed) response
        must not reject an already in-tolerance result without checking it.
        We require TWO independent no-write T0/T1/T0 measurements after the
        anomalous reading. The readings must agree with each other and with
        the anomalous reading, both remain within the alignment tolerance,
        and the camera return, scatter and persisted offsets must be stable.

        This consumes existing verification slots and cannot apply further
        offsets, increase correction budgets, or mask a gross/reverse move.
        """
        if maximum(initial_error)>self.tol:
            raise GateAbort('Offset response anomaly: alignment outside tolerance')
        if used_verifications + 2 > self.max_verify:
            raise GateAbort('Offset response anomaly: insufficient verification budget')
        saved=self.report.get('saved_corrections',[])
        if not saved:
            raise GateAbort('Offset response anomaly: no confirmed offset write')
        expected=saved[-1]['after']
        if max(abs(self.offsets()[i]-expected[i]) for i in range(3))>.0001:
            raise GateAbort('Offset response anomaly: saved offsets changed before recheck')
        prior=list(initial_error)
        self.phase='RECHECKING_ALIGNMENT'  # not eligible for in-place retry
        self.report['response_gain_recheck_status']='MEASURING'
        self.save_report()
        for index in (1,2):
            reading=self.measure(matrix,'gain_recheck_%02d_%02d'%(cycle,index))
            detail=self.report['measurements'][-1]
            change=vecsub(reading,prior)
            self.record('response_gain_rechecks',
                        cycle=cycle,sequence=index,error_mm=reading,change_mm=change,
                        t0_drift_mm=detail['t0_drift_mm'],
                        scatter_estimate_mm=detail['scatter_estimate_mm'])
            if max(abs(self.offsets()[i]-expected[i]) for i in range(3))>.0001:
                raise GateAbort('Offset response anomaly: saved offsets changed during recheck')
            if (not all(math.isfinite(x) for x in reading) or
                maximum(reading)>self.tol or maximum(change)>.015 or
                maximum(detail['t0_drift_mm'])>.015 or
                detail['scatter_estimate_mm']>.015):
                raise GateAbort('Offset response anomaly: repeat measurements not stable/in tolerance')
            prior=list(reading)
            gcmd.respond_info('AUTO XY: unexpected response; no-write alignment confirmation %d/2 X=%+.4f Y=%+.4f mm' % (
                index,reading[0],reading[1]))
        self.report['response_gain_recheck_status']='CONFIRMED_IN_TOLERANCE'
        self.report['response_gain_warning']='UNUSUAL_OFFSET_RESPONSE; REPEATABLE_IN_TOLERANCE_ALIGNMENT; INVESTIGATE_IF_REPEATED'
        self.save_report()
        return prior

    def apply(self,step,original):
        if maximum(step)>self.max_step+1e-6: raise GateAbort('Correction exceeded per-pass cap')
        cur=self.offsets()
        predicted=[cur[0]-step[0],cur[1]-step[1],cur[2]]
        if max(abs(predicted[i]-original[i]) for i in (0,1))>self.max_total+1e-7:
            raise GateAbort('Total offset correction exceeds safety cap')
        self.select(1)
        self.approach()
        for axis,target in (('x',step[0]),('y',step[1])):
            self.move(axis,-.23);self.move(axis,target)
        self.record('write_intents',before=cur,predicted=predicted,step=step)
        self.needs_reconcile=True
        self.run_gcode('_VAOC_SET_TOOL')
        after=self.offsets()
        if max(abs(after[i]-predicted[i]) for i in range(3))>.0001:
            raise GateAbort('RatOS did not persist predicted offset values')
        self.record('saved_corrections',before=cur,after=after,step=step)
        self.move('x',0.);self.move('y',0.)
        self.select(0)
        return after
    def rollback(self,original):
        if not self.needs_reconcile: return 'NO_SAVED_CHANGES'
        for axis,key in ((0,'idex_xoffset'),(1,'idex_yoffset')):
            value=original[axis]
            self.run_gcode('SAVE_VARIABLE VARIABLE=%s VALUE=%.10f'%(key,value))
        now=self.offsets()
        if max(abs(now[i]-original[i]) for i in (0,1))>.0001:
            raise GateAbort('Rollback saved offset verification failed')
        return 'ORIGINAL_SAVED_VALUES_RESTORED; RUNTIME ALIGNMENT NOT VERIFIED'
    def classify_measurement(self,err,corrections,verifications,streak):
        """Separate bounded correction and no-write verification budgets.

        In-tolerance measurement: a verification attempt, no saved offset write.
        Out-of-tolerance measurement: a correction opportunity (if budget allows).
        Camera captures/retries inside measure() are independently guarded and
        MUST NOT be treated as a request to ignore image detection failures.
        """
        if maximum(err)<=self.tol:
            verifications+=1
            streak+=1
            if streak>=2:
                return 'COMPLETE',verifications,streak
            if verifications>=self.max_verify:
                return 'VERIFY_EXHAUSTED',verifications,streak
            return 'VERIFY_AGAIN',verifications,streak
        if corrections>=self.max_correct:
            return 'CORRECTION_EXHAUSTED',verifications,0
        return 'CORRECT',verifications,0

    def flag(self,key):
        return str(self.variables().get(key,0)).strip().lower() in ('1','1.0','true')

    def retryable_failure(self, exc, original):
        """Only measurement/convergence faults can offer an in-VAOC retry.

        Do not rollback verified writes for eligible failures: doing so while
        VAOC is active could make the saved and live toolhead offsets disagree.
        The second pass reuses the currently verified saved offsets, but the
        aggregate 0.300 mm safety cap stays relative to the FIRST baseline.
        """
        if self._retry_precommitted or self._retry_context is not None:
            return False
        # Measurement-only retry is only meaningful in the protected held-file
        # workflow, and it must never authorize any offset write.
        if self.flag('auto_xy_v2_measure_only') and not self._protected_inside_gate:
            return False
        if not isinstance(exc,GateAbort):
            return False
        desc=str(exc)
        recoverable=(
            desc.startswith('Camera/nozzle detection failed at '),
            (self._protected_inside_gate and desc.startswith('MEASURE_ONLY alignment outside tolerance')),
            desc.startswith('Camera scale fit/zero repeatability failed'),
            desc.startswith('Camera return/scatter exceeds tolerance'),
            desc.startswith('Initial toolshift settling failed:'),
            desc.startswith('Verification limit reached'),
            desc.startswith('Correction limit reached'),
            desc.startswith('T0/T1 camera scale disagreement > 12%'),
            desc=='Calibration did not converge',
        )
        if not any(recoverable):
            return False
        # Must never offer retry during, or immediately after an incompletely
        # audited offset write, or when VAOC itself exited/stopped.
        if self.phase not in (('CALIBRATE','MEASURING','VERIFYING','MEASURE_ONLY')
                              if self._protected_inside_gate else ('CALIBRATE','MEASURING','VERIFYING')):
            return False
        if original is None or not self.directory or not self.vaoc_entered:
            return False
        if self.printer.get_state_message()[1]!='ready':
            return False
        if not self.sts('gcode_macro _VAOC').get('is_started'):
            return False
        intents=self.report.get('write_intents',[])
        saved=self.report.get('saved_corrections',[])
        if len(intents)!=len(saved):
            return False
        expected=saved[-1]['after'] if saved else original
        current=self.offsets()
        if max(abs(current[i]-expected[i]) for i in range(3))>.0001:
            return False
        if not all(math.isfinite(x) for x in current):
            return False
        if max(abs(current[i]-original[i]) for i in (0,1))>self.max_total+1e-6:
            return False
        # Read-only exact pose check: never move as part of failure handling.
        try:
            if self.tool()!=self.active:
                return False
            cp=self.cp()
            if max(abs(cp[i]-self.base[i]) for i in range(3))>.0001:
                return False
            actual=self.xyz()
            expected_pose=[self.base[0]+self.delta[0],self.base[1]+self.delta[1],self.base[2]]
            if max(abs(actual[i]-expected_pose[i]) for i in range(3))>.015:
                return False
            if maximum(self.delta)>.250001:
                return False
            if not all(a in str(self.sts('toolhead')['homed_axes']) for a in 'xyz'):
                return False
            self.check_camera_temps()
        except Exception:
            return False
        self._retry_context={
            'initial_offsets':list(original), 'expected_offsets':list(current),
            'base':list(self.base), 'delta':list(self.delta),
            'active':self.active, 'initial_tool':self.report['initial_tool'],
            'previous_report_dir':self.directory,
        }
        return True

    def validate_active_vaoc_context(self,ctx):
        """Read-only live checks before any attempt to reuse VAOC."""
        if not isinstance(ctx,dict):
            raise GateAbort('Missing active VAOC retry context')
        if self.printer.get_state_message()[1]!='ready':
            raise GateAbort('Klipper not ready for active VAOC retry')
        if not self.sts('gcode_macro _VAOC').get('is_started'):
            raise GateAbort('VAOC ended while preparing retry')
        if not all(axis in str(self.sts('toolhead')['homed_axes']) for axis in 'xyz'):
            raise GateAbort('Homing invalid before retry')
        if self.tool()!=ctx['active']:
            raise GateAbort('Active tool changed before retry')
        if max(abs(self.cp()[i]-ctx['base'][i]) for i in range(3))>.0001:
            raise GateAbort('VAOC control point changed before retry')
        pose=self.xyz()
        expected=[ctx['base'][0]+ctx['delta'][0],ctx['base'][1]+ctx['delta'][1],ctx['base'][2]]
        if max(abs(pose[i]-expected[i]) for i in range(3))>.015:
            raise GateAbort('VAOC position shifted before retry')
        if maximum(ctx['delta'])>.250001:
            raise GateAbort('VAOC sweep envelope exceeded before retry')
        offsets=self.offsets()
        if max(abs(offsets[i]-ctx['expected_offsets'][i]) for i in range(3))>.0001:
            raise GateAbort('Saved offsets changed before retry')
        if max(abs(offsets[i]-ctx['initial_offsets'][i]) for i in (0,1))>self.max_total+1e-6:
            raise GateAbort('Combined correction safety budget exceeded')
        self.check_camera_temps()

    def try_inline_print_retry(self,exc,gcmd,retry_possible):
        """Complete exactly ONE fresh calibration before returning to RatOS.

        Called from the original Klipper command's except handler, BEFORE
        generating a G-code error. The UI remains informational, never a
        blocking modal G-code request. A successful retry returns normally
        to the still-running START_PRINT. A failed retry raises and latches
        a fault through the nested gate's normal failure handler.
        """
        if not (self.inline_print_retry and retry_possible and
                not self._protected_inside_gate and
                not self._retry_precommitted and self.report.get('mode')=='PRINT'):
            return None
        ctx=self._retry_context
        parent_report=self.report
        parent_dir=self.directory
        try:
            self.validate_active_vaoc_context(ctx)
            # Make a reboot during the inline retry fail-closed. The run
            # already has auto_xy_v2_in_progress=1; lock fault and consume token
            # before any additional movement. AUTO XY stays ON in normal
            # operation and after a successful retry.
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_used VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
            parent_report['status']='INLINE_RETRY_STARTED'
            parent_report['inline_retry_reason']=str(exc)
            parent_report['inline_retry_budget']=1
            self.save_report_best_effort()
        except Exception as setup_error:
            parent_report['inline_retry_setup_refused']=str(setup_error)
            self.save_report_best_effort()
            return False
        gcmd.respond_info('AUTO XY: recoverable issue (%s). Keeping VAOC active and retrying calibration ONCE within this same print start.' % str(exc))
        self._retry_precommitted=True
        self.busy=False   # re-enter only from this same synchronous G-code call
        try:
            # Critical: PRINT not IDLE. This call MUST return successfully
            # before RatOS can advance to Beacon, priming and printing.
            try:
                self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd,mode='PRINT'))
            except BaseException as retry_error:
                parent_report['status']='INLINE_RETRY_FAILED_PRINT_ABORTED'
                parent_report['inline_retry_error']=str(retry_error)
                raise
            else:
                parent_report['status']='RECOVERED_INLINE'
                gcmd.respond_info('AUTO XY: single inline retry VERIFIED. Resuming the original RatOS print start; AUTO XY remains ON.')
                return True
            finally:
                # The child always owns the authoritative final fault/success
                # report. This parent cross-reference is best effort, not a
                # second failure point after a successful calibration.
                parent_report['inline_retry_report_dir']=self.directory
                try:
                    tmp=str(parent_dir)+'/report.json.tmp'
                    with open(tmp,'w') as f: json.dump(parent_report,f,indent=2)
                    os.replace(tmp,str(parent_dir)+'/report.json')
                except Exception:
                    logging.exception('Auto XY could not update parent report after inline retry')
        finally:
            # The nested gate is allowed no further retries. Its ordinary
            # exception handler fault-latches and aborts on any failure.
            self._retry_precommitted=False
            self._retry_context=None

    def prompt_on_failure(self,retry_possible):
        """Prompt buttons are serviced only AFTER the current G-code command stops.

        Never block Klipper's print-start command waiting for a Mainsail click:
        the next UI-issued G-code would be stuck behind it in Klipper's queue.
        """
        try:
            emit=self.gcode.respond_raw
            emit('// action:prompt_begin Auto XY calibration stopped')
            if self.report.get('mode')=='IDLE':
                emit('// action:prompt_text Standalone AutoXY calibration stopped. No original print is held to resume.')
            else:
                emit('// action:prompt_text Original print ABORTED. Do not resume it.')
            if retry_possible:
                emit('// action:prompt_text One fresh calibration can reuse the ACTIVE VAOC session.')
                emit('// action:prompt_text Wait until the old print is no longer active. Do NOT exit VAOC or acknowledge the fault before retry.')
                emit('// action:prompt_text Retry checks homing, live pose, tool, temperatures and saved offsets before ANY movement.')
                emit('// action:prompt_button Retry calibration once (keep VAOC)|AUTO_XY_RETRY_ONCE|warning')
                emit('// action:prompt_footer_button Leave Auto XY OFF|AUTO_XY_RETRY_DISMISS|secondary')
            else:
                emit('// action:prompt_text Unsafe/nonrecoverable failure, or retry exhausted. Manually recover VAOC and saved/runtime offsets.')
                emit('// action:prompt_footer_button Dismiss|AUTO_XY_OFF|secondary')
            emit('// action:prompt_show')
        except Exception:
            logging.exception('Auto XY could not display optional Mainsail failure prompt')

    def cmd_AUTO_XY_RETRY_ONCE(self,gcmd):
        """One operator-triggered retry without _VAOC_END/_VAOC_START.

        The original PRINT is always aborted; this is an IDLE recalibration.
        All hard faults remain latched until the new run completes. The token
        is consumed BEFORE re-enabling / moving; Klipper restart loses the
        RAM-only pose context and requires manual recovery.
        """
        if self.busy:
            raise gcmd.error('Retry refused: calibration is already busy')
        if not self.flag('auto_xy_v2_retry_available') or self.flag('auto_xy_v2_retry_used'):
            raise gcmd.error('Retry refused: no unused one-time retry is available')
        ctx=self._retry_context
        if ctx is None:
            raise gcmd.error('Retry refused: active VAOC pose context was lost (e.g. Klipper restarted); manually recover and acknowledge fault')
        if not self.flag('auto_xy_v2_fault') or not self.flag('auto_xy_v2_in_progress'):
            raise gcmd.error('Retry refused: original safety markers changed; manually inspect the printer')
        if self.enabled():
            raise gcmd.error('Retry blocked: AUTO XY must remain OFF until the guarded retry starts')
        if self.printer.get_state_message()[1]!='ready':
            raise gcmd.error('Retry blocked: Klipper is not ready')
        if str(self.sts('print_stats').get('state','')).lower() in ('printing','paused'):
            raise gcmd.error('Retry blocked: old print is still printing/paused. Stop the old print safely, keeping VAOC undisturbed')
        sdcard=self.printer.lookup_object('virtual_sdcard',None)
        if sdcard is not None and sdcard.get_status(self.reactor.monotonic()).get('is_active',False):
            raise gcmd.error('Retry blocked: old virtual SD print remains active')
        if not self.sts('gcode_macro _VAOC').get('is_started'):
            raise gcmd.error('Retry blocked: VAOC ended; the saved active-position context is no longer valid')
        if not all(axis in str(self.sts('toolhead')['homed_axes']) for axis in 'xyz'):
            raise gcmd.error('Retry blocked: xyz homing no longer valid')
        if self.tool()!=ctx['active']:
            raise gcmd.error('Retry blocked: active tool changed after failure')
        if max(abs(self.cp()[i]-ctx['base'][i]) for i in range(3))>.0001:
            raise gcmd.error('Retry blocked: VAOC control point changed')
        pose=self.xyz()
        expected=[ctx['base'][0]+ctx['delta'][0],ctx['base'][1]+ctx['delta'][1],ctx['base'][2]]
        if max(abs(pose[i]-expected[i]) for i in range(3))>.015:
            raise gcmd.error('Retry blocked: nozzle position changed or differs from stored VAOC pose')
        if maximum(ctx['delta'])>.250001:
            raise gcmd.error('Retry blocked: outside VAOC camera movement envelope')
        offsets=self.offsets()
        if max(abs(offsets[i]-ctx['expected_offsets'][i]) for i in range(3))>.0001:
            raise gcmd.error('Retry blocked: saved offsets changed after failure; manual review required')
        try:self.check_camera_temps()
        except GateAbort as exc:raise gcmd.error('Retry blocked: '+str(exc))
        # Crash-safe ordering: mark retry USED before allowing a single move.
        try:
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_used VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
            # Keep fault + interrupted marker set during the entire retry.
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=1')
        except Exception as exc:
            raise gcmd.error('Retry could not commit one-use token: '+str(exc))
        gcmd.respond_info('AutoXY | RETRY: Standalone active-VAOC retry accepted; no print is held.' if self.report.get('mode')=='IDLE' else 'AutoXY | RETRY: Legacy unprotected print is cancelled; a NEW print is required.')
        self._retry_precommitted=True
        try:
            self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd))
        finally:
            self._retry_precommitted=False
            self._retry_context=None  # one-use, even on failure

    # -------- RC15 dev3 exclusively diagnostic endurance path --------
    def cmd_AUTO_XY_ENDURANCE_RUN(self,gcmd):
        """One call: original verified AUTO_CALIBRATE -> continuous T0/T1 visits.

        Normal AUTO_XY_GATE, print-start, and MEASURE_ONLY never enter this path.
        The calibration gate itself checks safety, thermal/homing and commission.
        """
        if self._endurance_request is not None or self.busy:
            raise gcmd.error('AutoXY endurance already running')
        if self._retry_precommitted or self._retry_context is not None:
            raise gcmd.error('Endurance unavailable during active VAOC retry/recovery')
        if self._protected_inside_gate:
            raise gcmd.error('Endurance unavailable inside protected print')
        if not self.enabled():
            raise gcmd.error('Turn AUTO_XY_ON before supervised endurance calibration')
        if self.flag('auto_xy_v2_measure_only'):
            raise gcmd.error('Endurance requires AUTO_XY_ON, not MEASURE_ONLY: initial calibration must correct and verify')
        if self.settling_path!='original':
            raise gcmd.error('Endurance is qualified for Original settling path only')
        if self.reuse_enabled():
            raise gcmd.error('Disable calibration reuse before endurance (AUTO_XY_REUSE_OFF)')
        try:
            raw=str(gcmd.get('TOOL_CHANGES','100')).strip()
            if not raw.isdigit(): raise ValueError('TOOL_CHANGES must be an integer')
            changes=int(raw)
            limit=float(gcmd.get('DRIFT_LIMIT','1.0'))
        except (TypeError,ValueError):
            raise gcmd.error('TOOL_CHANGES must be an integer; DRIFT_LIMIT must be numeric')
        if changes<1 or changes>200:
            raise gcmd.error('TOOL_CHANGES must be 1..200')
        if not math.isfinite(limit) or limit<0.05 or limit>1.0:
            raise gcmd.error('DRIFT_LIMIT must be 0.05..1.0 mm, diagnostic only')
        if self.sts('print_stats')['state'] in ('printing','paused') or self.virtual_sd_active():
            raise gcmd.error('Endurance is standalone only; never from a print')
        self._endurance_request={'tool_changes':changes,'drift_limit_mm':limit,
                                 'timeout_seconds':3600.0}
        try:
            # _RetryGcmd sets MODE=IDLE and delegates all responses to console.
            self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd,'IDLE'))
        finally:
            self._endurance_request=None
            self._endurance_active=False

    def _endurance_files(self, include_charts=False):
        """Write crash-useful CSV and portable SVG charts, with no other dependencies."""
        if not self.directory or 'endurance_visits' not in self.report:
            return
        visits=self.report['endurance_visits']
        rows=[]
        for v in visits:
            for tag in ('A','B'):
                q=v['images'][tag]
                rows.append({
                    'visit':v['visit_index'],'change_count':v['change_count'],
                    'tool':'T%d'%v['tool'],'image':tag,
                    'x_px':q['center_px'][0],'y_px':q['center_px'][1],
                    'drift_x_mm':q['from_tool_baseline_mm'][0],
                    'drift_y_mm':q['from_tool_baseline_mm'][1],
                    'within_visit_x_mm':v['b_minus_a_mm'][0],
                    'within_visit_y_mm':v['b_minus_a_mm'][1],
                    'relative_x_mm':(v['relative_change_mm'][0] if v.get('relative_change_mm') is not None else ''),
                    'relative_y_mm':(v['relative_change_mm'][1] if v.get('relative_change_mm') is not None else ''),
                    'capture_path':q['path'],'sha256':q['sha256'],
                    'camera_request_monotonic_s':q.get('camera_request_monotonic_s'),
                    'seconds_motion_to_http_request':q.get('seconds_motion_to_http_request'),
                    'temperature_t0_c':v['temperatures_c']['extruder'],
                    'temperature_t1_c':v['temperatures_c']['extruder1'],
                })
        cols=('visit','change_count','tool','image','x_px','y_px','drift_x_mm','drift_y_mm',
              'within_visit_x_mm','within_visit_y_mm','relative_x_mm','relative_y_mm',
              'capture_path','sha256','camera_request_monotonic_s',
              'seconds_motion_to_http_request','temperature_t0_c','temperature_t1_c')
        data=io.StringIO();writer=csv.DictWriter(data,fieldnames=cols)
        writer.writeheader();writer.writerows(rows)
        filename=os.path.join(self.directory,'endurance_images.csv')
        tmp=filename+'.tmp'
        with open(tmp,'w') as f:f.write(data.getvalue())
        os.replace(tmp,filename)
        if not include_charts:return
        def graph(name, title, curves):
            # Show automatic data bounds: ±1 mm safety ceiling is NOT an acceptance band.
            curves=[(lab,[(float(x),float(y)) for x,y in pts if y is not None],color)
                    for lab,pts,color in curves]
            points=[(x,y) for _,pts,_ in curves for x,y in pts]
            if not points:return
            x_max=max(1,max(x for x,y in points))
            absolute=max(abs(y) for x,y in points)
            bound=max(.03,absolute*1.2)
            left,right,top,bottom=82,778,38,313
            def sx(v):return left+(right-left)*v/x_max
            def sy(v):return (top+bottom)/2-v/bound*(bottom-top)/2
            elements=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 810 355">',
                      '<rect width="810" height="355" fill="#fff"/>',
                      '<text x="28" y="23" font-family="sans-serif" font-size="16">%s</text>'%html.escape(title)]
            for tick in range(-2,3):
                v=bound*tick/2
                elements+=['<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e4e7eb"/>'%(left,sy(v),right,sy(v)),
                           '<text x="5" y="%.1f" font-family="sans-serif" font-size="11">%+.4f</text>'%(sy(v)-3,v)]
            for x in (0,x_max/4,x_max/2,3*x_max/4,x_max):
                elements+=['<text x="%.1f" y="335" text-anchor="middle" font-family="sans-serif" font-size="11">%g</text>'%(sx(x),x)]
            elements.append('<text x="350" y="352" font-family="sans-serif" font-size="11">Tool changes (vertical: drift in mm)</text>')
            for n,(lab,pts,color) in enumerate(curves):
                if not pts:continue
                poly=' '.join('%.2f,%.2f'%(sx(x),sy(y)) for x,y in pts)
                elements+=['<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>'%(poly,color),
                           '<text x="%d" y="%d" font-size="12" fill="%s" font-family="sans-serif">%s</text>'%(575+100*(n%2),23-13*(n//2),color,html.escape(lab))]
            elements.append('</svg>')
            out=os.path.join(self.directory,name+'.svg')
            tmp=out+'.tmp'
            with open(tmp,'w') as f:f.write('\n'.join(elements))
            os.replace(tmp,out)
        colours={'A':'#2366a5','B':'#d17b23'}
        for tool in (0,1):
            items=[v for v in visits if v['tool']==tool]
            for axis,key in ((0,'x'),(1,'y')):
                graph('endurance_t%d_%s'%(tool,key),'T%d %s drift by image (mm)'%(tool,key.upper()),
                      [(tag,[(v['change_count'],v['images'][tag]['from_tool_baseline_mm'][axis]) for v in items],colours[tag])
                       for tag in ('A','B')])
        for axis,key in ((0,'x'),(1,'y')):
            graph('endurance_ab_%s'%key,'Within-visit B minus A %s (mm)'%key.upper(),
                  [('T%d'%tool,[(v['change_count'],v['b_minus_a_mm'][axis]) for v in visits if v['tool']==tool],
                    '#2366a5' if tool==0 else '#d17b23') for tool in (0,1)])
            graph('endurance_relative_%s'%key,'T1 relative to preceding T0 %s drift (mm)'%key.upper(),
                  [('T1-T0',[(v['change_count'],v['relative_change_mm'][axis]) for v in visits
                              if v.get('relative_change_mm') is not None],'#7c3aed')])

    def _run_endurance(self, matrix, gcmd):
        req=self._endurance_request
        if req is None:raise GateAbort('Internal endurance request missing')
        self.phase='ENDURANCE'
        self._endurance_active=True
        # In separate stage: original calibration *already passed*, no more writes.
        self.deadline=self.reactor.monotonic()+req['timeout_seconds']
        saved=list(self.offsets())
        self.report['endurance']={'status':'RUNNING',**req,
                                  'start_offsets':saved,'tool_baseline_px':{},
                                  'tool_changes_completed':0,'visits_completed':0,
                                  'started_at':time.strftime('%Y-%m-%d %H:%M:%S'),
                                  'diagnostic_only':True}
        self.report['endurance_visits']=[]
        self.save_report()
        baseline={};prev_t0=None;baseline_rel=None
        gcmd.respond_info('AUTO XY ENDURANCE: verified calibration complete; starting %d tool changes; diagnostics only; no further offset writes'%req['tool_changes'])
        for i in range(req['tool_changes']+1):
            self.ensure_ok()
            if max(abs(self.offsets()[a]-saved[a]) for a in range(3))>1e-6:
                raise GateAbort('Endurance abort: saved offsets changed during diagnostic phase')
            tool=i%2
            self.select(tool)
            self.verify_pose()
            label='endurance_%03d_t%d'%(i,tool)
            # Only one Original settling approach; no extra calibration or scaling.
            self.approach()
            images={}
            for tag in ('A','B'):
                centre=self.worker_capture(label+'_'+tag.lower(),
                                           wait_before=self.camera_pair_gap if tag=='B' else None)
                capture=self.report['captures'][-1]
                diag=capture.get('camera_diagnostics') or {}
                timeline=diag.get('worker_snapshot_monotonic_s') or {}
                images[tag]={'center_px':list(centre),'path':capture['image'],
                             'sha256':capture['sha256'],
                             'camera_request_monotonic_s':timeline.get('request_started'),
                             'seconds_motion_to_http_request':diag.get('seconds_motion_to_http_request')}
            if tool not in baseline:
                baseline[tool]={tag:list(images[tag]['center_px']) for tag in ('A','B')}
                self.report['endurance']['tool_baseline_px']['T%d'%tool]=baseline[tool]
            for tag in ('A','B'):
                images[tag]['from_tool_baseline_mm']=solve(matrix,vecsub(images[tag]['center_px'],baseline[tool][tag]))
            pair_delta=solve(matrix,vecsub(images['B']['center_px'],images['A']['center_px']))
            mean=avg([images['A']['center_px'],images['B']['center_px']])
            relative=None
            if tool==0:
                prev_t0=mean
            elif prev_t0 is not None:
                rel=solve(matrix,vecsub(mean,prev_t0))
                if baseline_rel is None: baseline_rel=rel
                relative=vecsub(rel,baseline_rel)
            temps=self.nozzle_temps()
            visit={'visit_index':i,'change_count':i,'tool':tool,
                   'images':images,'b_minus_a_mm':pair_delta,
                   'average_center_px':mean,'relative_change_mm':relative,
                   'temperatures_c':temps,
                   'monotonic_s':time.monotonic()}
            if max(abs(self.offsets()[a]-saved[a]) for a in range(3))>1e-6:
                raise GateAbort('Endurance abort: saved offsets changed during diagnostic capture')
            self.report['endurance_visits'].append(visit)
            self.report['endurance']['visits_completed']=i+1
            self.report['endurance']['tool_changes_completed']=i
            self.save_report()
            self._endurance_files(include_charts=False)
            if (any(maximum(images[tag]['from_tool_baseline_mm'])>req['drift_limit_mm'] for tag in ('A','B'))
                    or maximum(pair_delta)>req['drift_limit_mm']
                    or (relative is not None and maximum(relative)>req['drift_limit_mm'])):
                raise GateAbort('Endurance drift exceeded diagnostic %.3f mm ceiling at visit %d'%(req['drift_limit_mm'],i))
            if i and i%10==0:
                gcmd.respond_info('AUTO XY ENDURANCE: %d/%d tool changes complete; T%d within-visit X=%+.4f Y=%+.4f mm'%(
                    i,req['tool_changes'],tool,pair_delta[0],pair_delta[1]))
        self.report['endurance']['status']='COMPLETED'
        self.report['endurance']['finished_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        self.save_report()
        self._endurance_files(include_charts=True)
        gcmd.respond_info('AUTO XY ENDURANCE: completed %d tool changes and %d individually logged A/B images; no diagnostic offset writes'%(
            req['tool_changes'],2*(req['tool_changes']+1)))

    def health(self,gcmd):
        v=self.variables()
        gcmd.respond_info('AutoXY %s / RatOS 2.1 IDEX camera calibration (after heatsoak, before Beacon); limits=%d corrections, %d verifications; inline_print_retry=%s; retry_available=%s retry_used=%s; active_vaoc_context=%s; switch=%s; fault=%s; interrupted=%s; busy=%s; last_error_recorded=%s'%(
            RELEASE_VERSION,self.max_correct,self.max_verify,self.inline_print_retry,v.get('auto_xy_v2_retry_available',0),v.get('auto_xy_v2_retry_used',0),bool(self._retry_context),'ON' if self.enabled() else 'OFF',v.get('auto_xy_v2_fault',0),
            v.get('auto_xy_v2_in_progress',0),self.busy,self.last_error or 'none'))
    def cmd_AUTO_XY_HEALTH(self,gcmd):
        self.health(gcmd)
        valid,reason=self._reuse_check(check_thermal=False)
        gcmd.respond_info('AUTO XY reuse: opt_in=%s valid_session=%s reason=%s' % (
            self.reuse_enabled(),valid,reason))
        gcmd.respond_info('AUTO XY camera: worker_mode=%s settling_path=%s detector_mode=%s' % (
            self.camera_worker_mode,self.settling_path,self.camera_detector_mode))

    def cmd_AUTO_XY_GATE(self,gcmd):
        endurance_requested=self._endurance_request is not None
        mode=gcmd.get('MODE','PRINT').upper()
        if endurance_requested and mode!='IDLE':
            raise gcmd.error('Endurance must be an IDLE-only operation')
        if mode not in ('PRINT','IDLE'): raise gcmd.error('MODE must be PRINT or IDLE')
        if str(self.variables().get('auto_xy_v2_in_progress',0)).lower() in ('1','1.0','true') and not self._retry_precommitted:
            raise gcmd.error('AUTO XY interrupted prior run detected: inspect saved/runtime offsets, run AUTO_XY_ACKNOWLEDGE')
        if str(self.variables().get('auto_xy_v2_fault',0)).lower() in ('1','1.0','true') and not (self._retry_precommitted and self._retry_context is not None):
            raise gcmd.error('AUTO XY fault latched: inspect saved/runtime offsets and run AUTO_XY_ACKNOWLEDGE before any print')
        if not self.enabled():
            self.pending_print_mode=None  # never let an OFF print leave stale tokens
            self.pending_toolshifts=None
            self.pending_reuse_candidate=False
            gcmd.respond_info('AUTO XY OFF — bypassing VAOC entirely, saved offsets unchanged')
            return
        if self.busy: raise gcmd.error('AUTO XY already running')
        if mode=='PRINT':
            # Belt-and-braces guard: protected-print support must never fall
            # back to the older inline retry that aborts on correlation errors.
            if self.protected_print_enabled and not self._protected_inside_gate:
                raise gcmd.error(
                    'AutoXY protected print requires AUTOXY_PRINT_CALIBRATE. '
                    'Ordinary START_PRINT cannot safely hold original G-code.')
            # The original START_PRINT can also run from the RatOS VAOC
            # console workflow; do not launch AutoXY for those calls.
            if not self._retry_precommitted and not self.virtual_sd_active():
                self.pending_print_mode=None
                self.pending_toolshifts=None
                self.pending_reuse_candidate=False
                gcmd.respond_info('AUTO XY: not a virtual-SD print; skipping automatic gate')
                return
            if self._retry_precommitted:
                # An inline retry reuses the original print's IDEX classification;
                # no new _USER_START_PRINT is executed between attempts.
                if self.active_print_mode!='INDEPENDENT':
                    raise gcmd.error('Auto XY inline retry lost its independent-mode context')
            else:
                requested=self.pending_print_mode
                captured_toolshifts=self.pending_toolshifts
                reuse_candidate=self.pending_reuse_candidate
                self.pending_reuse_candidate=False
                self.pending_print_mode=None  # consume once per START_PRINT
                self.pending_toolshifts=None
                if requested is None:
                    raise gcmd.error('Auto XY: missing early RatOS mode capture. Refusing camera motion; check _USER_START_PRINT hook.')
                if requested in ('COPY','MIRROR','SINGLE_TOOL'):
                    self.active_print_mode=None
                    gcmd.respond_info('AUTO XY: %s detected; skipping VAOC entirely; saved offsets unchanged; AUTO XY remains ON.' % requested)
                    return
                if requested!='INDEPENDENT':
                    raise gcmd.error('Auto XY: invalid START_PRINT mode: '+str(requested))
                self.active_print_mode='INDEPENDENT'
                # The earliest print hook has already enforced the fault
                # latch. At the post-heatsoak gate, trust nothing stale:
                # recheck homing, individual motors, offsets and temperature.
                if (reuse_candidate and self.reuse_enabled() and
                    not self.flag('auto_xy_v2_measure_only')):
                    eligible,why=self._reuse_check(check_thermal=True)
                    if eligible:
                        gcmd.respond_info('AUTO XY: prior calibration VERIFIED and still valid after heatsoak; skipping VAOC and offset writes')
                        self.last_reuse={'status':'REUSED','source':self.verified_session['report']}
                        self.active_print_mode=None
                        return
                    gcmd.respond_info('AUTO XY: reuse not permitted (%s); performing normal pre-Beacon calibration' % why)
        if mode=='IDLE' and self.sts('print_stats')['state'] in ('printing','paused'):
            raise gcmd.error('Manual Auto XY only allowed while idle')
        if mode=='IDLE' and not self._retry_precommitted:
            self.ensure_commissioned(gcmd)
        # Missing initial homing is a prerequisite, NOT a calibration failure.
        # Check before setting busy, creating a report, saving IN_PROGRESS,
        # starting VAOC, moving, or writing offsets. Do not auto-home because
        # a G28 may have physical consequences beyond the camera envelope.
        # An already latched fault was deliberately checked ABOVE this guard;
        # losing homing during an active run remains a hard failure in ensure_ok.
        if not self._retry_precommitted:
            homed=str(self.sts('toolhead').get('homed_axes',''))
            if not all(axis in homed for axis in 'xyz'):
                self.last_error='Printer must be homed first'
                if mode=='IDLE':
                    gcmd.respond_info('AUTO XY: Printer must be homed first (G28). No calibration started; no fault latched.')
                    return
                # Never silently bypass calibration on a two-tool PRINT:
                # stop START_PRINT with a normal G-code error, without a
                # persistent Auto XY fault or an interrupted-run marker.
                self.active_print_mode=None
                raise gcmd.error('AUTO XY: Printer must be homed first (G28). Print start stopped; no Auto XY fault latched.')
        # Experimental approaches have not demonstrated physical offset-write
        # accuracy. Explicitly gate their write access until separate acceptance.
        if (self.settling_path!='original' and
                not self.flag('auto_xy_v2_measure_only') and
                not self.allow_experimental_alignment_writes):
            raise gcmd.error('RC14 experimental settling is MEASURE-ONLY until hardware qualified; use AUTO_XY_MEASURE_ONLY_ON')
        # All known *initial* prerequisites belong before busy/VAOC/offset
        # markers. An operator setup mistake is not an interrupted calibration.
        if not self._retry_precommitted:
            if int(self.sts('gcode_macro RatOS').get('default_toolhead',0))!=0:
                raise gcmd.error('AUTO XY requires default toolhead T0; no calibration started')
            if self.sts('gcode_macro _VAOC').get('is_started'):
                raise gcmd.error('AUTO XY: VAOC is already active; exit it safely before calibration')
            if mode=='PRINT':
                macro=self.sts('gcode_macro START_PRINT')
                if str(macro.get('is_printing_gcode','false')).lower() in ('true','1'):
                    raise gcmd.error('AUTO XY: wrong START_PRINT phase; no calibration started')
            # Thermal waits are prerequisites, not alignment safety faults.
            # Only the explicitly enabled slicer-derived path issues M104.
            if mode=='PRINT' and self.thermal_mode=='slicer_derived':
                self.start_slicer_thermal_preflight(gcmd)
            else:
                self.wait_for_safe_camera_preflight(gcmd)
            try:
                self.tool()  # catches unsupported COPY/MIRROR state in manual mode
                self.check_camera_temps()
            except GateAbort as e:
                if self._original_heater_targets is not None:
                    self.restore_preflight_heaters()
                raise gcmd.error('AUTO XY prerequisite not met: '+str(e))
        retry_ctx=self._retry_context if self._retry_precommitted else None
        if not self._retry_precommitted:
            self._retry_context=None  # stale context after operator dismissed and manually recovered
        self._invalidate_verified_session('New calibration started')
        self.busy=True
        self._vision_session_invalid=False
        self.phase='STARTING'
        self._progress_stage=None
        self.last_error=''
        self.directory=None
        self.delta=list(retry_ctx['delta']) if retry_ctx else [0.,0.]
        self.active=retry_ctx['active'] if retry_ctx else 0
        self.count=0
        self._diag_last_motion_completed_s=None
        self._diag_previous_snapshot_started_s=None
        self.needs_reconcile=bool(retry_ctx and maximum(vecsub(retry_ctx['expected_offsets'],retry_ctx['initial_offsets']))>1e-6)
        self.vaoc_entered=False
        self.temp_guard_active=False
        self.deadline=self.reactor.monotonic()+900.0
        self.report={'status':'STARTING','mode':mode,'hook_point':'after_heating_bed_pre_beacon' if mode=='PRINT' else 'manual',
                     'target_tolerance_mm':self.tol,'one_time_retry':self._retry_precommitted,
                     'settling_path':self.settling_path,'camera_pair_gap':self.camera_pair_gap,
                     'camera_diagnostics_enabled':self.camera_diagnostics_enabled,
                     'settle_s':self.settle,
                     'camera_worker_mode':self.camera_worker_mode,
                     'camera_detector_mode':self.camera_detector_mode,
                     'commissioned_detector_guard':self.commissioned_detector_guard,
                     'max_corrections':self.max_correct,'max_verifications':self.max_verify,
                     'max_step_mm':self.max_step,'max_total_change_mm':self.max_total,
                     'started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'original_offsets':None,
                     'requested_idex_mode':self.active_print_mode if mode=='PRINT' else 'MANUAL',
                     'operation_mode':'ENDURANCE_AUTO_CALIBRATE' if endurance_requested else ('MEASURE_ONLY' if self.flag('auto_xy_v2_measure_only') else 'AUTO_CALIBRATE'),
                     'reuse_active_vaoc':bool(retry_ctx),
                     'prior_failed_run':retry_ctx['previous_report_dir'] if retry_ctx else None,
                     'inline_print_retry_enabled':bool(self.inline_print_retry),
                     'thermal_preflight':self._thermal_snapshot}
        original=None
        success=False
        try:
            self.ensure_ok()
            if mode=='PRINT':
                # This hook is AFTER bed heatsoak and BEFORE Beacon contact true-zero
                # heating (possibly 170 C) in _START_PRINT_AFTER_HEATING_BED.
                # RatOS is currently inside START_PRINT; it must NOT already be
                # in ordinary post-start printing state.
                macro=self.sts('gcode_macro START_PRINT')
                if str(macro.get('is_printing_gcode','false')).lower() in ('true','1'):
                    raise GateAbort('Pre-hotend heating hook phase not detected')
            if int(self.sts('gcode_macro RatOS').get('default_toolhead',0))!=0:
                raise GateAbort('This commissioned system requires default T0')
            if self.sts('gcode_macro _VAOC').get('is_started') and not retry_ctx:
                raise GateAbort('VAOC already active at print-start gate; finish earlier VAOC first')
            initial_tool=retry_ctx['initial_tool'] if retry_ctx else self.tool()
            self.report['initial_tool']=initial_tool
            original=list(retry_ctx['initial_offsets']) if retry_ctx else self.offsets()
            if retry_ctx:self.report['retry_start_offsets']=self.offsets()
            if not all(math.isfinite(q) for q in original):
                raise GateAbort('Nonfinite saved offsets')
            self.report['original_offsets']=original
            # Persist initial report before changing anything.
            stamp=time.strftime('%Y%m%d-%H%M%S')
            stem=stamp+'_'+mode.lower()
            for suffix in range(100):
                candidate=os.path.join(self.results,stem+('' if suffix==0 else '_%02d'%suffix))
                if not os.path.exists(candidate):
                    self.directory=candidate
                    break
            else:
                raise GateAbort('Unable to allocate a unique report folder')
            os.makedirs(self.directory,exist_ok=False)
            self.save_report()
            # Persistent marker survives a power loss or Klipper crash. A new
            # print must never silently continue after an interrupted save.
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_in_progress VALUE=1')
            if not retry_ctx:
                # A newly acknowledged fresh run gets a fresh one-time token.
                # Do NOT reset it when entering the one-time retry itself.
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_used VALUE=0')
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
            self.phase='CAMERA_TEMP_GUARD'
            self.prepare_camera_at_preheat(gcmd)
            self.phase='VAOC_ENTER'
            if retry_ctx:
                gcmd.respond_info('AutoXY: reusing STILL-ACTIVE VAOC for the one permitted retry')
                self.vaoc_entered=True
                self.base=list(retry_ctx['base'])
                self.verify_pose()  # position was already read-only verified
                self.move('x',0.);self.move('y',0.)
                self.select(0)
                self.verify_pose()
            else:
                gcmd.respond_info('AutoXY: entering VAOC after bed heatsoak, BEFORE Beacon probing and final hotend heat')
                self._diag_event('native_vaoc_start_begin')
                self.run_gcode('_VAOC_START')
                self.vaoc_entered=True
                self._diag_motion_complete('native_vaoc_start_complete')
                self._diag_event('initial_t0_load_begin')
                self.run_gcode('_VAOC_LOAD_TOOL T=0')
                self.active=0;self.delta=[0.,0.]
                self.base=self.cp()
                self.verify_pose()
                self._diag_motion_complete('initial_t0_load_complete')
            self.phase='CALIBRATE'
            gcmd.respond_info('AUTO XY: measuring live camera scale T0/T1')
            self.progress_stage(gcmd,'SCALING','Measuring T0 and T1 camera scale')
            self.worker_capture('preflight_t0')
            m0=self.calibrate(0);m1=self.calibrate(1)
            discrepancy=[]
            for j in (0,1):
                a=[m0[0][j],m0[1][j]];b=[m1[0][j],m1[1][j]]
                discrepancy.append(norm(vecsub(a,b))/max(.1,norm(vecmul(vecadd(a,b),.5))))
            if max(discrepancy)>.12: raise GateAbort('T0/T1 camera scale disagreement > 12%')
            mat=[[.5*(m0[i][j]+m1[i][j]) for j in (0,1)] for i in (0,1)]
            self.report['camera_transform']=mat
            self.report['camera_disagreement_percent']=[round(q*100,3) for q in discrepancy]
            self.save_report()
            self.select(0)
            # Capture and compare independent tool-change sequences before
            # permitting the first offset write or any response-gain test.
            self.settle_toolshifts(mat,gcmd)
            # Observation-only commissioning mode: never use _VAOC_SET_TOOL.
            # A failed tolerance check aborts the print and retains the usual
            # VAOC fault, because a failed in-VAOC run needs safe recovery.
            observation_only=self.flag('auto_xy_v2_measure_only')
            if observation_only:
                self.phase='MEASURE_ONLY'
                self.progress_stage(gcmd,'CONFIRMATION','Measurement-only alignment checks; no offset changes')
                readings=[]
                for check in (1,2):
                    readings.append(self.measure(mat,'measure_only_%02d'%check))
                observed_delta=vecsub(readings[1],readings[0])
                self.report['measure_only_results_mm']=readings
                self.report['measure_only_repeatability_mm']=observed_delta
                self.report['corrections_applied']=0
                self.report['verification_attempts']=2
                self.save_report()
                if maximum(observed_delta)>.015 or any(maximum(r)>self.tol for r in readings):
                    raise GateAbort('MEASURE_ONLY alignment outside tolerance or not repeatable; no saved offsets changed')
                err=readings[1]
                correction_count=0
                verification_count=2
                success=True
                self.report['status']='MEASURE_ONLY_VERIFIED'
                self.report['final_error_mm']=err
                self.report['final_offsets']=self.offsets()
                self.save_report()
                gcmd.respond_info('AUTO XY MEASURE ONLY: stable, in-tolerance X=%+.5f Y=%+.5f mm; no offsets changed'%(err[0],err[1]))
            correction_count=0
            verification_count=2 if observation_only else 0
            streak=0
            previous=None
            prior_step=None
            # Each pass either applies ONE bounded adjustment or consumes ONE
            # no-write verification. There can never be more than the sum.
            for cycle in range(1,self.max_correct+self.max_verify+1) if not observation_only else ():
                self.phase='MEASURING'
                err=self.measure(mat,'cycle_%02d'%cycle)
                gcmd.respond_info('AUTO XY check %d/%d: X=%+.4f Y=%+.4f mm' % (
                    cycle,self.max_correct+self.max_verify,err[0],err[1]))
                if prior_step is not None:
                    response=vecsub(err,previous)
                    anomalous=[]
                    for i in (0,1):
                        if abs(prior_step[i])>=.015:
                            gain=response[i]/prior_step[i]
                            if not math.isfinite(gain) or not math.isfinite(response[i]):
                                raise GateAbort('Offset response invalid: non-finite response')
                            if not (.2<=gain<=2.) or abs(response[i])>.11:
                                anomalous.append((i,gain))
                    if anomalous:
                        self.record('response_gain_anomalies',cycle=cycle,
                            axes=[a for a,g in anomalous],
                            gains=[g for a,g in anomalous],
                            before_error_mm=previous,after_error_mm=err,
                            applied_step_mm=prior_step,
                            response_mm=response,
                            measured_alignment_in_tolerance=maximum(err)<=self.tol)
                        # A gross physical displacement or reverse response
                        # remains fatal regardless of any apparently good
                        # isolated alignment photo. Bounds are independent of
                        # gain; <=0.11 mm observed response is still required.
                        for axis,gain in anomalous:
                            if abs(response[axis])>.11 or gain<=0:
                                raise GateAbort('Offset response invalid: gross/reverse axis %d gain %.3f'%(axis,gain))
                        # Out of tolerance? Do not write a further correction
                        # based on an anomalous response. Stop and rollback.
                        # In tolerance? Confirm two *independent* passes, even
                        # on later corrections and with both axes anomalous.
                        if maximum(err)>self.tol:
                            axis,gain=anomalous[0]
                            raise GateAbort('Offset response invalid: axis %d gain %.3f; alignment outside tolerance'%(axis,gain))
                        self.progress_stage(gcmd,'CONFIRMATION','Checking unusual response with extra camera measurements')
                        confirmed=self.confirm_anomalous_alignment(
                            mat,cycle,err,anomalous,verification_count,gcmd)
                        verification_count+=2
                        self.report['measurement_cycles']=cycle
                        self.report['verification_attempts']=verification_count
                        self.report['corrections_applied']=correction_count
                        self.report['status']='CALIBRATED'
                        self.report['final_error_mm']=confirmed
                        self.report['final_offsets']=self.offsets()
                        self.save_report()
                        err=confirmed
                        success=True
                        break
                    prior_step=None
                action,verification_count,streak=self.classify_measurement(
                    err,correction_count,verification_count,streak)
                self.report['measurement_cycles']=cycle
                self.report['verification_attempts']=verification_count
                self.report['corrections_applied']=correction_count
                self.save_report()
                if action=='VERIFY_EXHAUSTED':
                    raise GateAbort('Verification limit reached (%d) without two consecutive passes' % self.max_verify)
                if action=='CORRECTION_EXHAUSTED':
                    raise GateAbort('Correction limit reached (%d) with axis still outside tolerance' % self.max_correct)
                if action=='COMPLETE':
                    self.progress_stage(gcmd,'CONFIRMATION','XY alignment checks passed')
                    success=True
                    self.report['status']='CALIBRATED'
                    self.report['final_error_mm']=err
                    self.report['final_offsets']=self.offsets()
                    self.save_report()
                    break
                if action=='VERIFY_AGAIN':
                    self.progress_stage(gcmd,'CONFIRMATION','Verifying XY alignment with fresh camera images')
                    gcmd.respond_info('AUTO XY verification %d/%d: in tolerance, %d/2 consecutive; rechecking' % (
                        verification_count,self.max_verify,streak))
                    continue
                step=[max(-self.max_step,min(self.max_step,-.8*x)) if abs(x)>self.tol else 0. for x in err]
                # First ever Y change on this installation was not independently
                # verified in Stage 4C; retain the Stage 5 conservative 0.020 mm cap.
                if correction_count==0:step[1]=max(-self.first_y_max_step,min(self.first_y_max_step,step[1]))
                self.phase='WRITING_OFFSETS'
                previous=err
                prior_step=step
                self.progress_stage(gcmd,'ADJUSTMENT','Applying bounded XY offset correction')
                gcmd.respond_info('AUTO XY correction %d/%d: X=%+.4f Y=%+.4f mm' % (
                    correction_count+1,self.max_correct,step[0],step[1]))
                self.apply(step,original)
                correction_count+=1
                self.report['corrections_applied']=correction_count
                self.save_report()
                self.phase='VERIFYING'
                self.progress_stage(gcmd,'CONFIRMATION','Checking measured response after correction')
            if not success: raise GateAbort('Calibration did not converge')
            if endurance_requested:
                # Verified offsets must remain intact if the later diagnostic
                # loop fails; this phase is not allowed to write any offsets.
                self.report['endurance_calibration_verified']=True
                self.report['endurance_calibration_final_error_mm']=list(err)
                self.report['endurance_calibration_offsets']=list(self.offsets())
                self.save_report()
                self.needs_reconcile=False
                self._run_endurance(mat,gcmd)
            self.phase='VAOC_EXIT'
            self.select(0)
            self.run_gcode('_VAOC_END')
            self.vaoc_entered=False
            if self.tool()!=initial_tool:
                raise GateAbort('RatOS VAOC exit did not restore original tool selection')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_in_progress VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_used VALUE=0')
            self.report['status']=('ENDURANCE_COMPLETE_AND_VAOC_EXITED' if endurance_requested else
                ('MEASURE_ONLY_VERIFIED_AND_VAOC_EXITED' if observation_only else 'CALIBRATED_AND_VAOC_EXITED'))
            self.save_report()
            if not observation_only and not endurance_requested:
                self._store_verified_session()
            if self.report.get('response_gain_recheck_status')=='CONFIRMED_IN_TOLERANCE':
                gcmd.respond_info('AUTO XY WARNING: unusual offset response but stable in-tolerance alignment confirmed twice; inspect report.json.')
            self.progress_stage(gcmd,'COMPLETE',
                'Endurance diagnostics finished; no print is authorised' if endurance_requested else (
                'XY calibration verified; returning to RatOS print startup' if mode=='PRINT'
                else 'XY calibration verified; standalone run complete'))
            gcmd.respond_info('AUTO XY: VERIFIED X=%+.5f Y=%+.5f mm after %d adjustment(s), %d verification(s); %s'%(
                err[0],err[1],correction_count,verification_count,
                'diagnostic endurance complete; no print' if endurance_requested else 'proceeding.'))
        except (Exception,KeyboardInterrupt) as exc:
            logging.exception('AUTO XY gated calibration failed')
            self.last_error=str(exc)
            self.report['status']='FAILED_DO_NOT_PRINT'
            self.report['error']=str(exc)
            if endurance_requested and self.report.get('endurance'):
                self.report['endurance']['status']='FAILED'
                self.report['endurance']['error']=str(exc)
                try:self._endurance_files(include_charts=True)
                except Exception:logging.exception('Endurance graph/report generation failed')
            if self.directory:self.save_report_best_effort()
            # Active-VAOC retry is available ONLY for a classified measurement
            # or convergence issue, while the live pose and all persisted saved
            # writes agree. No rollback when eligible: the next calibration
            # continues from those exact saved values with the ORIGINAL
            # cumulative change cap, avoiding saved/runtime compensation split.
            retry_possible=False
            try:
                retry_possible=self.retryable_failure(exc,original)
            except Exception:
                logging.exception('Auto XY retry eligibility could not be established')
                retry_possible=False
            # The original print remains inside START_PRINT until this gate
            # returns. On a safely classified measurement/convergence error,
            # retry immediately IN PLACE once, without a prompt, without
            # aborting the print, and without disabling the saved ON switch.
            # The nested attempt MUST either exit VAOC and verify, or raise a
            # fatal error and latch the normal fault. Never retry recursively.
            inline_result=self.try_inline_print_retry(exc,gcmd,retry_possible)
            if inline_result is True:
                return
            if inline_result is False:
                retry_possible=False
                self._retry_context=None
            if not retry_possible:
                try:
                    if original is not None and self.needs_reconcile:
                        self.report['rollback']=self.rollback(original)
                except Exception as rollback_error:
                    self.report['rollback']='FAILED: '+str(rollback_error)
            else:
                self.report['rollback']='DEFERRED: trusted current saved offsets retained for one ACTIVE-VAOC retry'
            try:
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0')
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=1')
                self.report['auto_xy_switched_off']=True
            except Exception:
                self.report['auto_xy_switched_off']=False
                retry_possible=False
            try:
                if retry_possible:
                    for axis,key in enumerate(('auto_xy_v2_retry_x','auto_xy_v2_retry_y','auto_xy_v2_retry_z')):
                        self.run_gcode('SAVE_VARIABLE VARIABLE=%s VALUE=%.10f'%(key,self._retry_context['expected_offsets'][axis]))
                    self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=1')
                else:
                    self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
                    self._retry_context=None
            except Exception:
                retry_possible=False
                self._retry_context=None
                try:self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
                except Exception:pass
            self.report['retry_offered']=retry_possible
            self.report['retry_used']=self.flag('auto_xy_v2_retry_used')
            if self.directory:self.save_report_best_effort()
            if not getattr(self,'_protected_inside_gate',False) and not endurance_requested:
                self.prompt_on_failure(retry_possible)
            # Do not call _VAOC_END after failure. Retry is a separate,
            # operator-triggered command issued AFTER the original print aborts.
            raise gcmd.error(('AUTO XY FAILED; PRINT MUST NOT CONTINUE: ' if mode=='PRINT' else 'AUTO XY STANDALONE CALIBRATION FAILED: ')+str(exc))
        finally:
            self._stop_vision_process()
            self.busy=False
            self.temp_guard_active=False
            self.phase='IDLE'
            if not self._retry_precommitted:
                # Keep thermal ownership across an inline attempt, then restore
                # RatOS's original targets at the outermost gate exit.
                self.restore_preflight_heaters()
                self._thermal_snapshot=None

    # -------- the earlier protected-print revision opt-in protected original-file manual VAOC handoff --------
    # This code is inactive on ordinary START_PRINT and only operates when a
    # AutoXY-transformed file explicitly invokes the separate two-phase barrier.

    def _protected_printer_file(self):
        st=self.sts('print_stats')
        filename=str(st.get('filename') or '')
        state=str(st.get('state') or '').lower()
        if not filename or state not in ('printing','paused'):
            raise GateAbort('AutoXY requires an active virtual-SD print file')
        return filename,state

    def _protected_state(self, expected=None, paused=None):
        if not self.protected_print_enabled:
            raise GateAbort('AutoXY is disabled by configuration; trial is not armed')
        s=self._protected_session
        if not s or s.get('invalid'):
            raise GateAbort('No valid AutoXY in-memory job; cancel this file')
        filename,state=self._protected_printer_file()
        if filename!=s['filename']:
            s['invalid']=True
            raise GateAbort('AutoXY print filename changed; refusing stale authorization')
        if self.reactor.monotonic()-s['started']>2700.:
            s['invalid']=True
            raise GateAbort('AutoXY print-start recovery timed out')
        if expected is not None and s['phase']!=expected:
            raise GateAbort('AutoXY state %s expected %s'%(s['phase'],expected))
        if paused is True and state!='paused':
            raise GateAbort('AutoXY requires paused original virtual-SD file')
        if paused is False and state!='printing':
            raise GateAbort('AutoXY requires actively running original file')
        if self.printer.get_state_message()[1]!='ready':
            raise GateAbort('Klipper is not ready')
        axes=str(self.sts('toolhead')['homed_axes'])
        if not all(x in axes for x in 'xyz'):
            raise GateAbort('AutoXY lost XYZ homing')
        if s.get('motor_signature') is not None and self._motion_motor_signature()!=s['motor_signature']:
            s['invalid']=True
            raise GateAbort('AutoXY motion stepper enable state changed; cancel held file')
        bed=self.sts('heater_bed')
        temp=float(bed['temperature']);target=float(bed['target'])
        if not (math.isfinite(temp) and math.isfinite(target)):
            raise GateAbort('AutoXY bed temperatures invalid')
        if abs(target-s['bed_target'])>.2 or abs(temp-s['bed_target'])>3.:
            raise GateAbort('AutoXY heatsoak no longer valid; cancel and restart normally')
        return s

    def cmd_AUTOXY_PRINT_BEGIN(self,gcmd):
        if not self.protected_print_enabled:
            raise gcmd.error('AutoXY trial disabled: do not start transformed print')
        if self.busy or self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
            raise gcmd.error('AutoXY cannot arm: AutoXY fault or incomplete calibration')
        if self.sts('gcode_macro _VAOC').get('is_started'):
            raise gcmd.error('AutoXY cannot arm: VAOC already active')
        if not self.enabled():
            raise gcmd.error('AutoXY requires enabled AutoXY; measure-only is supported')
        filename,state=self._protected_printer_file()
        if state!='printing': raise gcmd.error('AutoXY BEGIN must run from file before heatsoak')
        target=gcmd.get_float('BED_TEMP',above=0.)
        self._protected_session={'filename':filename,'started':self.reactor.monotonic(),
                           'bed_target':target,'phase':'PREPARING','invalid':False,
                           'initial_offsets':tuple(self.offsets()),'manual_seen':False}
        gcmd.respond_info('AutoXY | opt-in guarded two-phase preheat begun for '+filename)

    def cmd_AUTOXY_PRINT_CALIBRATE(self,gcmd):
        s=self._protected_state('PREPARING',paused=False)
        if self.sts('gcode_macro _VAOC').get('is_started'):
            raise gcmd.error('AutoXY VAOC unexpectedly active after heatsoak')
        # the previous calibration engine's early _USER_START_PRINT hook has already captured mode.
        # Switching AUTOXY_OFF during heatsoak is NOT a successful calibration.
        if not self.enabled() or self.pending_print_mode!='INDEPENDENT' or not self.pending_toolshifts:
            s['invalid']=True
            raise gcmd.error('AutoXY no longer has an enabled independent two-tool AutoXY gate')
        if self.reuse_enabled():
            s['invalid']=True
            raise gcmd.error('AutoXY trial refuses session-reuse shortcut; turn reuse OFF')
        if tuple(self.offsets())!=s['initial_offsets']:
            s['invalid']=True
            raise gcmd.error('AutoXY saved offsets changed during bed heatsoak')
        # Ensure individually disabled stepper motors cannot bypass homed_axes.
        motor_sig=self._motion_motor_signature()
        if motor_sig is None:
            s['invalid']=True
            raise gcmd.error('AutoXY cannot prove all motion motors enabled; no calibration started')
        s['motor_signature']=motor_sig
        # The normal native post-heatsoak AutoXY hook is omitted ONLY from the
        # separately generated AutoXY_PHASE2 macro; this is its guarded replacement.
        previous_directory=getattr(self,'directory',None)
        self._protected_inside_gate=True
        try:
            self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd,'PRINT'))
        except Exception as exc:
            report=getattr(self,'report',None) or {}
            error=str(report.get('error') or '')
            prefixes=('MEASURE_ONLY alignment outside tolerance',
                      'Camera/nozzle detection failed at ',
                      'Camera scale fit/zero repeatability failed',
                      'Camera return/scatter exceeds tolerance',
                      'Initial toolshift settling failed:',
                      'Verification limit reached',
                      'T0/T1 camera scale disagreement')
            # For now, after ANY attempted write to T1 saved offsets, do NOT
            # offer same-print manual fallback. Saved/runtime reconcile is not
            # yet safely provable from status alone. Abort this original file.
            if (report.get('status')!='FAILED_DO_NOT_PRINT' or
                not error.startswith(prefixes) or
                report.get('corrections_applied',0) or
                report.get('write_intents') or
                tuple(self.offsets())!=s['initial_offsets'] or
                not self.flag('auto_xy_v2_fault') or
                not self.flag('auto_xy_v2_in_progress')):
                s['invalid']=True
                raise gcmd.error('AutoXY HARD FAILURE; original print must stop: '+str(exc))
            # the unified protected-print flow: hold the original file, offering ONE operator-selected
            # active-VAOC replay when the underlying engine positively proved
            # its retry context. Otherwise go directly to native manual VAOC.
            eligible=(self.flag('auto_xy_v2_retry_available') and
                      not self.flag('auto_xy_v2_retry_used') and
                      self._retry_context is not None)
            s['phase']='RETRY_REQUIRED' if eligible else 'MANUAL_REQUIRED'
            s['reason']=error
            s['retry_used']=False
            gcmd.respond_info('AutoXY | recoverable no-write failure; original print will be HELD. '
                              + ('One guarded retry available. ' if eligible else 'Manual VAOC required. ')
                              + error)
        else:
            report=getattr(self,'report',None) or {}
            if (not self.enabled() or self.directory==previous_directory or
                report.get('status') not in (
                    'CALIBRATED_AND_VAOC_EXITED',
                    'MEASURE_ONLY_VERIFIED_AND_VAOC_EXITED') or
                self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress')):
                s['invalid']=True
                raise gcmd.error('AutoXY automatic run returned without fresh verified calibration')
            s['phase']='AUTO_VERIFIED'
            gcmd.respond_info('AutoXY | fresh automatic XY verified; no pause required')
        finally:
            self._protected_inside_gate=False

    def _protected_failure_description(self):
        """Display-only explanation. Never influences a recovery/print safety decision."""
        s=self._protected_session or {}
        reason=str(s.get('reason') or '')
        if reason.startswith('MEASURE_ONLY alignment outside tolerance'):
            report=getattr(self,'report',None) or {}
            readings=report.get('measure_only_results_mm')
            if isinstance(readings,list) and len(readings)>=2:
                try:
                    a,b=readings[-2:]
                    if all(isinstance(t,(list,tuple)) and len(t)>=2 for t in (a,b)):
                        values=[float(a[0]),float(a[1]),float(b[0]),float(b[1])]
                        if all(math.isfinite(v) and abs(v)<10 for v in values):
                            return ('Camera measured the nozzles but the alignment was outside tolerance or unstable. '
                                    'X=%.4f/%.4f mm; Y=%.4f/%.4f mm. No automatic offsets were written.'
                                    % (values[0],values[2],values[1],values[3]))
                except (TypeError,ValueError,OverflowError):
                    pass
            return 'Camera measured the nozzles, but alignment was outside tolerance or unstable. No automatic offsets were written.'
        if reason.startswith('Camera/nozzle detection failed at '):
            return 'Camera nozzle registration failed. Check nozzle condition and camera visibility; do not move the carriage while held.'
        if reason.startswith('Camera scale fit/zero repeatability failed'):
            return 'Camera scale or position repeatability failed; alignment was not verified.'
        if reason.startswith('Initial toolshift settling failed'):
            return 'Nozzle seating did not stabilise; alignment was not verified.'
        if reason.startswith('Verification limit reached'):
            return 'The camera could not confirm nozzle alignment within the allowed verification checks.'
        return 'AutoXY did not verify nozzle alignment. Review the console/report before continuing.'

    def _protected_prompt(self, page):
        # UI messages are advisory only; never authorize calibration or resume.
        emit=self.gcode.respond_raw
        if page=='retry':
            emit('// action:prompt_begin AutoXY calibration retry available')
            emit('// action:prompt_text Original print HELD after heatsoak. No print was cancelled.')
            emit('// action:prompt_text '+self._protected_failure_description())
            emit('// action:prompt_text Retry AutoXY once or use native manual VAOC.')
            emit('// action:prompt_text Do NOT use normal RESUME or M24.')
            emit('// action:prompt_button Start native VAOC instead|AUTOXY_MANUAL_START|secondary')
            emit('// action:prompt_button Cancel held print|CANCEL_PRINT|error')
            emit('// action:prompt_footer_button Retry AutoXY once|AUTOXY_PRINT_RETRY|primary')
        elif page=='initial':
            emit('// action:prompt_begin AutoXY manual VAOC required')
            emit('// action:prompt_text Original print HELD after heatsoak; do not use normal RESUME or M24.')
            emit('// action:prompt_text '+self._protected_failure_description())
            emit('// action:prompt_text Start native RatOS VAOC. This prompt will close so you can use its controls.')
            emit('// action:prompt_text Stay with the heated printer. Cancel if you cannot safely finish.')
            emit('// action:prompt_button Cancel held print|CANCEL_PRINT|error')
            emit('// action:prompt_footer_button Start native VAOC|AUTOXY_MANUAL_START|primary')
        elif page=='returned':
            emit('// action:prompt_begin AutoXY manual VAOC finished')
            emit('// action:prompt_text Native VAOC is closed; the original print is still held.')
            emit('// action:prompt_text Only continue if you completed and saved manual alignment.')
            emit('// action:prompt_text Accept & Continue uses the existing guarded original-file path. No AutoXY remeasurement.')
            emit('// action:prompt_button Cancel held print|CANCEL_PRINT|error')
            emit('// action:prompt_footer_button Accept and continue original print|AUTOXY_MANUAL_ACCEPT_CONTINUE|primary')
        elif page=='authorized':
            emit('// action:prompt_begin AutoXY manual alignment accepted')
            emit('// action:prompt_text Original file is still held. Continue only if the printer is safe.')
            emit('// action:prompt_footer_button Continue original print|AUTOXY_PRINT_CONTINUE|primary')
        else:
            raise ValueError('Unknown AutoXY popup page')
        emit('// action:prompt_show')

    def _protected_manual_popup_tick(self, eventtime):
        # A native _VAOC_END occurs independently of our Klipper extension.
        # Observe its is_started transition while the SAME file is paused;
        # never poll/schedule G-code, issue M24 or clear faults here.
        try:
            s=self._protected_session
            if (s is not None and s.get('phase')=='MANUAL_ACTIVE' and
                s.get('manual_seen') and not s.get('return_prompt_shown')):
                self._protected_state('MANUAL_ACTIVE',paused=True)
                if not self.sts('gcode_macro _VAOC').get('is_started'):
                    # Mark first so transient front-end errors cannot emit a
                    # repeated or competing popup over RatOS's VAOC UI.
                    s['return_prompt_shown']=True
                    self._protected_prompt('returned')
        except GateAbort:
            # Hold stays guarded; an invalid print or changed bed never gets
            # a misleading Continue prompt. Operator can cancel normally.
            pass
        except Exception:
            logging.exception('AutoXY manual recovery popup observer failed')
        return eventtime+2.

    def cmd_AUTOXY_RECOVERY_SHOW(self,gcmd):
        # Optional manual redraw if the front end discards a prompt.
        s=self._protected_state(paused=True)
        if s['phase']=='RETRY_REQUIRED': page='retry'
        elif s['phase']=='MANUAL_REQUIRED': page='initial'
        elif s['phase']=='MANUAL_ACTIVE':
            if self.sts('gcode_macro _VAOC').get('is_started'):
                raise gcmd.error('Finish native VAOC before showing the recovery prompt')
            if not s.get('manual_seen'):
                raise gcmd.error('No fresh native manual VAOC session')
            page='returned'
            s['return_prompt_shown']=True
        elif s['phase']=='MANUAL_AUTHORIZED':page='authorized'
        else: raise gcmd.error('No waiting AutoXY manual recovery prompt')
        self._protected_prompt(page)

    def cmd_AUTOXY_PRINT_HOLD(self,gcmd):
        s=self._protected_state(paused=False)
        if s['phase']=='AUTO_VERIFIED':return
        if s['phase'] not in ('RETRY_REQUIRED','MANUAL_REQUIRED'):
            raise gcmd.error('AutoXY cannot pause from '+s['phase'])
        # M25 here pauses the FILE between standalone G-code commands.
        # The next file command AUTOXY_PRINT_CHECK is the fail-closed barrier.
        self.gcode.run_script_from_command('M25')
        gcmd.respond_info('AutoXY | original print HELD after heatsoak; choose guarded Retry (if offered) or native manual VAOC. Do NOT use normal RESUME/M24.')
        try:
            self._protected_prompt('retry' if s['phase']=='RETRY_REQUIRED' else 'initial')
        except Exception:
            logging.exception('AutoXY could not show optional Mainsail handoff prompt')

    def cmd_AUTOXY_PRINT_RETRY(self,gcmd):
        """One operator-authorised retry while the SAME virtual-SD file is HELD.

        Never release file until the engine has freshly verified alignment and
        exited VAOC. A second no-write failure goes to native manual VAOC.
        Any uncertain saved/runtime write is a HARD STOP, not a manual fallback.
        """
        s=self._protected_state('RETRY_REQUIRED',paused=True)
        if self.busy:
            raise gcmd.error('AutoXY retry refused: calibration busy')
        if s.get('retry_used'):
            raise gcmd.error('AutoXY retry already consumed')
        if not self.flag('auto_xy_v2_retry_available') or self.flag('auto_xy_v2_retry_used'):
            raise gcmd.error('AutoXY retry token invalid: use native manual VAOC')
        if not self.flag('auto_xy_v2_fault') or not self.flag('auto_xy_v2_in_progress'):
            raise gcmd.error('AutoXY retry expected latched failed AutoXY state')
        if self.enabled():
            raise gcmd.error('AutoXY retry expected AutoXY disabled after failure')
        if tuple(self.offsets())!=s['initial_offsets']:
            s['invalid']=True
            raise gcmd.error('AutoXY retry refused: saved offsets changed')
        ctx=self._retry_context
        # Verify camera pose and saved/runtime state before consuming the token
        # or issuing ANY physical movement. A failed guard preserves the hold.
        self.validate_active_vaoc_context(ctx)
        s['retry_used']=True
        s['phase']='RETRY_ACTIVE'
        # Power-loss-safe failure latch and one-shot token commitment.
        try:
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_used VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=1')
        except Exception:
            s['invalid']=True
            raise gcmd.error('AutoXY retry could not safely commit its one-use token; cancel held file')
        self.gcode.respond_raw('// action:prompt_end')
        gcmd.respond_info('AutoXY | RETRY: Rechecking camera alignment once; original print remains HELD')
        self._retry_precommitted=True
        self._protected_inside_gate=True
        prior_dir=self.directory
        heaters_restored=False
        try:
            try:
                self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd,'PRINT'))
            except Exception as exc:
                r=getattr(self,'report',None) or {}
                why=str(r.get('error') or str(exc))
                safe_no_write=(r.get('status')=='FAILED_DO_NOT_PRINT' and
                               not r.get('corrections_applied',0) and
                               not r.get('write_intents') and
                               tuple(self.offsets())==s['initial_offsets'] and
                               self.flag('auto_xy_v2_fault') and
                               self.flag('auto_xy_v2_in_progress'))
                if not safe_no_write:
                    s['invalid']=True
                    raise gcmd.error('AutoXY retry HARD FAILURE; original print cannot resume: '+str(exc))
                s['phase']='MANUAL_REQUIRED';s['reason']=why
                gcmd.respond_info('AutoXY | RECOVERY: Retry failed; original print remains HELD. Use native manual VAOC.')
                self._protected_prompt('initial')
                return
            r=getattr(self,'report',None) or {}
            if (self.directory==prior_dir or
                r.get('status') not in ('CALIBRATED_AND_VAOC_EXITED',
                                      'MEASURE_ONLY_VERIFIED_AND_VAOC_EXITED') or
                not self.enabled() or self.flag('auto_xy_v2_fault') or
                self.flag('auto_xy_v2_in_progress') or
                self.sts('gcode_macro _VAOC').get('is_started')):
                s['invalid']=True
                raise gcmd.error('AutoXY retry returned without fresh verified calibration; CANCEL original print')
            # Restore RatOS's original nozzle targets BEFORE releasing M24.
            # Otherwise resumed post-bed startup may race the delayed restore.
            try:
                self.restore_preflight_heaters()
                heaters_restored=True
            except Exception as exc:
                s['invalid']=True
                raise gcmd.error('AutoXY retry verified but heater restoration failed; original file must remain held: '+str(exc))
            self._thermal_snapshot=None
            s['phase']='AUTO_VERIFIED'
            gcmd.respond_info('AutoXY | COMPLETE: Guarded retry verified; continuing original print without repeat heatsoak')
            self.cmd_AUTOXY_PRINT_CONTINUE(gcmd)
        finally:
            self._protected_inside_gate=False
            self._retry_precommitted=False
            self._retry_context=None
            # The inner calibration gate intentionally retains the preflight
            # heater snapshot while the retry token is committed.  The
            # controller must release that ownership EXACTLY once now.
            if not heaters_restored:
                try:
                    self.restore_preflight_heaters()
                except Exception:
                    s['invalid']=True
                    logging.exception('AutoXY could not restore original nozzle targets after held retry')
            self._thermal_snapshot=None

    def cmd_AUTOXY_MANUAL_START(self,gcmd):
        s=self._protected_state(paused=True)
        if s['phase'] not in ('MANUAL_REQUIRED','RETRY_REQUIRED'):
            raise gcmd.error('AutoXY cannot start native VAOC from '+s['phase'])
        if self.busy:raise gcmd.error('AutoXY cannot start native VAOC while gate busy')
        # After a no-write failure, end the abandoned automatic VAOC context
        # before initiating a NEW distinguishable native manual session.
        if self.sts('gcode_macro _VAOC').get('is_started'):
            self.run_gcode('_VAOC_END')
        if self.sts('gcode_macro _VAOC').get('is_started'):
            s['invalid']=True
            raise gcmd.error('AutoXY failed to exit previous VAOC context')
        if tuple(self.offsets())!=s['initial_offsets']:
            s['invalid']=True
            raise gcmd.error('AutoXY saved offsets changed after failed auto; abort')
        # Dismiss the AutoXY prompt before native RatOS VAOC needs its own UI.
        # An error below still leaves the virtual-SD file held behind AUTOXY_PRINT_CHECK.
        self.gcode.respond_raw('// action:prompt_end')
        self.run_gcode('_VAOC_START')
        if not self.sts('gcode_macro _VAOC').get('is_started'):
            s['invalid']=True
            raise gcmd.error('AutoXY native VAOC did not start')
        self._retry_context=None
        self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
        s['manual_seen']=True;s['phase']='MANUAL_ACTIVE'
        s['return_prompt_shown']=False
        gcmd.respond_info('AutoXY | fresh RatOS manual VAOC started. Complete native alignment and exit VAOC before acceptance.')

    def cmd_AUTOXY_MANUAL_ACCEPT(self,gcmd):
        s=self._protected_state('MANUAL_ACTIVE',paused=True)
        if not s['manual_seen'] or self.sts('gcode_macro _VAOC').get('is_started'):
            raise gcmd.error('AutoXY manual VAOC must be exited before acceptance')
        offs=self.offsets()
        if any(not math.isfinite(x) or abs(x)>1.0 for x in offs):
            s['invalid']=True
            raise gcmd.error('AutoXY saved offset invalid or implausible')
        # Operator acceptance is the authoritative XY check. No camera pass.
        # Fault markers here are from the abandoned, NO-WRITE automatic cycle;
        # only clear them after successful fresh native VAOC and explicit consent.
        self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_in_progress VALUE=0')
        self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=0')
        self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_retry_available VALUE=0')
        s['phase']='MANUAL_AUTHORIZED'
        gcmd.respond_info('AutoXY | manual VAOC accepted. No AutoXY remeasurement will run.')

    def cmd_AUTOXY_MANUAL_ACCEPT_CONTINUE(self,gcmd):
        # Explicit operator decision: no timer/event may call this method.
        # Keep the old two-command workflow available for fallback.
        self.cmd_AUTOXY_MANUAL_ACCEPT(gcmd)
        self.cmd_AUTOXY_PRINT_CONTINUE(gcmd)
        self.gcode.respond_raw('// action:prompt_end')

    def cmd_AUTOXY_PRINT_CONTINUE(self,gcmd):
        s=self._protected_state(paused=True)
        if s['phase'] not in ('MANUAL_AUTHORIZED','AUTO_VERIFIED'):
            raise gcmd.error('AutoXY original file cannot continue from '+s['phase'])
        if self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
            raise gcmd.error('AutoXY refuses continuation while AutoXY fault/in-progress is latched')
        if self.sts('gcode_macro _VAOC').get('is_started'):
            raise gcmd.error('AutoXY refuses continuation with active VAOC')
        self.gcode.run_script_from_command('M24')
        gcmd.respond_info('AutoXY | releasing guarded original file')

    def cmd_AUTOXY_PRINT_CHECK(self,gcmd):
        s=self._protected_state(paused=False)
        if s['phase']=='AUTO_VERIFIED' and not self.enabled():
            s['invalid']=True
            raise gcmd.error('AutoXY AutoXY switched OFF after verification; reject continuation')
        if s['phase'] not in ('AUTO_VERIFIED','MANUAL_AUTHORIZED'):
            s['invalid']=True
            raise gcmd.error('AutoXY unauthorized continuation blocked. CANCEL this print.')
        if self.sts('gcode_macro _VAOC').get('is_started'):
            s['invalid']=True
            raise gcmd.error('AutoXY VAOC still active; print cannot continue')
        if self.flag('auto_xy_v2_fault') or self.flag('auto_xy_v2_in_progress'):
            s['invalid']=True
            raise gcmd.error('AutoXY unresolved AutoXY fault; print cannot continue')
        s['phase']='GUARD_CONSUMED'
        gcmd.respond_info('AutoXY | one-use guarded continuation accepted; skipping repeat calibration')

    def cmd_AUTOXY_PRINT_FINISH_GATE(self,gcmd):
        self._protected_state('GUARD_CONSUMED',paused=False)
        self._protected_session['phase']='FINISHING'
        gcmd.respond_info('AutoXY | proceeding through RatOS native post-bed Beacon and hotend startup')

    def _release_display_phase(self):
        s=self._protected_session
        if not s:return 'IDLE'
        phase=s.get('phase','IDLE')
        try:
            if phase=='FINISHING' and self.sts('print_stats').get('state')=='complete':
                return 'COMPLETED'
        except Exception:
            pass
        return phase

    def cmd_AUTOXY_PRINT_STATUS(self,gcmd):
        s=self._protected_session
        gcmd.respond_info('AutoXY %s; protected_print=%s; phase=%s; file=%s; invalid=%s' % (
            RELEASE_VERSION, self.protected_print_enabled, self._release_display_phase(),
            s['filename'] if s else 'none',s.get('invalid',False) if s else False))

    def cmd_AUTOXY_RELEASE_STATUS(self,gcmd):
        s=self._protected_session
        gcmd.respond_info('AutoXY %s; protected_print=%s; phase=%s; valid=%s; standalone_run=available' % (
            RELEASE_VERSION, bool(self.protected_print_enabled),
            self._release_display_phase(),
            not s.get('invalid',False) if s else True))

def load_config(config):
    return AutoXYGate(config)
