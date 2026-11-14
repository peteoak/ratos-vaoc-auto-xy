"""Offline regression tests: version-aware RC16 vision telemetry and rollback."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location(
    'vision_telemetry_refresh',
    ROOT/'scripts/rc16_vision_telemetry_refresh.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VisionTelemetryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.home=root/'pi'
        self.repo=root/'repo'
        self.repo.mkdir()
        for name in ('auto_xy/klipper','auto_xy/vision','auto_xy/rollbacks'):
            (self.home/name).mkdir(parents=True,exist_ok=True)
        items=[]
        self.previous=[]
        for index in range(4):
            source='source%d.py'%index
            target='auto_xy/klipper/gate.py' if index==0 else (
                'auto_xy/vision/module%d.py'%index)
            new=('new-source-%d'%index).encode()
            before=('old-source-%d'%index).encode() if index!=3 else None
            (self.repo/source).write_bytes(new)
            if before is not None:
                (self.home/target).write_bytes(before)
            # Synthetic coherent source states mimic a baseline, a
            # vision-v1 release and a final telemetry-v2 release.
            middle=('vision-v1-%d'%index).encode() if index<2 else new
            items.append((source,target,
                          module.blob(before) if before else None,
                          module.blob(middle),module.blob(new)))
            self.previous.append((target,before))
        self.pin=patch.object(module,'SOURCES',tuple(items))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_check_is_read_only_and_names_four_sources(self):
        state,already=module.checked_files(self.repo,self.home)
        self.assertFalse(already)
        self.assertEqual(len(state),4)
        self.assertFalse((self.home/self.previous[3][0]).exists())

    def test_install_updates_exact_sources_and_adds_detector(self):
        result,backup=module.install(self.repo,self.home,live=False)
        self.assertEqual(result,'updated')
        self.assertTrue(backup.is_dir())
        self.assertEqual(len(list((backup/'originals').glob('*.py'))),3)
        for src,target,_,_,_ in module.SOURCES:
            self.assertEqual((self.home/target).read_bytes(),(self.repo/src).read_bytes())
        self.assertEqual(module.install(self.repo,self.home,live=False)[0],
                         'already-installed')

    def test_partial_install_refused_before_any_write(self):
        (self.home/module.SOURCES[0][1]).write_bytes(
            (self.repo/module.SOURCES[0][0]).read_bytes())
        with self.assertRaisesRegex(module.Refuse,'Mixed old/new'):
            module.install(self.repo,self.home,live=False)
        self.assertFalse((self.home/module.SOURCES[3][1]).exists())

    def test_unknown_existing_module_fails_closed(self):
        (self.home/module.SOURCES[3][1]).write_bytes(b'unreviewed')
        with self.assertRaisesRegex(module.Refuse,'Unrecognised'):
            module.checked_files(self.repo,self.home)

    def test_live_restart_failure_rolls_back_and_removes_new_module(self):
        called=[]
        idle={'print_stats':{'state':'standby'},
              'virtual_sdcard':{'is_active':False,'file_path':'','filename':''},
              'gcode_macro _VAOC':{'is_started':False},
              'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False},
              'save_variables':{'variables':{
                  'auto_xy_v2_fault':0,'auto_xy_v2_in_progress':0}}}
        def reboot():
            called.append(True)
            if len(called)==1:
                raise OSError('simulated Klipper restart failure')
        with patch.object(module,'status',return_value=idle):
            with self.assertRaisesRegex(module.Refuse,'all previous files restored'):
                module.install(self.repo,self.home,live=True,
                               restart_service=reboot,wait_ready=lambda:None)
        self.assertEqual(len(called),2)
        for path,contents in self.previous:
            file=self.home/path
            if contents is None:
                self.assertFalse(file.exists())
            else:
                self.assertEqual(file.read_bytes(),contents)

    def test_active_vaoc_or_fault_blocks_install(self):
        states=[
            {'print_stats':{'state':'paused'}},
            {'print_stats':{'state':'standby'},
             'virtual_sdcard':{'is_active':False,'file_path':'','filename':''},
             'gcode_macro _VAOC':{'is_started':True},
             'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False},
             'save_variables':{'variables':{}}},
        ]
        for state in states:
            with self.subTest(state=state):
                with patch.object(module,'status',return_value=state):
                    with self.assertRaises(module.Refuse):
                        module.install(self.repo,self.home,live=True,
                                       restart_service=lambda:None,
                                       wait_ready=lambda:None)
        for path,contents in self.previous:
            if contents is not None:
                self.assertEqual((self.home/path).read_bytes(),contents)


    def test_coherent_vision_v1_upgrades_directly(self):
        for index, (source,target,old_sha,mid_sha,new_sha) in enumerate(module.SOURCES):
            if index<2:
                (self.home/target).write_bytes(('vision-v1-%d'%index).encode())
            else:
                (self.home/target).write_bytes((self.repo/source).read_bytes())
        entries,already=module.checked_files(self.repo,self.home)
        self.assertFalse(already)
        self.assertEqual(len(entries),4)
        self.assertEqual(module.install(self.repo,self.home,live=False)[0],'updated')
        self.assertTrue(all((self.home/target).read_bytes()==(self.repo/source).read_bytes()
                            for source,target,_,_,_ in module.SOURCES))

    def test_inconsistent_gate_and_worker_release_is_refused(self):
        (self.home/module.SOURCES[0][1]).write_bytes(b'vision-v1-0')
        with self.assertRaisesRegex(module.Refuse,'Mixed old/new'):
            module.checked_files(self.repo,self.home)



if __name__=='__main__':
    unittest.main()
