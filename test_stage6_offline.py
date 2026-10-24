#!/usr/bin/env python3
"""Offline Klipper-API simulators, not physical hardware qualification."""
import importlib.util
from pathlib import Path
import tempfile, time
root=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('gate',root/'auto_xy_gate.py')
gate=importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)

class Reactor:
    def monotonic(self):return time.monotonic()
    def pause(self,t):pass
    def register_timer(self,fn,when):self.timer=(fn,when)
class G:
    def __init__(self):self.commands={};self.lines=[];self.responses=[]
    def register_command(self,name,fn,desc=''):self.commands[name]=fn
    def respond_raw(self,msg):self.responses.append(msg)
    def run_script_from_command(self,text):self.lines.extend(text.splitlines())
class Printer:
    def __init__(self):
        self.r=Reactor();self.g=G();self.var={'auto_xy_enabled':0,
            'idex_xoffset':-.164335,'idex_yoffset':-.238529,'idex_zoffset':.0225,
            'idex_xcontrolpoint':112.44888,'idex_ycontrolpoint':328.54641,
            'idex_zcontrolpoint':2.05}
    def get_reactor(self):return self.r
    def lookup_object(self,name,default=None):
        if name=='gcode':return self.g
        return None
    def get_state_message(self):return ('Printer is ready','ready')
class Config:
    def __init__(self):self.p=Printer()
    def get_printer(self):return self.p
    def get(self,key,default):return default
    def getfloat(self,key,default,**kwargs):return default
    def getint(self,key,default,**kwargs):return default
    def getboolean(self,key,default,**kwargs):return default
class Cmd:
    def __init__(self,mode='IDLE',toolshifts=0):self.mode=mode;self.toolshifts=toolshifts;self.messages=[]
    def get(self,k,default=None):
        if k=='MODE':return self.mode
        if k=='TOTAL_TOOLSHIFTS':return self.toolshifts
        return default
    def respond_info(self,msg):self.messages.append(msg)
    def error(self,msg):return ValueError(msg)

class SimGate(gate.AutoXYGate):
    def __init__(self,config):
        super().__init__(config)
        self.sim_pos=[112.44888,328.54641,2.05]
        self.sim_tool=0
        self.sim_error=[-.115, .012]
        self.sim_start=[-.164335,-.238529]
        self.vaoc=False
        self.capture_fail=False
        self.sim_temp={'extruder':24.,'extruder1':24.}
        self.sim_target={'extruder':0.,'extruder1':0.}
        self.overheat_during_capture=False
        self.sim_commands=[]
    def sts(self,name):
        if name=='gcode_macro RatOS':return {'default_toolhead':0}
        if name=='gcode_macro _VAOC':return {'is_started':self.vaoc}
        if name=='gcode_macro START_PRINT':return {'is_printing_gcode':False}
        if name=='print_stats':return {'state':'standby'}
        if name in ('extruder','extruder1'):return {'temperature':self.sim_temp[name], 'target':self.sim_target[name]}
        if name=='toolhead':return {'homed_axes':'xyz'}
        if name=='dual_carriage':return {'carriage_1':'primary' if self.sim_tool==1 else 'inactive'}
        if name=='save_variables':return {'variables':self.printer.var}
        if name=='gcode_move':return {'gcode_position':self.sim_pos}
        raise KeyError(name)
    def pause(self,t):pass
    def run_gcode(self,script):
        for cmd in script.splitlines():
            cmd=cmd.strip()
            self.sim_commands.append(cmd)
            if cmd=='_VAOC_START':self.vaoc=True
            elif cmd=='_VAOC_END':self.vaoc=False
            elif cmd.startswith('_VAOC_LOAD_TOOL T='):
                self.sim_tool=int(cmd.rsplit('=',1)[1]);self.sim_pos=self.cp()
            elif cmd.startswith('_VAOC_MOVE'):
                pts=dict(x.split('=') for x in cmd.split()[1:])
                self.sim_pos[0]+=float(pts['X']);self.sim_pos[1]+=float(pts['Y'])
            elif cmd=='_VAOC_SET_TOOL':
                self.printer.var['idex_xoffset']-=self.sim_pos[0]-self.cp()[0]
                self.printer.var['idex_yoffset']-=self.sim_pos[1]-self.cp()[1]
            elif cmd.startswith('SET_HEATER_TEMPERATURE HEATER='):
                tokens=dict(part.split('=',1) for part in cmd.split()[1:])
                if tokens['TARGET']=='0': self.sim_temp[tokens['HEATER']]=100.
            elif cmd.startswith('SAVE_VARIABLE VARIABLE='):
                sp=cmd.split()
                key=sp[1].split('=',1)[1];val=float(sp[2].split('=',1)[1]);self.printer.var[key]=val
            elif cmd!='M400':raise AssertionError('Unknown mock GCode: '+cmd)
    def worker_capture(self,label):
        if self.capture_fail:raise gate.GateAbort('simulated camera failure')
        if self.overheat_during_capture:
            self.sim_temp['extruder']=160.
            self.ensure_ok()
        self.count+=1
        self.ensure_ok()
        self.verify_pose()
        relative=[self.sim_pos[i]-self.cp()[i] for i in (0,1)]
        curr=self.offsets()
        drift=[self.sim_error[i]+self.sim_start[i]-curr[i] for i in (0,1)]
        actual=[relative[i]+(drift[i] if self.sim_tool==1 else 0.) for i in (0,1)]
        point=[960.-280*actual[0],540.-290*actual[1]]
        self.record('captures',label=label,center_px=point)
        return point


def test():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        off=Cmd()
        s.cmd_AUTO_XY_GATE(off)
        assert 'bypassing' in off.messages[0]
        assert c.p.var['idex_xoffset']==-.164335
        c.p.var['auto_xy_enabled']=1
        run=Cmd()
        s.cmd_AUTO_XY_GATE(run)
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED',s.report
        assert len(s.report['saved_corrections'])==2
        assert abs(s.report['final_error_mm'][0])<=.03
        assert abs(c.p.var['idex_xoffset']+ .264335)<.001
        assert s.vaoc==False
        # A camera failure latches fault and disables future auto prints.
        c.p.var['auto_xy_enabled']=1
        s.capture_fail=True
        try:s.cmd_AUTO_XY_GATE(Cmd())
        except ValueError as e:assert 'PRINT MUST NOT CONTINUE' in str(e)
        else:raise AssertionError('Should have failed closed')
        assert c.p.var['auto_xy_fault']==1
        assert c.p.var['auto_xy_enabled']==0
        try:s.cmd_AUTO_XY_GATE(Cmd())
        except ValueError as e:assert ('fault latched' in str(e) or 'interrupted prior run' in str(e))
        else:raise AssertionError('Fault latch bypassed')
        print('PASS: OFF bypass; convergence & saved offset; VAOC exit; camera fail & persisted latch.')

if __name__=='__main__':test()

# Installer wiring test: temp filesystem with a preinstalled Stage5 include.
def test_installer():
    import io,contextlib,sys
    spec_i=importlib.util.spec_from_file_location('installer',root/'install_stage6.py')
    mod=importlib.util.module_from_spec(spec_i);spec_i.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as tmp:
        temp=Path(tmp)
        mod.HOME=temp
        mod.CFG=temp/'printer_data/config'
        mod.DEST=temp/'auto_xy/stage6'
        mod.KLIPPER=temp/'klipper/klippy/extras/auto_xy_gate.py'
        mod.CFG.mkdir(parents=True);mod.KLIPPER.parent.mkdir(parents=True)
        (mod.CFG/'printer.cfg').write_text('[include RatOS.cfg]\n[include auto_xy_control.cfg]\n')
        (mod.CFG/'auto_xy_control.cfg').write_text('[gcode_macro AUTO_XY_ON]\ngcode:\n M117 old\n')
        (mod.CFG/'RatOS').mkdir()
        (mod.CFG/'RatOS/macros.cfg').write_text("""[gcode_macro START_PRINT]
gcode:
 _USER_START_PRINT { rawparams }
 {% set total_toolshifts = params.TOTAL_TOOLSHIFTS|default(0)|int %}
 {% if total_toolshifts == 0 %}
 {% endif %}
 _IDEX_SINGLE X={parking_position}
 MAYBE_HOME
 G4 P{(bed_heat_soak_time * 1000)}
 _USER_START_PRINT_AFTER_HEATING_BED { rawparams }
 _START_PRINT_AFTER_HEATING_BED T={initial_tool}
 _USER_START_PRINT_BED_MESH { rawparams }
""")
        mod.live_status=lambda:{'auto_xy_enabled':0,'idex_xoffset':-.164335}
        before=(mod.CFG/'printer.cfg').read_text()
        old=sys.argv
        try:
            sys.argv=['install_stage6.py','--check']
            with contextlib.redirect_stdout(io.StringIO()):mod.run()
            assert (mod.CFG/'printer.cfg').read_text()==before
            sys.argv=['install_stage6.py','--install']
            with contextlib.redirect_stdout(io.StringIO()):mod.run()
        finally:sys.argv=old
        result=(mod.CFG/'printer.cfg').read_text()
        assert result.count('[include auto_xy_print.cfg]')==1
        assert '[include auto_xy_control.cfg]' not in result
        assert mod.KLIPPER.exists()
        assert (mod.DEST/'auto_xy_vision_worker.py').exists()
        assert any((temp/'auto_xy/stage6_backups').glob('*/printer.cfg'))
        print('PASS: install --check is read-only; backup, include migration, Klipper extra copied.')

if __name__=='__main__':test_installer()


def test_early_hook():
    config=(root/'auto_xy_print.cfg').read_text()
    assert '[gcode_macro _USER_START_PRINT_AFTER_HEATING_BED]' in config
    assert '[gcode_macro _USER_START_PRINT_BED_MESH]' not in config
    assert '[gcode_macro _USER_START_PRINT_AFTER_HEATING_EXTRUDER]' not in config
    assert 'max_camera_nozzle_temp: 155.0' in config
    assert 'max_camera_target_temp: 150.0' in config
    assert 'BEFORE Beacon contact probing' in config
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        # OFF must not change either nozzle target even at print temperatures.
        s.sim_temp['extruder']=150.
        s.sim_target['extruder']=150.
        s.pending_print_mode='INDEPENDENT'
        s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        assert not any('SET_HEATER_TEMPERATURE' in q for q in s.sim_commands)
        # ON: normal 150 C preheat stays enabled while VAOC runs.
        c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT'
        s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        assert not any('SET_HEATER_TEMPERATURE' in q for q in s.sim_commands)
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED'
        assert s.report['camera_temperature_entry_c']['extruder']==150.0
        assert s.report['camera_target_start_c']['extruder']==150.0
        assert s.report['heating_targets_preserved'] is True
    # Reject a hot initial target even when current temperature is low.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        s.sim_temp['extruder']=145.
        s.sim_target['extruder']=220.
        c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT'
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as e:assert 'target exceeds camera preheat cap' in str(e)
        else: raise AssertionError('Hotend print-temperature target was not blocked')
        assert c.p.var['auto_xy_fault']==1
    # Reject temperature overshoot detected DURING the measurements.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        s.sim_temp['extruder']=150.
        s.sim_target['extruder']=150.
        s.overheat_during_capture=True
        c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT'
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as e:assert 'exceeds camera temperature cap' in str(e)
        else: raise AssertionError('Mid-cycle overheated nozzle was not blocked')
        assert c.p.var['auto_xy_fault']==1
    print('PASS: pre-Beacon hook, OFF no-op, 150 C preheat preserved, over-temp and high-target fail-closed.')

if __name__=='__main__':test_early_hook()


def test_mainsail_toggle():
    # Static validation of shipped G-code macros. Klipper/Jinja live behavior
    # must still be verified on printer; this is not an on-printer test.
    cfg = (root/'auto_xy_print.cfg').read_text()
    assert cfg.count('[gcode_macro AUTO_XY_TOGGLE]') == 1
    toggle = cfg.split('[gcode_macro AUTO_XY_TOGGLE]',1)[1].split('[gcode_macro ',1)[0]
    assert 'SAVE_VARIABLE VARIABLE=auto_xy_enabled VALUE=0' in toggle
    assert 'SAVE_VARIABLE VARIABLE=auto_xy_enabled VALUE=1' in toggle
    assert 'auto_xy_fault' in toggle and 'auto_xy_in_progress' in toggle
    assert 'idex_xoffset' not in toggle and 'idex_yoffset' not in toggle
    assert 'AUTO_XY_ON' in cfg and 'AUTO_XY_OFF' in cfg and 'AUTO_XY_STATUS' in cfg
    print('PASS: one-click Mainsail toggle present, persisted ON/OFF, fault guard and no offset writes.')

if __name__=='__main__':test_mainsail_toggle()


def test_independent_budgets():
    """Exercise 10 adjustment decisions independently of 6 no-write verifies."""
    c=Config();s=SimGate(c)
    assert s.max_correct==10 and s.max_verify==6
    corrections=0; verifies=0; streak=0
    for i in range(10):
        action,verifies,streak=s.classify_measurement([.041,.0],corrections,verifies,streak)
        assert action=='CORRECT' and verifies==0 and streak==0
        corrections+=1
    action,verifies,streak=s.classify_measurement([.041,.0],corrections,verifies,streak)
    assert action=='CORRECTION_EXHAUSTED', 'The 11th correction was not refused'

    # Every within-tolerance check consumes ONE verification. A later OOT
    # measurement starts a correction and resets the two-check streak.
    corrections=0; verifies=0; streak=0
    for i in range(5):
        action,verifies,streak=s.classify_measurement([.012,.003],corrections,verifies,streak)
        assert action=='VERIFY_AGAIN' and verifies==i+1 and streak==1
        action,verifies,streak=s.classify_measurement([.034,.003],corrections,verifies,streak)
        assert action=='CORRECT' and verifies==i+1 and streak==0
        corrections+=1
    action,verifies,streak=s.classify_measurement([.012,.003],corrections,verifies,streak)
    assert action=='VERIFY_EXHAUSTED' and verifies==6, 'The sixth inconclusive verification was not refused'

    action,verifies,streak=s.classify_measurement([.012,.009],0,0,0)
    assert (action,verifies,streak)==('VERIFY_AGAIN',1,1)
    action,verifies,streak=s.classify_measurement([.020,.008],0,verifies,streak)
    assert (action,verifies,streak)==('COMPLETE',2,2)

    cfg=(root/'auto_xy_print.cfg').read_text()
    assert 'max_corrections: 10' in cfg
    assert 'max_verifications: 6' in cfg
    assert 'max_step: 0.050' in cfg and 'max_total_change: 0.300' in cfg
    source=(root/'auto_xy_gate.py').read_text()
    assert 'range(1,self.max_correct+self.max_verify+1)' in source
    assert 'Six-correction maximum reached' not in source
    print('PASS: 10 independent adjustment slots, 6 independent no-write verifications, two-check convergence, and existing motion caps.')

if __name__=='__main__':test_independent_budgets()

class ScriptedAlignmentGate(SimGate):
    """Run the real correction gate against deterministic camera-error sequences."""
    def __init__(self,config,errors):
        super().__init__(config)
        self.test_errors=list(errors)

    def measure(self,m,label):
        if not self.test_errors:raise AssertionError('Unexpected extra measurement')
        err=list(self.test_errors.pop(0))
        self.record('measurements',label=label,error_mm=err,
                    t0_drift_mm=[0,0],scatter_estimate_mm=0.,saved_offsets=self.offsets())
        return err


def test_real_gate_10_corrections():
    def wave(sign):
        error=.065*sign
        errors=[]
        corrections=0
        while abs(error)>.03:
            errors.append([error,0.])
            step=max(-.05,min(.05,-.8*error))
            error+=.22*step  # within Stage 4/5 response safety [0.2, 2.0]
            corrections+=1
        errors.append([error,0.]) # verification check
        return errors,corrections
    first,n1=wave(1)
    second,n2=wave(-1)
    assert n1==n2==5,(n1,n2)
    sequence=first+second+[[.065,0.]]  # out of tolerance after all 10 writes
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=ScriptedAlignmentGate(c,sequence);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        original=s.offsets()
        try:s.cmd_AUTO_XY_GATE(Cmd())
        except ValueError as e:assert 'Correction limit reached (10)' in str(e),str(e)
        else:raise AssertionError('11th correction was not refused')
        assert s.report['corrections_applied']==10,s.report
        assert s.report['verification_attempts']==2,s.report
        assert s.report['measurement_cycles']==13,s.report
        assert len(s.report['saved_corrections'])==10
        assert s.report['status']=='FAILED_DO_NOT_PRINT'
        assert 'DEFERRED' in s.report['rollback']
        assert s.report['retry_offered'] is True
        assert s.vaoc is True
        assert c.p.var['auto_xy_retry_available']==1
        assert max(abs(s.offsets()[i]-original[i]) for i in (0,1))<=s.max_total
        assert c.p.var['auto_xy_enabled']==0 and c.p.var['auto_xy_fault']==1
        print('PASS: real gate executes 10 bounded adjustments, refuses #11, logs counts, restores saved offsets, latches fault.')


def test_real_gate_verification_limit():
    sequence=[[.02,0.]]
    for _ in range(5):
        sequence.extend([[.04,0.],[.012,0.]])
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=ScriptedAlignmentGate(c,sequence);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        try:s.cmd_AUTO_XY_GATE(Cmd())
        except ValueError as e:assert 'Verification limit reached (6)' in str(e),str(e)
        else:raise AssertionError('Seventh verification was not refused')
        assert s.report['corrections_applied']==5,s.report
        assert s.report['verification_attempts']==6,s.report
        assert s.report['measurement_cycles']==11,s.report
        assert s.report['status']=='FAILED_DO_NOT_PRINT'
        assert c.p.var['auto_xy_enabled']==0 and c.p.var['auto_xy_fault']==1
        print('PASS: real gate aborts at 6 inconclusive verifications, counts persisted and safety fault latched.')

if __name__=='__main__':
    test_real_gate_10_corrections()
    test_real_gate_verification_limit()


def fail_initial(s):
    s.printer.var['auto_xy_enabled']=1
    s.capture_fail=True
    s.pending_print_mode='INDEPENDENT'
    try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
    except ValueError as e:assert 'PRINT MUST NOT CONTINUE' in str(e)
    else:raise AssertionError('Should abort original print')
    # Generic synthetic failure is not eligible. Camera worker failures use
    # the full descriptive GateAbort raised in the production worker method.

class RecoverableCamGate(SimGate):
    def worker_capture(self,label):
        if self.capture_fail:
            raise gate.GateAbort('Camera/nozzle detection failed at '+label+': test dark camera')
        return super().worker_capture(label)


def test_in_place_retry_success_no_second_vaoc_start():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=RecoverableCamGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        s.capture_fail=True
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError as e:assert 'PRINT MUST NOT CONTINUE' in str(e)
        else:raise AssertionError('First camera failure should abort print')
        assert s.vaoc and c.p.var.get('auto_xy_retry_available')==1
        assert c.p.var['auto_xy_enabled']==0 and c.p.var['auto_xy_fault']==1
        assert c.p.var['auto_xy_in_progress']==1
        assert s.report['retry_offered'] is True
        assert any('action:prompt_button Retry calibration once (keep VAOC)' in q for q in c.p.g.responses)
        old_start_count=s.sim_commands.count('_VAOC_START')
        old_end_count=s.sim_commands.count('_VAOC_END')
        assert old_start_count==1 and old_end_count==0
        s.capture_fail=False
        answer=Cmd(mode='IDLE')
        s.cmd_AUTO_XY_RETRY_ONCE(answer)
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED'
        assert s.report['reuse_active_vaoc'] is True
        assert c.p.var['auto_xy_fault']==0 and c.p.var['auto_xy_in_progress']==0
        assert c.p.var['auto_xy_retry_available']==0 and c.p.var['auto_xy_retry_used']==0
        assert s.sim_commands.count('_VAOC_START')==1,'Must not enter VAOC a second time'
        assert s.sim_commands.count('_VAOC_END')==1,'Ends VAOC only after verified retry'
        assert s.vaoc==False
        assert any('Original print remains cancelled' in m for m in answer.messages)
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'no unused' in str(e)
        else:raise AssertionError('Must not permit extra retry')
        print('PASS: active VAOC preserved after failure; one in-place retry verifies, exits once and never resumes print.')


def test_in_place_retry_failure_exhausts_token():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=RecoverableCamGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1;s.capture_fail=True
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError:pass
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'PRINT MUST NOT CONTINUE' in str(e)
        else:raise AssertionError('Retry camera failure should abort')
        assert s.vaoc is True
        assert c.p.var['auto_xy_retry_used']==1 and c.p.var['auto_xy_retry_available']==0
        assert c.p.var['auto_xy_fault']==1 and c.p.var['auto_xy_enabled']==0
        assert s.report['retry_offered'] is False
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'no unused' in str(e)
        else:raise AssertionError('Third attempt must not run')
        print('PASS: failed in-place retry remains faulted; token exhausted, no third attempt.')


def test_in_place_retry_refuses_changed_pose_and_offsets_and_heat():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=RecoverableCamGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1;s.capture_fail=True
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError:pass
        s.capture_fail=False
        # Any unsafe condition must be refused BEFORE token consumption.
        s.sim_pos[0]+=.03
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'nozzle position changed' in str(e)
        else:raise AssertionError('Altered nozzle pose should prevent retry')
        s.sim_pos[0]-=.03
        s.printer.var['idex_xoffset']+=.01
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'saved offsets changed' in str(e)
        else:raise AssertionError('Offset drift should prevent retry')
        s.printer.var['idex_xoffset']-=.01
        s.sim_temp['extruder']=160.
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'temperature cap' in str(e)
        else:raise AssertionError('Overhot nozzle should prevent retry')
        s.sim_temp['extruder']=150.
        s.vaoc=False
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'VAOC ended' in str(e)
        else:raise AssertionError('Inactive VAOC should prevent reuse')
        assert c.p.var['auto_xy_retry_available']==1 and not c.p.var.get('auto_xy_retry_used',0)
        print('PASS: motion, offsets, hot nozzle and closed VAOC refuse retry without consuming token.')


def test_in_place_retry_after_saved_adjustments():
    # Verification limit failure after real adjustments retains those offsets
    # and a second calibration continues under the ORIGINAL 0.300mm cap.
    sequence=[[.02,0.]]
    for _ in range(5):sequence.extend([[.04,0.],[.012,0.]])
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=ScriptedAlignmentGate(c,sequence);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        first=s.offsets()
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError as e:assert 'Verification limit reached' in str(e)
        else:raise AssertionError('Verification exhaustion should fail')
        assert s.vaoc and s.report['retry_offered']
        assert 'DEFERRED' in s.report['rollback']
        after_first=s.offsets()
        assert after_first!=first
        s.test_errors=[[.01,.0],[.012,.0]]
        s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED'
        assert s.report['original_offsets']==first
        assert s.report['retry_start_offsets']==after_first
        assert s.offsets()==after_first
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==1
        print('PASS: verification exhaustion with saved changes retries in VAOC without rollback, retains original total cap.')


def test_exception_not_retryable():
    # Unknown worker errors and thermal problems must never invite retry.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1;s.capture_fail=True
        try:s.cmd_AUTO_XY_GATE(Cmd())
        except ValueError:pass
        assert not s.report['retry_offered']
        assert c.p.var.get('auto_xy_retry_available',0)==0
        assert s.vaoc is True
        print('PASS: unexpected failure does not expose in-VAOC retry.')

if __name__=='__main__':
    test_in_place_retry_success_no_second_vaoc_start()
    test_in_place_retry_failure_exhausts_token()
    test_in_place_retry_refuses_changed_pose_and_offsets_and_heat()
    test_in_place_retry_after_saved_adjustments()
    test_exception_not_retryable()


def test_active_retry_from_nonzero_sweep_position():
    class OneBadSweep(RecoverableCamGate):
        def __init__(self,c):
            super().__init__(c)
            self.fail_sweep=True
        def worker_capture(self,label):
            if self.fail_sweep and label=='cal_t1_x_+0.2':
                raise gate.GateAbort('Camera/nozzle detection failed at '+label+': transient camera error')
            return super().worker_capture(label)
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=OneBadSweep(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError:pass
        assert s.report['retry_offered'] and s.vaoc
        assert s.active==1 and abs(s.delta[0]-.2)<.00001
        s.fail_sweep=False
        s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED'
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==1
        print('PASS: fail during nonzero camera sweep, verify exact pose, safely recenter and retry without re-entering VAOC.')


def test_original_cumulative_cap_survives_retry():
    class FaultAtEnd(ScriptedAlignmentGate):
        def measure(self,m,label):
            if self.test_errors[0]=='FAIL':
                self.test_errors.pop(0)
                raise gate.GateAbort('Camera return/scatter exceeds tolerance')
            return super().measure(m,label)
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=FaultAtEnd(c,[[e,0.] for e in (.29,.24,.19,.14,.09,.04,.029)]+['FAIL'])
        s.results=tmp;c.p.var['auto_xy_enabled']=1
        first=s.offsets()
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='IDLE'))
        except ValueError:pass
        assert s.report['retry_offered'],s.report
        assert abs(s.offsets()[0]-first[0])>.27
        assert abs(s.offsets()[0]-first[0])<.3
        s.test_errors=[[.04,0.],[.002,0.]]
        try:s.cmd_AUTO_XY_RETRY_ONCE(Cmd())
        except ValueError as e:assert 'Total offset correction exceeds safety cap' in str(e)
        else:raise AssertionError('Retry incorrectly restarted the cumulative 0.30mm budget')
        assert c.p.var['auto_xy_retry_used']==1 and c.p.var['auto_xy_retry_available']==0
        assert s.report.get('retry_offered') is False
        assert 'ORIGINAL_SAVED_VALUES_RESTORED' in s.report.get('rollback','')
        assert abs(s.offsets()[0]-first[0])<.0001
        print('PASS: retry cannot reset cumulative .300mm budget; abort rolls back to first attempt original offset.')

if __name__=='__main__':
    test_active_retry_from_nonzero_sweep_position()
    test_original_cumulative_cap_survives_retry()

# Stage 6.3.5: keep the *same print-start command* alive on a recoverable
# failure, perform one fresh attempt, then either return normally or fault.
def test_inline_print_camera_retry_continues_same_print():
    class OneBadFrame(RecoverableCamGate):
        def __init__(self,c):
            super().__init__(c)
            self.fail_once=True
        def worker_capture(self,label):
            if self.fail_once and label=='cal_t1_x_+0.2':
                self.fail_once=False
                raise gate.GateAbort('Camera/nozzle detection failed at '+label+': transient frame')
            return super().worker_capture(label)
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=OneBadFrame(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        cmd=Cmd(mode='PRINT')
        s.pending_print_mode='INDEPENDENT'
        s.cmd_AUTO_XY_GATE(cmd)  # returns normally to the SAME START_PRINT
        assert s.report['mode']=='PRINT'
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED',s.report
        assert s.report['one_time_retry'] is True
        assert s.report['reuse_active_vaoc'] is True
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==1
        assert s.vaoc is False
        assert c.p.var['auto_xy_enabled']==1
        assert c.p.var['auto_xy_fault']==0 and c.p.var['auto_xy_in_progress']==0
        assert not any('action:prompt_begin' in m for m in c.p.g.responses)
        assert any('Resuming the original RatOS print start' in m for m in cmd.messages)
        import json
        parent_path=Path(s.report['prior_failed_run'])/'report.json'
        old=json.loads(parent_path.read_text())
        assert old['status']=='RECOVERED_INLINE',old
        assert old['inline_retry_report_dir']==s.directory
        print('PASS: one transient PRINT camera fault retries inline at offset sweep +0.2, original print continues, ON preserved, one VAOC start/end.')


def test_inline_print_verification_retry_preserves_saves():
    sequence=[[.02,0.]]
    for _ in range(5): sequence.extend([[.04,0.],[.012,0.]])
    sequence.extend([[.010,0.],[.012,0.]])
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=ScriptedAlignmentGate(c,sequence);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        initial=s.offsets()
        s.pending_print_mode='INDEPENDENT'
        s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED',s.report
        assert s.report['one_time_retry'] and s.report['reuse_active_vaoc']
        assert s.report['original_offsets']==initial
        assert s.report['retry_start_offsets']!=initial
        assert s.offsets()==s.report['retry_start_offsets']
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==1
        assert c.p.var['auto_xy_enabled']==1
        assert c.p.var['auto_xy_fault']==0
        print('PASS: verification exhaustion retries inline in same print without re-entering VAOC or rolling back verified writes.')


def test_inline_print_second_failure_stops():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=RecoverableCamGate(c);s.results=tmp
        s.capture_fail=True;c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT'
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as e:assert 'PRINT MUST NOT CONTINUE' in str(e)
        else:raise AssertionError('Two failed camera passes must stop original print')
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==0
        assert c.p.var['auto_xy_enabled']==0
        assert c.p.var['auto_xy_fault']==1
        assert c.p.var['auto_xy_retry_used']==1
        assert c.p.var['auto_xy_retry_available']==0
        assert s.report['one_time_retry'] is True
        assert s.report['retry_offered'] is False
        print('PASS: second inline failure ends original print, disables AutoXY, latches fault, no third retry.')


def test_inline_print_hard_fault_is_not_retried():
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        s.sim_temp['extruder']=170.;s.sim_target['extruder']=170.
        c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT'
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as e:assert 'temperature cap' in str(e)
        else:raise AssertionError('Overheat must always abort')
        assert s.sim_commands.count('_VAOC_START')==0
        assert c.p.var['auto_xy_enabled']==0 and c.p.var['auto_xy_fault']==1
        assert c.p.var.get('auto_xy_retry_used',0)==0
        print('PASS: 170C Beacon thermal condition aborts immediately without consuming retry or starting VAOC.')

if __name__=='__main__':
    test_inline_print_camera_retry_continues_same_print()
    test_inline_print_verification_retry_preserves_saves()
    test_inline_print_second_failure_stops()
    test_inline_print_hard_fault_is_not_retried()

def test_inline_print_cumulative_limit_not_reset():
    class FaultAtEnd(ScriptedAlignmentGate):
        def measure(self,m,label):
            if self.test_errors[0]=='FAIL':
                self.test_errors.pop(0)
                raise gate.GateAbort('Camera return/scatter exceeds tolerance')
            return super().measure(m,label)
    with tempfile.TemporaryDirectory() as tmp:
        sequence=[[e,0.] for e in (.29,.24,.19,.14,.09,.04,.029)] + ['FAIL']
        sequence.extend([[.04,0.],[.002,0.]])
        c=Config();s=FaultAtEnd(c,sequence);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        old=s.offsets()
        s.pending_print_mode='INDEPENDENT'
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as e:assert 'Total offset correction exceeds safety cap' in str(e)
        else:raise AssertionError('Inline rescue must NOT reset original 0.300mm total budget')
        assert c.p.var['auto_xy_enabled']==0 and c.p.var['auto_xy_fault']==1
        assert c.p.var['auto_xy_retry_available']==0 and c.p.var['auto_xy_retry_used']==1
        assert s.sim_commands.count('_VAOC_START')==1
        assert s.sim_commands.count('_VAOC_END')==0
        assert s.report['status']=='FAILED_DO_NOT_PRINT'
        assert s.report['one_time_retry'] is True
        assert max(abs(s.offsets()[i]-old[i]) for i in (0,1))<.0001
        import json
        p=Path(s.report['prior_failed_run'])/'report.json'
        assert json.loads(p.read_text())['status']=='INLINE_RETRY_FAILED_PRINT_ABORTED'
        print('PASS: inline retry cannot reset total 0.300mm saved-offset budget; second hard fault aborts print and restores original saved values.')

if __name__=='__main__':test_inline_print_cumulative_limit_not_reset()


def test_idex_mode_routing():
    """RatOS distinguishes single vs dual independent jobs using toolshifts."""
    for carriage,toolshifts,expected in [
        ('copy',0,'COPY'),('mirror',0,'MIRROR'),
        ('copy',3,'COPY'),('mirror',3,'MIRROR'),
        ('inactive',0,'SINGLE_TOOL'),('primary',0,'SINGLE_TOOL'),
        ('parked',0,'SINGLE_TOOL'),
        ('inactive',2,'INDEPENDENT'),('primary',1,'INDEPENDENT'),
        ('parked',4,'INDEPENDENT')]:
        with tempfile.TemporaryDirectory() as tmp:
            c=Config();s=SimGate(c);s.results=tmp
            c.p.var['auto_xy_enabled']=1
            orig_sts=s.sts
            s.sts=lambda name,orig_sts=orig_sts,carriage=carriage: (
                {'carriage_1':carriage} if name=='dual_carriage' else orig_sts(name))
            early=Cmd(mode='PRINT',toolshifts=toolshifts)
            s.cmd_AUTO_XY_CAPTURE_MODE(early)
            assert s.pending_print_mode==expected,(carriage,toolshifts,s.pending_print_mode)
            assert s.pending_toolshifts==toolshifts
            s.sts=orig_sts
            before=s.offsets()
            result=Cmd(mode='PRINT')
            s.cmd_AUTO_XY_GATE(result)
            assert s.pending_print_mode is None
            assert s.pending_toolshifts is None
            if expected in ('COPY','MIRROR','SINGLE_TOOL'):
                assert 'skipping VAOC' in ' '.join(result.messages),result.messages
                assert s.sim_commands==[],s.sim_commands
                assert s.offsets()==before
                assert c.p.var['auto_xy_enabled']==1
                assert not s.vaoc
                assert not getattr(s,'directory',None)
            else:
                assert s.report['status']=='CALIBRATED_AND_VAOC_EXITED'
                assert s.report['requested_idex_mode']=='INDEPENDENT'
                assert '_VAOC_START' in s.sim_commands
    # Missing early capture must fail before motion, rather than infer from
    # the carriage state after RatOS resets to independent.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        try:s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        except ValueError as exc:assert 'missing early RatOS mode capture' in str(exc)
        else:raise AssertionError('Missing early capture allowed camera motion')
        assert s.sim_commands==[]
    # Explicit OFF always skips, and drops both saved tokens.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        s.pending_print_mode='INDEPENDENT';s.pending_toolshifts=10
        s.cmd_AUTO_XY_GATE(Cmd(mode='PRINT'))
        assert s.pending_print_mode is None and s.pending_toolshifts is None
        assert s.sim_commands==[]
    # Invalid metadata is rejected at the earliest hook.
    with tempfile.TemporaryDirectory() as tmp:
        c=Config();s=SimGate(c);s.results=tmp
        c.p.var['auto_xy_enabled']=1
        try:s.cmd_AUTO_XY_CAPTURE_MODE(Cmd(mode='PRINT',toolshifts=-1))
        except ValueError as exc:assert 'negative TOTAL_TOOLSHIFTS' in str(exc)
        else:raise AssertionError('Accepted a negative toolshift count')
    print('PASS: single T0/T1 bypass; COPY/MIRROR bypass; independent two-tool calibrates; no stale tokens.')


def test_mode_install_guards():
    import importlib.util
    spec_i=importlib.util.spec_from_file_location('installer2',root/'install_stage6.py')
    mod=importlib.util.module_from_spec(spec_i);spec_i.loader.exec_module(mod)
    release=(root/'auto_xy_print.cfg').read_text()
    assert '[gcode_macro _USER_START_PRINT]' in release
    assert 'AUTO_XY_CAPTURE_MODE TOTAL_TOOLSHIFTS={params.TOTAL_TOOLSHIFTS|default(0)|int}' in release
    assert 'AUTO_XY_GATE MODE=PRINT' in release
    with tempfile.TemporaryDirectory() as tmp:
        temp=Path(tmp);mod.CFG=temp
        (temp/'printer.cfg').write_text('[include RatOS.cfg]\n')
        try:mod.validate(root)
        except RuntimeError as exc:
            # no conflicts: this configuration is expected to pass validation
            raise AssertionError(str(exc)) from exc
        (temp/'printer.cfg').write_text('[include RatOS.cfg]\n[gcode_macro _USER_START_PRINT]\ngcode:\n M117 user\n')
        try:mod.validate(root)
        except RuntimeError as exc:assert 'Existing user print-start hook' in str(exc)
        else:raise AssertionError('Installer overwrites an existing _USER_START_PRINT hook')
    print('PASS: installer requires early mode hook, refuses conflicting user-defined START_PRINT hook.')

if __name__=='__main__':
    test_idex_mode_routing()
    test_mode_install_guards()
