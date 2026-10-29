import importlib.util,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('fresh_installer_under_test',ROOT/'scripts/install_autoxy.py')
install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)
class FreshInstallerSnapshotTests(unittest.TestCase):
 def test_fresh_install_rollback_offline(self):
  with tempfile.TemporaryDirectory() as td:
   home=Path(td)/'pi'
   for d in ('printer_data/config','klipper/klippy/extras','auto_xy/rollbacks'):(home/d).mkdir(parents=True)
   initial='[mcu]\nserial: /dev/ttyS0\n\n#*# < SAVE_CONFIG >\n#*# example\n'
   (home/'printer_data/config/printer.cfg').write_text(initial)
   changes=install.plan(home,ROOT,'fresh')
   self.assertEqual(len(changes),8)
   self.assertIn(b'[include auto_xy.cfg]',changes['printer_data/config/printer.cfg'])
   self.assertEqual((home/'printer_data/config/printer.cfg').read_text(),initial)
   folder=install.install(home,changes,'fresh',None)
   self.assertTrue((home/'klipper/klippy/extras/auto_xy_gate.py').is_symlink())
   self.assertEqual((home/'auto_xy/klipper/auto_xy_gate.py').read_bytes(),(ROOT/'src/auto_xy_gate.py').read_bytes())
   self.assertIn('camera_detector_mode: legacy',(home/'printer_data/config/auto_xy.cfg').read_text())
   install.rollback(home,folder,live=False)
   self.assertEqual((home/'printer_data/config/printer.cfg').read_text(),initial)
   self.assertFalse((home/'klipper/klippy/extras/auto_xy_gate.py').exists())
   self.assertFalse((home/'printer_data/config/auto_xy.cfg').exists())
 def test_existing_is_never_fresh(self):
  with tempfile.TemporaryDirectory() as td:
   home=Path(td)/'pi'; (home/'printer_data/config').mkdir(parents=True)
   (home/'printer_data/config/auto_xy.cfg').write_text('existing config')
   self.assertEqual(install.identify(home),'upgrade')
if __name__=='__main__':unittest.main()
