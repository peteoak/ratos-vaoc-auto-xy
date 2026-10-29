import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rc15_dev5_upgrade',ROOT/'scripts/upgrade_rc15_dev4_to_dev5.py')
inst=importlib.util.module_from_spec(spec)
spec.loader.exec_module(inst)
class InstallerTests(unittest.TestCase):
    def home(self,temp):
        home=Path(temp)/'pi'
        for part in ('auto_xy/klipper','auto_xy/vision','auto_xy/rollbacks',
                     'printer_data/config','klipper/klippy/extras'):(home/part).mkdir(parents=True)
        fixtures={'auto_xy/klipper/auto_xy_gate.py':'dev4_gate.py',
                  'auto_xy/vision/auto_xy_vision_worker.py':'dev4_worker.py',
                  'auto_xy/vision/auto_xy_detector.py':'dev4_detector.py'}
        for dest,name in fixtures.items():shutil.copy2(ROOT/'tests/fixtures'/name,home/dest)
        (home/'printer_data/config/printer.cfg').write_text('[include auto_xy.cfg]\n')
        (home/'printer_data/config/auto_xy.cfg').write_text('''[include auto_xy_macros.cfg]
[auto_xy_gate]
settling_path: original
camera_worker_mode: persistent
allow_experimental_alignment_writes: False
camera_diagnostics_enabled: True
settle: 1.4
camera_pair_gap: 0.8
''')
        os.symlink(str(home/'auto_xy/klipper/auto_xy_gate.py'),home/'klipper/klippy/extras/auto_xy_gate.py')
        return home
    def call(self,home,*args):
        before=sys.argv
        try:
            sys.argv=['upgrade_rc15_dev4_to_dev5.py',*args,'--home',str(home),'--offline']
            with contextlib.redirect_stdout(io.StringIO()) as capture:inst.main()
            return capture.getvalue()
        finally:sys.argv=before
    def test_safeguarded_install_exact_hash_and_rollback(self):
        inst.verify_package()
        with tempfile.TemporaryDirectory() as tmp:
            home=self.home(tmp)
            self.assertIn('PREFLIGHT PASS',self.call(home,'--check'))
            with self.assertRaisesRegex(RuntimeError,'confirmation token'):
                self.call(home,'--install')
            self.assertIn('INSTALL COMPLETE',self.call(home,'--install','--confirm','INSTALL_RC15_DEV5'))
            inst.check_sources(home,{k:inst.sha(ROOT/v) for k,v in inst.FILES.items()})
            backups=list((home/'auto_xy/rollbacks').iterdir())
            self.assertEqual(len(backups),1)
            self.assertIn('RESTORED',self.call(home,'--rollback',str(backups[0]),'--confirm','ROLLBACK_RC15_DEV5'))
            inst.check_sources(home,inst.ORIGINAL_RC15_DEV4)
    def test_refuses_unknown_runtime_and_experimental_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.home(tmp)
            cfg=home/'printer_data/config/auto_xy.cfg'
            cfg.write_text(cfg.read_text().replace('settling_path: original','settling_path: diagonal'))
            with self.assertRaisesRegex(RuntimeError,'safe setting'):
                self.call(home,'--check')
            cfg.write_text(cfg.read_text().replace('settling_path: diagonal','settling_path: original'))
            (home/'auto_xy/vision/auto_xy_detector.py').write_text('unknown patch')
            with self.assertRaisesRegex(RuntimeError,'Different runtime source'):
                self.call(home,'--check')
    def test_preflight_allows_existing_two_stage_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.home(tmp)
            cfg=home/'printer_data/config/auto_xy.cfg'
            cfg.write_text(cfg.read_text()+'camera_detector_mode: two_stage\n')
            self.assertIn('PREFLIGHT PASS',self.call(home,'--check'))

    def test_rollback_refuses_dev5_only_config_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.home(tmp)
            self.call(home,'--install','--confirm','INSTALL_RC15_DEV5')
            cfg=home/'printer_data/config/auto_xy.cfg'
            cfg.write_text(cfg.read_text()+'camera_detector_mode: two_stage_roi\n')
            backup=next((home/'auto_xy/rollbacks').iterdir())
            with self.assertRaisesRegex(RuntimeError,'Change camera_detector_mode'):
                self.call(home,'--rollback',str(backup),'--confirm','ROLLBACK_RC15_DEV5')
            cfg.write_text(cfg.read_text().replace('camera_detector_mode: two_stage_roi\n','camera_detector_mode: two_stage\n'))
            self.assertIn('RESTORED',self.call(home,'--rollback',str(backup),'--confirm','ROLLBACK_RC15_DEV5'))

if __name__=='__main__':unittest.main(verbosity=2)
