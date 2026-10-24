#!/usr/bin/env python3
"""Standalone stdlib-only retention and uninstall/reinstall regression coverage."""
import datetime as dt
import json
import tempfile
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

import install_stage6
from auto_xy_retention import retention_plan, execute
from printer_cfg_editor import remove_all_autoxy_includes
import uninstall_auto_xy

SAVE = ('[include RatOS.cfg]\n[printer]\nkinematics: corexy\n\n'
        '[include auto_xy_print.cfg]\n\n'
        '#*# <---------------------- SAVE_CONFIG ---------------------->\n'
        '#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n'
        '#*# [extruder]\n#*# pid_kp = 12.2\n'
        '#*# [probe]\n#*# z_offset = 0.99\n')

class RetentionTests(unittest.TestCase):
    NOW=dt.datetime(2026,10,24,12,0,0)
    def make(self, root, days, status, images=2):
        d = self.NOW - dt.timedelta(days=days)
        folder=root/(d.strftime('%Y%m%d-%H%M%S')+'_print')
        folder.mkdir()
        (folder/'report.json').write_text(json.dumps({'status':status}))
        for i in range(images): (folder/f'{i+1:03d}_t0.jpg').write_bytes(b'JPEG')
        return folder
    def test_success_images_7_days_fail_images_30_reports_90(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            good6=self.make(root,6,'CALIBRATED_AND_VAOC_EXITED')
            good8=self.make(root,8,'CALIBRATED_AND_VAOC_EXITED')
            fail20=self.make(root,20,'FAILED_DO_NOT_PRINT')
            fail31=self.make(root,31,'FAILED_DO_NOT_PRINT')
            old=self.make(root,91,'RECOVERED_INLINE')
            actions, skipped=retention_plan(root,self.NOW)
            self.assertEqual(len(actions),5) # 2 imgs for good8, fail31; 1 old dir
            self.assertEqual(len(skipped),0)
            self.assertEqual(execute(actions),5)
            self.assertEqual(len(list(good6.glob('*.jpg'))),2)
            self.assertEqual(len(list(good8.glob('*.jpg'))),0)
            self.assertTrue((good8/'report.json').exists())
            self.assertEqual(len(list(fail20.glob('*.jpg'))),2)
            self.assertEqual(len(list(fail31.glob('*.jpg'))),0)
            self.assertFalse(old.exists())
    def test_unknown_or_incomplete_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.make(root,100,'STARTING')
            b=self.make(root,95,'FAILED_DO_NOT_PRINT')
            (b/'my_custom_notes.txt').write_text('KEEP')
            c=self.make(root,110,'UNRECOGNISED')
            actions,skipped=retention_plan(root,self.NOW)
            self.assertEqual(actions,[])
            self.assertGreaterEqual(len(skipped),3)
            self.assertTrue((a/'001_t0.jpg').exists())
            self.assertTrue((c/'report.json').exists())
            self.assertTrue((b/'my_custom_notes.txt').exists())
    def test_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); external=Path(tmp)/'external.jpg';external.write_text('keep')
            d=self.make(root,45,'FAILED_DO_NOT_PRINT')
            (d/'001_t0.jpg').unlink()
            (d/'001_t0.jpg').symlink_to(external)
            actions,_=retention_plan(root,self.NOW)
            self.assertEqual([p.name for k,p,_ in actions],['002_t0.jpg'])
            execute(actions)
            self.assertEqual(external.read_text(),'keep')
    def test_preview_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=self.make(Path(tmp),60,'FAILED_DO_NOT_PRINT')
            actions,_=retention_plan(tmp,self.NOW)
            self.assertEqual(len(actions),2)
            self.assertTrue((d/'001_t0.jpg').exists())

class UninstallTests(unittest.TestCase):
    def prepare(self,tmp):
        home=Path(tmp)
        cfg=home/'printer_data/config';cfg.mkdir(parents=True)
        (cfg/'printer.cfg').write_text(SAVE)
        (cfg/'ratos-variables.cfg').write_text("{'idex_xoffset': -0.1974339, 'auto_xy_enabled': 0}\n")
        (cfg/'auto_xy_print.cfg').write_text('[auto_xy_gate]\n[gcode_macro AUTO_XY_TOGGLE]\ngcode:\n')
        ext=home/'klipper/klippy/extras/auto_xy_gate.py';ext.parent.mkdir(parents=True)
        ext.write_text('class AutoXYGate:\n    cmd_AUTO_XY_GATE_help=1\n')
        stage=home/'auto_xy/stage6';stage.mkdir(parents=True)
        (stage/'auto_xy_detector.py').write_text('test')
        (home/'auto_xy/print_gate_runs').mkdir()
        (home/'auto_xy/print_gate_runs/test.jpg').write_bytes(b'data')
        (home/'auto-xy-venv').mkdir()
        (home/'auto-xy-venv/python').write_text('test')
        return home
    def test_uninstall_safe_and_preserves_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.prepare(tmp)
            before=(home/'printer_data/config/ratos-variables.cfg').read_bytes()
            paths, updated, actions=uninstall_auto_xy.inspect(home)
            self.assertIn('#*# pid_kp = 12.2',updated)
            self.assertNotIn('[include auto_xy_print.cfg]',updated)
            self.assertIn(('edit',paths['cfg']),actions)
            backup=uninstall_auto_xy.apply_uninstall(home)
            self.assertTrue((backup/'variables__ratos-variables.cfg').exists())
            self.assertEqual((home/'printer_data/config/ratos-variables.cfg').read_bytes(),before)
            self.assertFalse((home/'klipper/klippy/extras/auto_xy_gate.py').exists())
            self.assertFalse((home/'printer_data/config/auto_xy_print.cfg').exists())
            self.assertTrue((home/'auto_xy/print_gate_runs/test.jpg').exists())
            self.assertTrue((home/'auto-xy-venv').exists())
            self.assertEqual((home/'printer_data/config/printer.cfg').read_text().split('#*#',1)[1],SAVE.split('#*#',1)[1])
    def test_full_purge_keeps_external_backup_and_saved_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.prepare(tmp)
            original=(home/'printer_data/config/ratos-variables.cfg').read_bytes()
            backup=uninstall_auto_xy.apply_uninstall(home,purge_project=True,purge_venv=True)
            self.assertFalse((home/'auto_xy').exists())
            self.assertFalse((home/'auto-xy-venv').exists())
            self.assertTrue((backup/'cfg__printer.cfg').exists())
            self.assertEqual((home/'printer_data/config/ratos-variables.cfg').read_bytes(),original)
            self.assertNotIn('[include auto_xy_print.cfg]',(home/'printer_data/config/printer.cfg').read_text())
    def test_reinstall_printer_cfg_roundtrip(self):
        from printer_cfg_editor import install_include
        with tempfile.TemporaryDirectory() as tmp:
            home=self.prepare(tmp)
            uninstall_auto_xy.apply_uninstall(home,purge_project=True,purge_venv=True)
            cfg=home/'printer_data/config/printer.cfg'
            restored=install_include(cfg.read_text())
            self.assertIn('[include auto_xy_print.cfg]',restored)
            self.assertEqual(restored.split('#*#',1)[1],SAVE.split('#*#',1)[1])
    def test_full_install_uninstall_reinstall_end_to_end(self):
        """Use the real installer twice around a full purge with mocked Moonraker."""
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            cfg=home/'printer_data/config';cfg.mkdir(parents=True)
            (cfg/'printer.cfg').write_text(SAVE.replace('[include auto_xy_print.cfg]\n\n',''))
            vars=cfg/'ratos-variables.cfg'
            vars.write_text("{'idex_xoffset': -0.1974339, 'auto_xy_enabled': 0}\n")
            (cfg/'RatOS').mkdir()
            (cfg/'RatOS/macros.cfg').write_text("""[gcode_macro START_PRINT]
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
            stage=home/'auto_xy';stage.mkdir()
            pyvenv=home/'auto-xy-venv';pyvenv.mkdir()
            kl=home/'klipper/klippy/extras';kl.mkdir(parents=True)
            before_vars=vars.read_bytes()
            oldhome,oldcfg,olddest,oldkl=install_stage6.HOME,install_stage6.CFG,install_stage6.DEST,install_stage6.KLIPPER
            oldargs=sys.argv
            def install():
                with mock.patch.object(install_stage6,'live_status',return_value={'auto_xy_enabled':0,'idex_xoffset':-.1974339}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        sys.argv=['install_stage6.py','--check'];install_stage6.run()
                        sys.argv=['install_stage6.py','--install'];install_stage6.run()
            try:
                install_stage6.HOME=home;install_stage6.CFG=cfg
                install_stage6.DEST=stage/'stage6';install_stage6.KLIPPER=kl/'auto_xy_gate.py'
                install()
                self.assertTrue((stage/'stage6/auto_xy_retention.py').exists())
                self.assertIn('[include auto_xy_print.cfg]',(cfg/'printer.cfg').read_text())
                uninstall_auto_xy.apply_uninstall(home,purge_project=True,purge_venv=True)
                self.assertFalse(stage.exists())
                self.assertEqual(vars.read_bytes(),before_vars)
                self.assertNotIn('[include auto_xy_print.cfg]',(cfg/'printer.cfg').read_text())
                stage.mkdir();pyvenv.mkdir()
                install()
                self.assertTrue((stage/'stage6/auto_xy_retention.py').exists())
                self.assertIn('[include auto_xy_print.cfg]',(cfg/'printer.cfg').read_text())
                self.assertEqual(vars.read_bytes(),before_vars)
                self.assertEqual((cfg/'printer.cfg').read_text().split('#*#',1)[1],SAVE.split('#*#',1)[1])
            finally:
                install_stage6.HOME,install_stage6.CFG,install_stage6.DEST,install_stage6.KLIPPER=oldhome,oldcfg,olddest,oldkl
                sys.argv=oldargs

    def test_refuse_unknown_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=self.prepare(tmp)
            (home/'klipper/klippy/extras/auto_xy_gate.py').write_text('unrelated plugin')
            with self.assertRaises(RuntimeError):uninstall_auto_xy.inspect(home)
    def test_known_legacy_include_removed(self):
        self.assertNotIn('[include auto_xy_control.cfg]',remove_all_autoxy_includes(SAVE.replace('auto_xy_print.cfg','auto_xy_control.cfg')))

if __name__ == '__main__':
    unittest.main(verbosity=2)
