"""Offline tests for narrow reviewed loose ZIP cleanup, no live printer required."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('clean_zip',root/'scripts/rc16_loose_zip_cleanup.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class PackageZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.state=self.home/'auto_xy/rollbacks'
        self.state.mkdir(parents=True)
        self.fns=list(mod.PACKAGES)
        self.zip1=self.home/self.fns[0]
        self.zip2=self.home/self.fns[1]
        self._zip(self.zip1)
        self._zip(self.zip2)
        (self.home/'printer_data/config').mkdir(parents=True)
        self.config=self.home/'printer_data/config/config-20261023-183344.zip'
        self._zip(self.config)
        self.audit=self.home/'AutoXY_Failure_Diagnostics_20261113_075938.zip'
        self._zip(self.audit)
    def _zip(self,path,content=b'old code'):
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('historical.py',content)
    def _plan(self):
        path=self.home/'AutoXY_Zip_Package_Plan_local.json'
        mod.prepare_plan(self.home,path)
        return path
    def test_allowlist_scan_keeps_backup_and_diagnostics_out(self):
        r=mod.audit(self.home)
        self.assertEqual({x['name'] for x in r},{self.zip1.name,self.zip2.name})
        self.assertTrue(self.audit.is_file())
        self.assertTrue(self.config.is_file())
    def test_plan_does_not_modify_any_zip(self):
        before={p:p.read_bytes() for p in (self.zip1,self.zip2,self.audit,self.config)}
        p=self._plan()
        self.assertTrue(p.is_file())
        self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_del_requires_explicit_phrase(self):
        plan=self._plan()
        with self.assertRaises(mod.Refuse): mod.remove(self.home,plan,confirm='yes',state_dir=self.state)
        self.assertTrue(self.zip1.is_file())
        self.assertFalse(list(self.state.iterdir()))
    def test_removes_only_exact_reviewed_root_zip(self):
        plan=self._plan()
        receipt, entries=mod.remove(self.home,plan,confirm='DELETE OLD PACKAGES',state_dir=self.state)
        self.assertFalse(self.zip1.exists())
        self.assertFalse(self.zip2.exists())
        self.assertEqual(len(entries),2)
        self.assertTrue(self.audit.exists())
        self.assertTrue(self.config.exists())
        self.assertEqual(len(json.loads(receipt.read_text())['entries']),2)
    def test_archive_change_after_plan_blocks_every_deletion(self):
        plan=self._plan()
        self._zip(self.zip1,content=b'new data')
        with self.assertRaises(mod.Refuse): mod.remove(self.home,plan,confirm='DELETE OLD PACKAGES',state_dir=self.state)
        self.assertTrue(self.zip2.is_file())
    def test_file_appearing_after_plan_blocks(self):
        plan=self._plan()
        self._zip(self.home/self.fns[2])
        with self.assertRaises(mod.Refuse): mod.remove(self.home,plan,confirm='DELETE OLD PACKAGES',state_dir=self.state)
    def test_unknown_path_forged_plan_refused(self):
        plan=self._plan()
        data=json.loads(plan.read_text())
        data['entries'][0]['name']='config-20261023-183344.zip'
        plan.write_text(json.dumps(data))
        with self.assertRaises(mod.Refuse): mod.remove(self.home,plan,confirm='DELETE OLD PACKAGES',state_dir=self.state)
    def test_missing_output_or_renamed_directory_refused(self):
        plan=self._plan()
        target=self.home/'AutoXY_Zip_Package_Plan_other.json'
        target.symlink_to(plan)
        with self.assertRaises(mod.Refuse): mod.remove(self.home,target,confirm='DELETE OLD PACKAGES',state_dir=self.state)
    def test_symlink_refused(self):
        self.zip1.unlink()
        self.zip1.symlink_to(self.zip2)
        with self.assertRaises(mod.Refuse):mod.audit(self.home)
    def test_hardlink_refused(self):
        (self.home/'linked.zip').hardlink_to(self.zip1)
        with self.assertRaises(mod.Refuse):mod.audit(self.home)
    def test_invalid_zip_refused(self):
        self.zip1.write_bytes(b'not a zip')
        with self.assertRaises(mod.Refuse):mod.audit(self.home)
    def test_size_cap_refused(self):
        self.zip1.write_bytes(b'z'* (mod.MAX_BYTES+1))
        with self.assertRaises(mod.Refuse):mod.audit(self.home)
    def test_bomb_central_directory_refused_without_decompression(self):
        self.zip1.unlink()
        with zipfile.ZipFile(self.zip1, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('huge.bin', b'z'*(130*1024*1024))
        self.assertLess(self.zip1.stat().st_size, mod.MAX_BYTES)
        with self.assertRaises(mod.Refuse): mod.audit(self.home)

    def test_receipt_dir_missing_refused(self):
        plan=self._plan()
        missing=self.home/'notthere'
        with self.assertRaises(mod.Refuse):mod.remove(self.home,plan,confirm='DELETE OLD PACKAGES',state_dir=missing)
        self.assertTrue(self.zip1.is_file())

if __name__ == '__main__':unittest.main()