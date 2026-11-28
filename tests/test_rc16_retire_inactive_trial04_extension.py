"""Offline tests for unloading obsolete Trial04 *import symlink* only."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('retire', ROOT/'scripts/rc16_retire_inactive_trial04_extension.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class RetireInactiveTrial04Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.home=Path(t.name)
        for fn in (m.SOURCE,m.EXTRA,m.PRINTER_CFG,m.LEGACY_CONFIG,m.RC16_CONFIG):
            (self.home/fn).parent.mkdir(parents=True,exist_ok=True)
        self.source=self.home/m.SOURCE
        self.source.write_bytes(b'known legacy source content')
        self.ext=self.home/m.EXTRA
        self.ext.symlink_to(self.source)
        (self.home/m.PRINTER_CFG).write_text('[include auto_xy_rc16.cfg]\n# [include auto_xy_local_recovery.cfg]\n')
        (self.home/m.RC16_CONFIG).write_text('[auto_xy_ratos_handoff_rc16]\nenabled: True\n')
        (self.home/m.LEGACY_CONFIG).write_text('[auto_xy_local_recovery]\nenabled: True\n')
        (self.home/m.ROLLBACK_ROOT).mkdir(parents=True)
        f=patch.object(m,'LEGACY_SHA256',m.sha(self.source));f.start();self.addCleanup(f.stop)
        self.state=self.state_good()

    def state_good(self):
        return {'print_stats':{'state':'complete'},
                'virtual_sdcard':{'is_active':False,'file_path':None,'filename':None},
                'gcode_macro _VAOC':{'is_started':False},
                'auto_xy_ratos_handoff_rc16':{'enabled':True,'armed':True,'busy':False,
                                              'sd_api_guarded':True,'resume_api_guarded':True},
                'save_variables':{'variables':{'auto_xy_v2_enabled':1,
                                               'auto_xy_v2_fault':0,
                                               'auto_xy_v2_in_progress':0,
                                               'auto_xy_v2_retry_available':0}},
                'configfile':{'settings':{'auto_xy_ratos_handoff_rc16':{},'auto_xy_gate':{}}}}
    def test_retire_preserves_code_config_and_rollback_restore(self):
        before={p:(self.home/p).read_bytes() for p in (m.SOURCE,m.PRINTER_CFG,m.RC16_CONFIG,m.LEGACY_CONFIG)}
        receipt=m.retire(self.home,query=lambda:self.state)
        self.assertFalse(os.path.lexists(self.ext))
        self.assertTrue(receipt.exists())
        for path,contents in before.items():self.assertEqual((self.home/path).read_bytes(),contents)
        m.restore(self.home,receipt,query=lambda:self.state)
        self.assertTrue(self.ext.is_symlink())
        self.assertEqual(self.ext.resolve(),self.source.resolve())

    def test_active_legacy_refused(self):
        self.state['configfile']['settings']['auto_xy_local_recovery']={}
        with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)
        self.assertTrue(self.ext.is_symlink())

    def test_busy_file_or_unverified_guard_refused(self):
        for field,value in [('sd_api_guarded',False),('armed',False),('busy',True)]:
            self.state['auto_xy_ratos_handoff_rc16'][field]=value
            with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)
            self.state['auto_xy_ratos_handoff_rc16'][field]=not value
            self.assertTrue(self.ext.is_symlink())
        self.state['virtual_sdcard']['filename']='held.gcode'
        with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)

    def test_unknown_symlink_refused(self):
        self.ext.unlink();self.ext.symlink_to(self.home/m.PRINTER_CFG)
        with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)
        self.assertTrue(self.ext.is_symlink())

    def test_modified_binary_refused(self):
        self.source.write_bytes(b'changed source not in audit')
        with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)

    def test_active_include_refused(self):
        (self.home/m.PRINTER_CFG).write_text('[include auto_xy_local_recovery.cfg]\n')
        with self.assertRaises(m.Refuse):m.retire(self.home,query=lambda:self.state)

    def test_rollback_after_failed_postflight(self):
        count=[0]
        def query():
            count[0]+=1
            s=self.state_good()
            if count[0]>=3:s['auto_xy_ratos_handoff_rc16']['sd_api_guarded']=False
            return s
        with self.assertRaisesRegex(m.Refuse,'reverted'):
            m.retire(self.home,query=query)
        self.assertTrue(self.ext.is_symlink())
        self.assertEqual(self.ext.resolve(),self.source.resolve())

    def test_receipt_cannot_be_outside_trusted_rollback_root(self):
        fake=self.home/'external/receipt.json';fake.parent.mkdir()
        fake.write_text(json.dumps({'schema':1}))
        with self.assertRaises(m.Refuse):m.verify_snapshot(self.home,fake)

if __name__=='__main__':unittest.main()