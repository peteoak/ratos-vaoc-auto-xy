"""Offline tests of the no-delete, off-Pi-only archive tool."""
import importlib.util
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MOD_PATH = Path(__file__).resolve().parents[1]/'scripts/rc16_archive_historical_backups.py'
spec = importlib.util.spec_from_file_location('archive_tool',MOD_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root/'pi'
        self.home.mkdir()
        self.dest = root/'external'
        self.dest.mkdir()
        self.old = self.home/'old-backup'
        self.old.mkdir()
        (self.old/'sub').mkdir()
        (self.old/'sub'/'ratos-settings.cfg').write_bytes(b'[include etc]\n')
        (self.old/'blob.bin').write_bytes(b'abc'*100)
        # Internal links must be archived without following them.
        (self.old/'link-to-elsewhere').symlink_to('/private/home/pi/secrets')
        self.zips = self.home/'printer_data/config'
        self.zips.mkdir(parents=True)
        (self.zips/'snapshot.zip').write_bytes(b'PK\x03\x04example')
        self.source_plan = {'old':('old-backup',),'config':('printer_data/config/snapshot.zip',)}
        p = patch.object(tool,'GROUPS',self.source_plan)
        p.start()
        self.addCleanup(p.stop)

    def test_plan_and_snapshot_are_read_only(self):
        before = {str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file() and not p.is_symlink()}
        result = tool.total_report(self.home)
        self.assertEqual(set(result), {'old','config'})
        self.assertEqual(result['old']['files'],2)
        self.assertEqual(result['config']['files'],1)
        after = {str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file() and not p.is_symlink()}
        self.assertEqual(before,after)

    def test_archive_verify_and_symlink_preserved_without_exfiltration(self):
        target = tool.backup(self.home,self.dest,strict_external=False,archive_stamp='unit-test')
        self.assertTrue((target/'receipt.json').is_file())
        self.assertTrue(tool.verify_set(self.home,target,strict_external=False))
        with tarfile.open(target/'old.tar.gz','r:gz') as t:
            link=t.getmember('old-backup/link-to-elsewhere')
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname,'/private/home/pi/secrets')
            self.assertFalse(any(x.name.startswith('/private') for x in t.getmembers()))
        self.assertEqual((self.old/'blob.bin').read_bytes(),b'abc'*100)

    def test_refuse_same_pi_filesystem(self):
        with self.assertRaises(tool.Refuse):
            tool.validate_external(self.home,self.dest)
        with self.assertRaises(tool.Refuse):
            tool.backup(self.home,self.dest,strict_external=True,archive_stamp='never')
        self.assertFalse(any(self.dest.iterdir()))

    def test_does_not_replace_existing_output(self):
        path=self.dest/'old.tar.gz'
        path.write_text('sentinel')
        snapshot=tool.scan_group(self.home,'old',hash_files=True)
        with self.assertRaises(tool.Refuse):
            tool.archive_group(self.home,snapshot,path)
        self.assertEqual(path.read_text(),'sentinel')

    def test_reject_special_fifo_without_touching_it(self):
        fifo=self.old/'strange-pipe'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(tool.Refuse,'Special file'):
            tool.scan_group(self.home,'old',hash_files=False)
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_reject_source_change_after_verified_backup(self):
        target=tool.backup(self.home,self.dest,strict_external=False,archive_stamp='changed')
        (self.old/'blob.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(tool.Refuse,'Source changed'):
            tool.verify_set(self.home,target,strict_external=False)
        self.assertTrue((self.old/'blob.bin').is_file())

    def test_reject_tampered_archive(self):
        target=tool.backup(self.home,self.dest,strict_external=False,archive_stamp='tamper')
        with (target/'old.tar.gz').open('ab') as f:
            f.write(b'WRONG')
        with self.assertRaisesRegex(tool.Refuse,'digest mismatch'):
            tool.verify_set(self.home,target,strict_external=False)

    def test_reject_missing_historical_group(self):
        (self.old/'blob.bin').unlink()
        tool.GROUPS['missing']=('not-here',)
        with self.assertRaisesRegex(tool.Refuse,'Missing/untrusted'):
            tool.total_report(self.home)

    def test_prevent_archive_path_traversal(self):
        for s in ('../bad','/bad','a/../../bad'):
            with self.assertRaises(tool.Refuse):
                tool.relative_safe(s)

    def test_hardlink_content_verified(self):
        os.link(self.old/'blob.bin',self.old/'second-link.bin')
        target=tool.backup(self.home,self.dest,strict_external=False,archive_stamp='hardlink')
        self.assertTrue(tool.verify_set(self.home,target,strict_external=False))

    def test_receipt_not_written_when_archive_verification_fails(self):
        with patch.object(tool,'verify_archive',side_effect=tool.Refuse('damaged')):
            with self.assertRaises(tool.Refuse):
                tool.backup(self.home,self.dest,strict_external=False,archive_stamp='bad')
        self.assertFalse((self.dest/'AutoXY_Historical_Backups_bad'/'receipt.json').exists())

if __name__ == '__main__':
    unittest.main()