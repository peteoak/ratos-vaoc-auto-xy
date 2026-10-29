#!/usr/bin/env python3
"""Offline end-to-end execution of real gate control-flow with fake motion/camera.
No Klipper, Moonraker, heaters, calibration writes, or physical camera required.
"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('auto_xy_gate_dev3',ROOT/'src/auto_xy_gate.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Reactor:
    def monotonic(self):return 100.
class Gcmd:
    def __init__(self):self.messages=[]
    def get(self,name,default=None):return default
    def respond_info(self,message):self.messages.append(message)
    def error(self,message):return RuntimeError(message)

class GateIntegration(unittest.TestCase):
    def build(self,root,endurance):
        g=m.AutoXYGate.__new__(m.AutoXYGate)
        g.reactor=Reactor()
        g.gcode_events=[];g._retry_precommitted=False;g._retry_context=None
        g._protected_inside_gate=False;g.protected_print_enabled=False
        g._endurance_request=({'tool_changes':2,'drift_limit_mm':1.0,
                               'timeout_seconds':3600.} if endurance else None)
        g._endurance_active=False;g.busy=False;g._vision_session_invalid=False
        g.pending_print_mode=None;g.pending_toolshifts=None;g.pending_reuse_candidate=False
        g.active_print_mode=None;g.camera_diagnostics_enabled=True
        g.camera_worker_mode='persistent';g.camera_detector_mode='legacy';g.settling_path='original'
        g.allow_experimental_alignment_writes=False;g.inline_print_retry=False
        g.max_correct=3;g.max_verify=3;g.tol=.03;g.max_step=.05;g.max_total=.3
        g._original_heater_targets=None;g._thermal_snapshot=None
        g.results=str(root);g.temp_guard_active=False;g.phase='IDLE'
        g.settle=1.4;g.camera_pair_gap=.8;g.commissioned_detector_guard=False
        g._progress_stage=None;g.last_error='';g.verified_session=None
        g._diag_last_motion_completed_s=None;g._diag_previous_snapshot_started_s=None
        g.needs_reconcile=False;g.vaoc_entered=False;g.active=0;g.count=0
        g.variables_map={'auto_xy_v2_enabled':1,'auto_xy_v2_fault':0,
                         'auto_xy_v2_in_progress':0,'auto_xy_v2_measure_only':0}
        g.variables=lambda:g.variables_map
        g.enabled=lambda:True
        g.flag=lambda name:bool(g.variables_map.get(name,0))
        g.offsets=lambda:[-.045,-.239,.0225]
        g.cp=lambda:[110.,328.,1.85]
        g.tool=lambda:g.active
        g.sts=lambda k:({'print_stats':{'state':'standby'},
                        'toolhead':{'homed_axes':'xyz'},
                        'gcode_macro RatOS':{'default_toolhead':0},
                        'gcode_macro _VAOC':{'is_started':g.native_vaoc},
                        'gcode_macro START_PRINT':{'is_printing_gcode':False}}[k])
        g.native_vaoc=False
        def rg(cmd):
            g.gcode_events.append(cmd)
            if cmd=='_VAOC_START':g.native_vaoc=True
            if cmd=='_VAOC_END':g.native_vaoc=False
            if cmd.startswith('_VAOC_LOAD_TOOL T='):
                g.active=int(cmd.split('=')[-1])
            if cmd.startswith('SAVE_VARIABLE'):
                v=re.search(r'VARIABLE=(\w+) VALUE=(\S+)',cmd)
                if v:g.variables_map[v.group(1)]=float(v.group(2))
        g.run_gcode=rg
        g.ensure_commissioned=lambda cmd:None
        g.wait_for_safe_camera_preflight=lambda cmd:None
        g.check_camera_temps=lambda:None
        g.prepare_camera_at_preheat=lambda cmd:None
        g._invalidate_verified_session=lambda reason:None
        g.ensure_ok=lambda:None
        g.verify_pose=lambda:None
        g.approach=lambda:None
        g.progress_stage=lambda *args:None
        g._stop_vision_process=lambda:None
        g.restore_preflight_heaters=lambda:None
        g._store_verified_session=lambda: (_ for _ in ()).throw(AssertionError('Endurance must not authorize future print reuse'))
        g.nozzle_temps=lambda:{'extruder':149.,'extruder1':149.}
        g.select=lambda t: rg('_VAOC_LOAD_TOOL T=%d'%t) if t!=g.active else None
        g.save_report=lambda: m.AutoXYGate.save_report(g)
        g._endurance_files=lambda include_charts=False:m.AutoXYGate._endurance_files(g,include_charts)
        g.calibrate=lambda t:[[290.,0.],[0.,290.]]
        g.settle_toolshifts=lambda mat,cmd:None
        g.measure=lambda mat,label:[.008,.004]
        def worker_capture(label,wait_before=None):
            # Preflight and 2 images per endurance visit.
            if label!='preflight_t0':
                i=int(re.search(r'endurance_(\d+)',label).group(1))
                x=960+g.active*10+.01*i+(.4 if label.endswith('_b') else 0)
            else:x=960
            point=[x,535.]
            g.report.setdefault('captures',[]).append({'label':label,'image':str(Path(g.directory)/('img-'+label+'.jpg')),
                  'sha256':label,'center_px':point,'camera_diagnostics':{}})
            return point
        g.worker_capture=worker_capture
        return g
    def test_endurance_from_full_calibration_to_vaoc_exit(self):
        with tempfile.TemporaryDirectory() as d:
            g=self.build(d,True);cmd=Gcmd()
            m.AutoXYGate.cmd_AUTO_XY_GATE(g,m._RetryGcmd(cmd,'IDLE'))
            reports=list(Path(d).glob('*/report.json'))
            self.assertEqual(len(reports),1)
            data=json.loads(reports[0].read_text())
            self.assertEqual(data['status'],'ENDURANCE_COMPLETE_AND_VAOC_EXITED')
            self.assertTrue(data['endurance_calibration_verified'])
            self.assertEqual(data['operation_mode'],'ENDURANCE_AUTO_CALIBRATE')
            self.assertEqual(len(data['endurance_visits']),3)
            self.assertEqual(len(data['captures']),7)
            self.assertEqual(g.gcode_events.count('_VAOC_START'),1)
            self.assertEqual(g.gcode_events.count('_VAOC_END'),1)
            self.assertFalse(g.native_vaoc)
            self.assertEqual(g.variables_map['auto_xy_v2_in_progress'],0)
            self.assertEqual(g.variables_map['auto_xy_v2_fault'],0)
            self.assertEqual(len([x for x in g.gcode_events if '_VAOC_SET_TOOL' in x]),0)
            self.assertEqual(g.gcode_events.count('_VAOC_LOAD_TOOL T=1'),1)
            self.assertTrue((reports[0].parent/'endurance_images.csv').exists())
            self.assertEqual(len(list(reports[0].parent.glob('endurance_*.svg'))),8)
    def test_public_endurance_command_routes_to_verified_gate(self):
        with tempfile.TemporaryDirectory() as d:
            g=self.build(d,False)
            g.reuse_enabled=lambda:False
            g.virtual_sd_active=lambda:False
            cmd=Gcmd()
            cmd.get=lambda name,default=None: {'TOOL_CHANGES':'3','DRIFT_LIMIT':'1.0'}.get(name,default)
            m.AutoXYGate.cmd_AUTO_XY_ENDURANCE_RUN(g,cmd)
            self.assertIsNone(g._endurance_request)
            self.assertFalse(g._endurance_active)
            data=json.loads(next(Path(d).glob('*/report.json')).read_text())
            self.assertEqual(data['status'],'ENDURANCE_COMPLETE_AND_VAOC_EXITED')
            self.assertEqual(data['endurance']['tool_changes_completed'],3)
            self.assertFalse(g.native_vaoc)
    def test_endurance_capture_failure_latches_fault_preserves_calibration_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            g=self.build(d,True);cmd=Gcmd()
            original_capture=g.worker_capture
            count=[0]
            def broken_capture(label,wait_before=None):
                if label.startswith('endurance_'):
                    count[0]+=1
                    if count[0]==4:
                        raise m.GateAbort('Camera/nozzle detection failed at injected endurance frame')
                return original_capture(label,wait_before)
            g.worker_capture=broken_capture
            g.retryable_failure=lambda error,original:False
            g.try_inline_print_retry=lambda *args:None
            g.prompt_on_failure=lambda *args: (_ for _ in ()).throw(AssertionError('Endurance must never offer print prompt'))
            with patch('logging.exception'):
                with self.assertRaisesRegex(RuntimeError,'AUTO XY STANDALONE CALIBRATION FAILED'):
                    m.AutoXYGate.cmd_AUTO_XY_GATE(g,m._RetryGcmd(cmd,'IDLE'))
            data=json.loads(next(Path(d).glob('*/report.json')).read_text())
            self.assertEqual(data['status'],'FAILED_DO_NOT_PRINT')
            self.assertTrue(data['endurance_calibration_verified'])
            self.assertEqual(data['endurance']['status'],'FAILED')
            self.assertEqual(data['endurance']['visits_completed'],1)
            self.assertEqual(g.variables_map['auto_xy_v2_fault'],1)
            self.assertEqual(g.variables_map['auto_xy_v2_enabled'],0)
            self.assertEqual(g.variables_map['auto_xy_v2_in_progress'],1)
            self.assertTrue(g.native_vaoc)   # fail-closed, manual safe recovery
            self.assertNotIn('rollback',data)
            self.assertGreaterEqual(len(list(next(Path(d).glob('*/report.json')).parent.glob('endurance_*.svg'))),4)
    def test_normal_standalone_not_changed_by_endurance_feature(self):
        with tempfile.TemporaryDirectory() as d:
            g=self.build(d,False);cmd=Gcmd()
            g._store_verified_session=lambda:None
            m.AutoXYGate.cmd_AUTO_XY_GATE(g,m._RetryGcmd(cmd,'IDLE'))
            data=json.loads(next(Path(d).glob('*/report.json')).read_text())
            self.assertEqual(data['status'],'CALIBRATED_AND_VAOC_EXITED')
            self.assertNotIn('endurance',data)
            self.assertNotIn('endurance_visits',data)
            self.assertEqual(g.gcode_events.count('_VAOC_START'),1)
            self.assertEqual(g.gcode_events.count('_VAOC_END'),1)

if __name__=='__main__':unittest.main(verbosity=2)
