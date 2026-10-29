import hashlib,importlib.util,json,os,re,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class SnapshotReleaseTests(unittest.TestCase):
 def test_src_exact_version(self):
  self.assertIn('RELEASE_VERSION = "0.5.0-rc15-dev5"',(ROOT/'src/auto_xy_gate.py').read_text())
 def test_temperature_set_and_experiments_retained(self):
  text=(ROOT/'src/auto_xy_gate.py').read_text()
  for item in ("('ratos','slicer_derived')", "('original','diagonal','dogleg')", "('legacy','two_stage','two_stage_roi')",'thermal_reduction_t0','thermal_reduction_t1'):
   self.assertIn(item,text)
  cfg=(ROOT/'config/auto_xy.cfg.example').read_text()
  self.assertIn('thermal_mode: ratos',cfg)
  self.assertIn('settling_path: original',cfg)
  self.assertIn('protected_print_enabled: False',cfg)
 def test_installer_package_validation(self):
  spec=importlib.util.spec_from_file_location('installed_snapshot',ROOT/'scripts/install_autoxy.py')
  # ratos_extension in sibling scripts dir is part of runtime import
  import sys
  sys.path.insert(0,str(ROOT/'scripts'))
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  module.verify_package(ROOT)
 def test_runtime_src_checksums(self):
  spec=importlib.util.spec_from_file_location('verify_runtime',ROOT/'scripts/check_installed_dev5.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  for name in module.FILES.values():self.assertTrue((ROOT/name).is_file())
 def test_macro_sections_unique(self):
  lines=(ROOT/'config/auto_xy_macros.cfg').read_text()
  names=re.findall(r'^\[gcode_macro\s+([^\]]+)\]',lines,re.M)
  self.assertEqual(len(names),len(set(names)))
  self.assertEqual(len(names),20)
if __name__=='__main__':unittest.main()
