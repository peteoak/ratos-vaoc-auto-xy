# RatOS 2.1 optional XY calibration gate for Klipper (Stage 6.3.7; two-tool-only mode-aware inline print-start rescue; pre-Beacon hook).
# GPLv3-compatible extension: no third-party dependencies in Klipper Python.
# Camera/OpenCV execution occurs in a separate venv subprocess while the
# Klipper reactor is yielded, so MCU communications remain serviced.
import json, math, os, subprocess, time, logging, sys

class GateAbort(Exception):
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


class AutoXYGate:
    cmd_AUTO_XY_GATE_help='Run gated IDEX camera alignment only in independent mode'
    cmd_AUTO_XY_CAPTURE_MODE_help='Capture RatOS print mode and toolshift count before carriage reset'
    cmd_AUTO_XY_HEALTH_help='Check AutoXY Klipper integration and saved enable flag'
    cmd_AUTO_XY_RETRY_ONCE_help='Retry ONE recoverable calibration failure in the same active VAOC session after print abort'
    def __init__(self,config):
        self.printer=config.get_printer()
        self.reactor=self.printer.get_reactor()
        self.gcode=self.printer.lookup_object('gcode')
        self.python=config.get('python','/home/pi/auto-xy-venv/bin/python')
        self.worker=config.get('worker','/home/pi/auto_xy/auto_xy_vision_worker.py')
        self.results=os.path.expanduser(config.get('results','/home/pi/auto_xy/print_gate_runs'))
        self.camera=config.get('camera_url','http://127.0.0.1:8080/?action=snapshot')
        self.roi=config.get('roi','680,275,590,555')
        self.tol=config.getfloat('tolerance',.030,above=.009,below=.061)
        self.max_correct=config.getint('max_corrections',10,minval=1,maxval=10)
        self.max_verify=config.getint('max_verifications',6,minval=2,maxval=6)
        # No Mainsail G-code prompt can be serviced while START_PRINT's
        # synchronous gate owns the G-code command queue. Permit precisely
        # one recoverable in-place replay instead; never loop indefinitely.
        self.inline_print_retry=config.getboolean('inline_print_retry',True)
        self.max_step=config.getfloat('max_step',.05,above=.005,maxval=.05)
        self.max_total=config.getfloat('max_total_change',.3,above=.05,maxval=.3)
        self.settle=config.getfloat('settle',.8,above=.2,maxval=3)
        # Verify repeatable tool seating BEFORE any saved-offset write.
        # Each measurement performs T0 -> T1 -> T0; a stable consecutive pair
        # is required, with a bounded budget to fail safely if not settled.
        self.preflight_settling_checks=config.getint('preflight_settling_checks',4,minval=2,maxval=6)
        self.preflight_settling_tolerance=config.getfloat('preflight_settling_tolerance',.015,above=.004,maxval=.03)
        self.timeout=config.getfloat('snapshot_timeout',7.,above=2.,maxval=12.)
        self.max_camera_nozzle_temp=config.getfloat('max_camera_nozzle_temp',155.,above=100.,maxval=175.)
        self.max_camera_target_temp=config.getfloat('max_camera_target_temp',150.,above=100.,maxval=175.)
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
        self.phase='IDLE'
        self.last_error=''
        self.gcode.register_command('AUTO_XY_CAPTURE_MODE',self.cmd_AUTO_XY_CAPTURE_MODE,desc=self.cmd_AUTO_XY_CAPTURE_MODE_help)
        self.gcode.register_command('AUTO_XY_GATE',self.cmd_AUTO_XY_GATE,desc=self.cmd_AUTO_XY_GATE_help)
        self.gcode.register_command('AUTO_XY_HEALTH',self.cmd_AUTO_XY_HEALTH,desc=self.cmd_AUTO_XY_HEALTH_help)
        self.gcode.register_command('AUTO_XY_RETRY_ONCE',self.cmd_AUTO_XY_RETRY_ONCE,desc=self.cmd_AUTO_XY_RETRY_ONCE_help)

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
        self.pending_print_mode=None
        self.pending_toolshifts=None
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
        self.pending_print_mode=requested
        self.pending_toolshifts=toolshifts
        gcmd.respond_info('AUTO XY: START_PRINT captured %s; TOTAL_TOOLSHIFTS=%d%s' % (
            requested,toolshifts,' (single-tool bypass)' if requested=='SINGLE_TOOL' else ''))

    def sts(self,name):
        obj=self.printer.lookup_object(name,None)
        if obj is None: raise GateAbort('Klipper object missing: '+name)
        return obj.get_status(self.reactor.monotonic())
    def variables(self): return self.sts('save_variables')['variables']
    def offsets(self):
        v=self.variables()
        return [float(v[k]) for k in ('idex_xoffset','idex_yoffset','idex_zoffset')]
    def enabled(self):
        value=self.variables().get('auto_xy_enabled',0)
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
        if self.printer.get_state_message()[1]!='ready': raise GateAbort('Klipper no longer ready')
        if getattr(self,'deadline',float('inf'))<self.reactor.monotonic():
            raise GateAbort('15-minute calibration watchdog elapsed')
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
        return states
    def prepare_camera_at_preheat(self,gcmd):
        # Keep RatOS's existing preheat state; no heater commands are emitted.
        # This runs only with AUTO XY ON, prior to final print temperature.
        state=self.check_camera_temps()
        self.report['camera_temperature_entry_c']={k:v['temperature'] for k,v in state.items()}
        self.report['camera_temperature_start_c']={k:v['temperature'] for k,v in state.items()}
        self.report['camera_target_start_c']={k:v['target'] for k,v in state.items()}
        self.report['heating_targets_preserved']=True
        self.save_report()
        self.temp_guard_active=True
        gcmd.respond_info('AUTO XY: keeping current RatOS preheat (targets at or below %.1f C), monitoring actual nozzles <= %.1f C' %
                          (self.max_camera_target_temp,self.max_camera_nozzle_temp))

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
    def worker_capture(self,label):
        self.ensure_ok()
        if self.tool()!=self.active: raise GateAbort('Unexpected selected tool')
        self.pause(self.settle)
        self.count+=1
        image=os.path.join(self.directory,'%03d_%s.jpg'%(self.count,label))
        failures=[]
        for attempt in range(2):
            self.ensure_ok()
            cmd=[self.python,self.worker,'--url',self.camera,'--image',image,
                 '--roi',self.roi,'--timeout',str(self.timeout)]
            try:
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      close_fds=True)
            except OSError as e: raise GateAbort('Cannot start vision worker: '+str(e))
            deadline=self.reactor.monotonic()+self.timeout+10.
            try:
                while proc.poll() is None and self.reactor.monotonic()<deadline:
                    self.reactor.pause(self.reactor.monotonic()+.15)
                if proc.poll() is None:
                    proc.kill();proc.communicate()
                    failures.append('Worker timed out')
                else:
                    out,err=proc.communicate()
                    if proc.returncode:
                        failures.append(err.decode('utf8','replace')[:500])
                    else:
                        data=json.loads(out.decode('utf8'))
                        point=[float(x) for x in data['center_px']]
                        if len(point)!=2 or not all(math.isfinite(x) for x in point):
                            raise ValueError('Invalid image centre')
                        self.record('captures',label=label,tool=self.active,
                                    center_px=point,radius_px=data['radius_px'],
                                    image=image,sha256=data['sha256'],attempt=attempt+1)
                        return point
            except (ValueError,KeyError,UnicodeError) as exc:
                failures.append('Bad worker output: '+str(exc))
            finally:
                if proc.poll() is None:
                    proc.kill();proc.communicate()
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
    def approach(self):
        self.move('x',-.25);self.move('x',0.)
        self.move('y',-.25);self.move('y',0.)
    def select(self,tool):
        if maximum(self.delta)>1e-6:raise GateAbort('Cannot change tool away from VAOC reference')
        if tool!=self.active:
            self.run_gcode('_VAOC_LOAD_TOOL T=%d'%tool)
            self.active=tool;self.delta=[0.,0.]
            self.verify_pose()
    def reference(self,label):
        self.approach()
        samples=[self.worker_capture(label+'_a'),self.worker_capture(label+'_b')]
        mean=avg(samples)
        scatter=max(norm(vecsub(s,mean)) for s in samples)
        return mean,scatter
    def calibrate(self,tool):
        self.select(tool)
        self.approach()
        cols=[]; intercept=[]; residual=[]
        for axis in ('x','y'):
            points=[]
            for d in (-.2,0.,.2):
                self.move(axis,d)
                c=self.worker_capture('cal_t%d_%s_%+.1f'%(tool,axis,d))
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
        if not isinstance(exc,GateAbort):
            return False
        desc=str(exc)
        recoverable=(
            desc.startswith('Camera/nozzle detection failed at '),
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
        if self.phase not in ('CALIBRATE','MEASURING','VERIFYING'):
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
                not self._retry_precommitted and self.report.get('mode')=='PRINT'):
            return None
        ctx=self._retry_context
        parent_report=self.report
        parent_dir=self.directory
        try:
            self.validate_active_vaoc_context(ctx)
            # Make a reboot during the inline retry fail-closed. The run
            # already has auto_xy_in_progress=1; lock fault and consume token
            # before any additional movement. AUTO XY stays ON in normal
            # operation and after a successful retry.
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_fault VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_used VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
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
        if not self.flag('auto_xy_retry_available') or self.flag('auto_xy_retry_used'):
            raise gcmd.error('Retry refused: no unused one-time retry is available')
        ctx=self._retry_context
        if ctx is None:
            raise gcmd.error('Retry refused: active VAOC pose context was lost (e.g. Klipper restarted); manually recover and acknowledge fault')
        if not self.flag('auto_xy_fault') or not self.flag('auto_xy_in_progress'):
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
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_used VALUE=1')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
            # Keep fault + interrupted marker set during the entire retry.
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_enabled VALUE=1')
        except Exception as exc:
            raise gcmd.error('Retry could not commit one-use token: '+str(exc))
        gcmd.respond_info('AUTO XY: one active-VAOC MANUAL retry accepted. Original print remains cancelled; a NEW print is required after success.')
        self._retry_precommitted=True
        try:
            self.cmd_AUTO_XY_GATE(_RetryGcmd(gcmd))
        finally:
            self._retry_precommitted=False
            self._retry_context=None  # one-use, even on failure

    def health(self,gcmd):
        v=self.variables()
        gcmd.respond_info('AUTO XY Stage 6.3.7 / beta.4.2 alignment-first (AFTER_HEATING_BED / pre-Beacon); limits=%d corrections, %d verifications; inline_print_retry=%s; retry_available=%s retry_used=%s; active_vaoc_context=%s; switch=%s; fault=%s; interrupted=%s; busy=%s; last_error=%s'%(
            self.max_correct,self.max_verify,self.inline_print_retry,v.get('auto_xy_retry_available',0),v.get('auto_xy_retry_used',0),bool(self._retry_context),'ON' if self.enabled() else 'OFF',v.get('auto_xy_fault',0),
            v.get('auto_xy_in_progress',0),self.busy,self.last_error or 'none'))
    def cmd_AUTO_XY_HEALTH(self,gcmd):self.health(gcmd)
    def cmd_AUTO_XY_GATE(self,gcmd):
        mode=gcmd.get('MODE','PRINT').upper()
        if mode not in ('PRINT','IDLE'): raise gcmd.error('MODE must be PRINT or IDLE')
        if str(self.variables().get('auto_xy_in_progress',0)).lower() in ('1','1.0','true') and not self._retry_precommitted:
            raise gcmd.error('AUTO XY interrupted prior run detected: inspect saved/runtime offsets, run AUTO_XY_ACKNOWLEDGE')
        if str(self.variables().get('auto_xy_fault',0)).lower() in ('1','1.0','true') and not (self._retry_precommitted and self._retry_context is not None):
            raise gcmd.error('AUTO XY fault latched: inspect saved/runtime offsets and run AUTO_XY_ACKNOWLEDGE before any print')
        if not self.enabled():
            self.pending_print_mode=None  # never let an OFF print leave stale tokens
            self.pending_toolshifts=None
            gcmd.respond_info('AUTO XY OFF — bypassing VAOC entirely, saved offsets unchanged')
            return
        if self.busy: raise gcmd.error('AUTO XY already running')
        if mode=='PRINT':
            if self._retry_precommitted:
                # An inline retry reuses the original print's IDEX classification;
                # no new _USER_START_PRINT is executed between attempts.
                if self.active_print_mode!='INDEPENDENT':
                    raise gcmd.error('Auto XY inline retry lost its independent-mode context')
            else:
                requested=self.pending_print_mode
                captured_toolshifts=self.pending_toolshifts
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
        if mode=='IDLE' and self.sts('print_stats')['state'] in ('printing','paused'):
            raise gcmd.error('Manual Auto XY only allowed while idle')
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
        if mode=='IDLE':
            try:
                self.check_camera_temps()
            except GateAbort as e:
                raise gcmd.error('Manual VAOC heater safety check failed: '+str(e))
        retry_ctx=self._retry_context if self._retry_precommitted else None
        if not self._retry_precommitted:
            self._retry_context=None  # stale context after operator dismissed and manually recovered
        self.busy=True
        self.phase='STARTING'
        self.last_error=''
        self.directory=None
        self.delta=list(retry_ctx['delta']) if retry_ctx else [0.,0.]
        self.active=retry_ctx['active'] if retry_ctx else 0
        self.count=0
        self.needs_reconcile=bool(retry_ctx and maximum(vecsub(retry_ctx['expected_offsets'],retry_ctx['initial_offsets']))>1e-6)
        self.vaoc_entered=False
        self.temp_guard_active=False
        self.deadline=self.reactor.monotonic()+900.0
        self.report={'status':'STARTING','mode':mode,'hook_point':'after_heating_bed_pre_beacon' if mode=='PRINT' else 'manual',
                     'target_tolerance_mm':self.tol,'one_time_retry':self._retry_precommitted,
                     'max_corrections':self.max_correct,'max_verifications':self.max_verify,
                     'max_step_mm':self.max_step,'max_total_change_mm':self.max_total,
                     'started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'original_offsets':None,
                     'requested_idex_mode':self.active_print_mode if mode=='PRINT' else 'MANUAL',
                     'reuse_active_vaoc':bool(retry_ctx),
                     'prior_failed_run':retry_ctx['previous_report_dir'] if retry_ctx else None,
                     'inline_print_retry_enabled':bool(self.inline_print_retry)}
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
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_in_progress VALUE=1')
            if not retry_ctx:
                # A newly acknowledged fresh run gets a fresh one-time token.
                # Do NOT reset it when entering the one-time retry itself.
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_used VALUE=0')
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
            self.phase='CAMERA_TEMP_GUARD'
            self.prepare_camera_at_preheat(gcmd)
            self.phase='VAOC_ENTER'
            if retry_ctx:
                gcmd.respond_info('AUTO XY Stage 6.3.7: reusing STILL-ACTIVE VAOC for the one permitted retry')
                self.vaoc_entered=True
                self.base=list(retry_ctx['base'])
                self.verify_pose()  # position was already read-only verified
                self.move('x',0.);self.move('y',0.)
                self.select(0)
                self.verify_pose()
            else:
                gcmd.respond_info('AUTO XY Stage 6.3.7: entering VAOC after bed heatsoak, BEFORE Beacon probe and final hotend heat')
                self.run_gcode('_VAOC_START')
                self.vaoc_entered=True
                self.run_gcode('_VAOC_LOAD_TOOL T=0')
                self.active=0;self.delta=[0.,0.]
                self.base=self.cp()
                self.verify_pose()
            self.phase='CALIBRATE'
            gcmd.respond_info('AUTO XY: measuring live camera scale T0/T1')
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
            correction_count=0
            verification_count=0
            streak=0
            previous=None
            prior_step=None
            # Each pass either applies ONE bounded adjustment or consumes ONE
            # no-write verification. There can never be more than the sum.
            for cycle in range(1,self.max_correct+self.max_verify+1):
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
                    success=True
                    self.report['status']='CALIBRATED'
                    self.report['final_error_mm']=err
                    self.report['final_offsets']=self.offsets()
                    self.save_report()
                    break
                if action=='VERIFY_AGAIN':
                    gcmd.respond_info('AUTO XY verification %d/%d: in tolerance, %d/2 consecutive; rechecking' % (
                        verification_count,self.max_verify,streak))
                    continue
                step=[max(-self.max_step,min(self.max_step,-.8*x)) if abs(x)>self.tol else 0. for x in err]
                # First ever Y change on this installation was not independently
                # verified in Stage 4C; retain the Stage 5 conservative 0.020 mm cap.
                if correction_count==0:step[1]=max(-.020,min(.020,step[1]))
                self.phase='WRITING_OFFSETS'
                previous=err
                prior_step=step
                gcmd.respond_info('AUTO XY correction %d/%d: X=%+.4f Y=%+.4f mm' % (
                    correction_count+1,self.max_correct,step[0],step[1]))
                self.apply(step,original)
                correction_count+=1
                self.report['corrections_applied']=correction_count
                self.save_report()
                self.phase='VERIFYING'
            if not success: raise GateAbort('Calibration did not converge')
            self.phase='VAOC_EXIT'
            self.select(0)
            self.run_gcode('_VAOC_END')
            self.vaoc_entered=False
            if self.tool()!=initial_tool:
                raise GateAbort('RatOS VAOC exit did not restore original tool selection')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_in_progress VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_fault VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
            self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_used VALUE=0')
            self.report['status']='CALIBRATED_AND_VAOC_EXITED'
            self.save_report()
            if self.report.get('response_gain_recheck_status')=='CONFIRMED_IN_TOLERANCE':
                gcmd.respond_info('AUTO XY WARNING: unusual offset response but stable in-tolerance alignment confirmed twice; inspect report.json.')
            gcmd.respond_info('AUTO XY: VERIFIED X=%+.5f Y=%+.5f mm after %d adjustment(s), %d verification(s); proceeding.'%(
                err[0],err[1],correction_count,verification_count))
        except (Exception,KeyboardInterrupt) as exc:
            logging.exception('AUTO XY gated calibration failed')
            self.last_error=str(exc)
            self.report['status']='FAILED_DO_NOT_PRINT'
            self.report['error']=str(exc)
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
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_enabled VALUE=0')
                self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_fault VALUE=1')
                self.report['auto_xy_switched_off']=True
            except Exception:
                self.report['auto_xy_switched_off']=False
                retry_possible=False
            try:
                if retry_possible:
                    for axis,key in enumerate(('auto_xy_retry_x','auto_xy_retry_y','auto_xy_retry_z')):
                        self.run_gcode('SAVE_VARIABLE VARIABLE=%s VALUE=%.10f'%(key,self._retry_context['expected_offsets'][axis]))
                    self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=1')
                else:
                    self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
                    self._retry_context=None
            except Exception:
                retry_possible=False
                self._retry_context=None
                try:self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_retry_available VALUE=0')
                except Exception:pass
            self.report['retry_offered']=retry_possible
            self.report['retry_used']=self.flag('auto_xy_retry_used')
            if self.directory:self.save_report_best_effort()
            self.prompt_on_failure(retry_possible)
            # Do not call _VAOC_END after failure. Retry is a separate,
            # operator-triggered command issued AFTER the original print aborts.
            raise gcmd.error('AUTO XY FAILED; PRINT MUST NOT CONTINUE: '+str(exc))
        finally:
            self.busy=False
            self.temp_guard_active=False
            self.phase='IDLE'

def load_config(config):
    return AutoXYGate(config)
