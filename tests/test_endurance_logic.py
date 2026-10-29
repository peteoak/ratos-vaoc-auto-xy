#!/usr/bin/env python3
"""Controller logic against a no-printer, no-camera simulator."""
import ast
import csv
import importlib.util
import json
import math
from pathlib import Path
import re
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'src/auto_xy_gate.py'
spec=importlib.util.spec_from_file_location('auto_xy_gate_dev3',P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FakeReactor:
    def monotonic(self):return 123.0

class FakeGcmd:
    def __init__(self, opts=None):self.opts=opts or {};self.messages=[]
    def get(self,name,default=None):return self.opts.get(name,default)
    def respond_info(self,message):self.messages.append(message)
    def error(self,message):return RuntimeError(message)

class FakeGate:
    """Mocked hardware operations; genuine endurance report generation and math."""
    def __init__(self,folder,changes=4,drift=1.0,move_factor=0.0):
        self.report={'captures':[]}
        self.directory=str(folder)
        self.reactor=FakeReactor()
        self.phase='VERIFYING'
        self._endurance_request={'tool_changes':changes,'drift_limit_mm':drift,'timeout_seconds':3600}
        self._endurance_active=False
        self.active=0
        self.select_calls=[]
        self.capture_labels=[]
        self.offsets_value=[-.045,-.238,.0225]
        self.move_factor=move_factor
        self.camera_pair_gap=.8
        self.nozzle_temps=lambda:{'extruder':149.1,'extruder1':149.4}
        self.ensure_ok=lambda:None
        self.verify_pose=lambda:None
        self.approach=lambda:None
        self.offsets=lambda:list(self.offsets_value)
        self.select=self._select
        self.worker_capture=self._worker_capture
        self.save_report=self._save_report
        self._endurance_files=lambda include_charts=False:m.AutoXYGate._endurance_files(self,include_charts)
    def _select(self,tool):
        if self.active!=tool:self.select_calls.append((self.active,tool))
        self.active=tool
    def _save_report(self):
        (Path(self.directory)/'report.json').write_text(json.dumps(self.report))
    def _worker_capture(self,label,wait_before=None):
        visit=int(re.search(r'endurance_(\d+)_',label).group(1))
        tool=int(re.search(r'_t(\d)_',label).group(1))
        tag=label[-1]
        self.capture_labels.append(label)
        assert self.active==tool
        x=960+tool*6+visit*self.move_factor+(0.7 if tag=='b' else 0.0)
        y=535+tool*2+(0.3 if tag=='b' else 0.0)
        point=[x,y]
        self.report['captures'].append({'label':label,'tool':tool,
            'image':str(Path(self.directory)/(label+'.jpg')),'sha256':label,
            'center_px':point,'camera_diagnostics':{
                'seconds_motion_to_http_request':1.4,
                'worker_snapshot_monotonic_s':{'request_started':123.5}}})
        return point

class TestEndurance(unittest.TestCase):
    def test_expected_100_changes_101_visits_202_images_and_eight_charts(self):
        with tempfile.TemporaryDirectory() as d:
            g=FakeGate(d,changes=100,move_factor=.01)
            m.AutoXYGate._run_endurance(g,[[290,0],[0,290]],FakeGcmd())
            self.assertEqual(len(g.report['endurance_visits']),101)
            self.assertEqual(len(g.select_calls),100)
            self.assertEqual(len(g.report['captures']),202)
            self.assertEqual(g.report['endurance']['status'],'COMPLETED')
            self.assertEqual(sum(x['tool']==0 for x in g.report['endurance_visits']),51)
            self.assertEqual(sum(x['tool']==1 for x in g.report['endurance_visits']),50)
            self.assertEqual(len(list(Path(d).glob('endurance_*.svg'))),8)
            with (Path(d)/'endurance_images.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),202)
            self.assertEqual([r['image'] for r in rows[:4]],['A','B','A','B'])
            self.assertTrue(all(r['x_px']!='' for r in rows))
            self.assertAlmostEqual(g.report['endurance_visits'][0]['b_minus_a_mm'][0],.7/290,places=7)
            self.assertEqual(g.report['endurance_visits'][0]['images']['A']['from_tool_baseline_mm'],[0.,0.])
            self.assertEqual(g.report['endurance_visits'][1]['images']['B']['from_tool_baseline_mm'],[0.,0.])
    def test_loose_drift_limit_still_aborts_and_preserves_partial_archive(self):
        with tempfile.TemporaryDirectory() as d:
            g=FakeGate(d,changes=25,move_factor=80.)
            with self.assertRaisesRegex(m.GateAbort,'diagnostic .* ceiling'):
                m.AutoXYGate._run_endurance(g,[[290,0],[0,290]],FakeGcmd())
            self.assertLess(len(g.report['endurance_visits']),26)
            self.assertGreater(len(g.report['endurance_visits']),2)
            self.assertTrue((Path(d)/'endurance_images.csv').exists())
            self.assertEqual(len(g.report['captures']),len(g.report['endurance_visits'])*2)
    def test_config_and_normal_gate_do_not_rewrite_measuring(self):
        src=P.read_text()
        self.assertIn('self._run_endurance(mat,gcmd)',src)
        self.assertIn("if endurance_requested:",src)
        self.assertIn("'MEASURE_ONLY_VERIFIED_AND_VAOC_EXITED'",src)
        self.assertIn("'CALIBRATED_AND_VAOC_EXITED'",src)
        self.assertIn("self.measure(mat,'cycle_%02d'%cycle)",src)
        self.assertIn("self.measure(mat,'measure_only_%02d'%check)",src)
        self.assertIn("self.run_gcode('_VAOC_END')",src)
        self.assertIn("self.needs_reconcile=False",src)
        self.assertNotIn('self.apply(',src[src.index('    def _run_endurance'):src.index('    def health(')])
    def test_off_and_measure_only_and_unqualified_path_refused_before_hardware(self):
        def make_fake(enabled=True,measure_only=False,path='original'):
            g=m.AutoXYGate.__new__(m.AutoXYGate)
            g.busy=False;g._endurance_request=None;g._retry_precommitted=False
            g._retry_context=None;g._protected_inside_gate=False
            g.enabled=lambda:enabled;g.flag=lambda name: measure_only if name=='auto_xy_v2_measure_only' else False
            g.settling_path=path;g.reuse_enabled=lambda:False
            return g
        for kwargs,phrase in [({'enabled':False},'Turn AUTO_XY_ON'),
                              ({'measure_only':True},'not MEASURE_ONLY'),
                              ({'path':'diagonal'},'Original')]:
            with self.subTest(kwargs=kwargs):
                fake=make_fake(**kwargs)
                with self.assertRaisesRegex(RuntimeError,phrase):
                    m.AutoXYGate.cmd_AUTO_XY_ENDURANCE_RUN(fake,FakeGcmd())
    def test_tool_change_count_bounds_and_drift_limit(self):
        g=m.AutoXYGate.__new__(m.AutoXYGate)
        g.busy=False;g._endurance_request=None;g._retry_precommitted=False
        g._retry_context=None;g._protected_inside_gate=False
        g.enabled=lambda:True;g.flag=lambda _:False;g.settling_path='original'
        g.reuse_enabled=lambda:False;g.sts=lambda _: {'state':'standby'}
        g.virtual_sd_active=lambda:False
        for options in ({'TOOL_CHANGES':'0'}, {'TOOL_CHANGES':'201'},
                        {'DRIFT_LIMIT':'2.0'}, {'DRIFT_LIMIT':'NaN'},
                        {'TOOL_CHANGES':'3.5'}):
            with self.subTest(options=options):
                with self.assertRaises(RuntimeError):
                    m.AutoXYGate.cmd_AUTO_XY_ENDURANCE_RUN(g,FakeGcmd(options))

if __name__=='__main__':unittest.main(verbosity=2)
