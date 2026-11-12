"""Offline tests for single-file RC16 vision refresh and rollback."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location(
    'rc16_vision_refresh',ROOT/'scripts/rc16_vision_refresh.py')
refresh=importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class VisionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'pi'
        (self.home/'auto_xy/vision').mkdir(parents=True)
        (self.home/'auto_xy/rollbacks').mkdir(parents=True)
        self.target=self.home/refresh.TARGET
        self.original=(ROOT/'src/auto_xy_reference_signature_rc16.py').read_bytes()
        self.target.write_bytes(self.original)
        self.new=b'# simulated newly reviewed vision matcher\n'
        self.guard=patch.object(refresh,'check_sources',
            side_effect=lambda root,home:(self.original,self.new))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_idle_checks_reject_held_or_active_prints(self):
        state={'print_stats':{'state':'standby'},
               'virtual_sdcard':{'is_active':False,'file_path':'','filename':''},
               'gcode_macro _VAOC':{'is_started':False},
               'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False}}
        refresh.check_idle(state)
        state['print_stats']['state']='paused'
        with self.assertRaises(refresh.Refusal):
            refresh.check_idle(state)
        state['print_stats']['state']='standby'
        state['virtual_sdcard']['filename']='held.gcode'
        with self.assertRaises(refresh.Refusal):
            refresh.check_idle(state)
        state['virtual_sdcard']['filename']=''
        state['gcode_macro _VAOC']['is_started']=True
        with self.assertRaises(refresh.Refusal):
            refresh.check_idle(state)

    def test_offline_refresh_updates_only_pinned_target(self):
        result,snapshot=refresh.install(ROOT,self.home,live=False)
        self.assertEqual(result,'updated')
        self.assertEqual(self.target.read_bytes(),self.new)
        self.assertEqual((snapshot/'signature-original.py').read_bytes(),self.original)
        self.assertTrue((snapshot/'receipt.json').exists())

    def test_restart_failure_restores_exact_old_signature(self):
        calls=[]
        def restart():
            calls.append('restart')
            if len(calls)==1:
                raise OSError('simulated Klipper restart failure')
        with patch.object(refresh,'read_status',return_value={
                'print_stats':{'state':'standby'},
                'virtual_sdcard':{'is_active':False,'filename':'','file_path':''},
                'gcode_macro _VAOC':{'is_started':False},
                'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False}}):
            with self.assertRaisesRegex(refresh.Refusal,'original verified file restored'):
                refresh.install(ROOT,self.home,live=True,
                                do_restart=restart,wait=lambda:None)
        self.assertEqual(calls,['restart','restart'])
        self.assertEqual(self.target.read_bytes(),self.original)

    def test_prewrite_refusal_does_not_claim_broken_rollback(self):
        with patch.object(refresh,'read_status',
                          side_effect=[{
                'print_stats':{'state':'standby'},
                'virtual_sdcard':{'is_active':False,'filename':'','file_path':''},
                'gcode_macro _VAOC':{'is_started':False},
                'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False}},
                          {'print_stats':{'state':'paused'}}]):
            with self.assertRaisesRegex(refresh.Refusal,'before replacing'):
                refresh.install(ROOT,self.home,live=True,
                                do_restart=lambda:None,wait=lambda:None)
        self.assertEqual(self.target.read_bytes(),self.original)


if __name__=='__main__':
    unittest.main()
