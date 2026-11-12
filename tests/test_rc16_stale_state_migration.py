"""RC16 transactional cleanup of stale RC15 safety flags; no print or offset reset."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('migration_state_rc16',
                                            ROOT/'scripts/rc16_active_install.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class StaleStateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.old={'flags':{key:0 for key in mod.MIGRATED_FLAGS},
                  'offsets':{'idex_xoffset':-.127329409629779,
                             'idex_yoffset':-.24902894095742162,
                             'idex_zoffset':.0225}}
        self.old['flags'].update({
            'auto_xy_v2_enabled':1,'auto_xy_v2_fault':1,
            'auto_xy_v2_in_progress':1,'auto_xy_v2_retry_available':1,
            'auto_xy_v2_retry_used':1,'auto_xy_v2_measure_only':1})
        self.sent=[]

    def safe_status(self):
        return ({'virtual_sdcard':{'is_active':False},
                 'print_stats':{'state':'complete'}},{'state':'ready'})

    def test_archive_contains_exact_prior_state_and_offsets(self):
        mod.archive_migration_state(self.folder,self.old)
        path=self.folder/mod.MIGRATION_ARCHIVE
        self.assertEqual(json.loads(path.read_text())['state'],self.old)
        self.assertFalse(path.stat().st_mode & 0o077)
        with self.assertRaises(FileExistsError):
            mod.archive_migration_state(self.folder,self.old)

    def test_only_obsolete_flags_cleared_then_direct_restore(self):
        with patch.object(mod,'read_migration_state',return_value=self.old), \
             patch.object(mod,'set_migration_flag',side_effect=lambda k,v:self.sent.append((k,v))), \
             patch('install_autoxy.moonraker_status',side_effect=self.safe_status):
            mod.reset_stale_flags(self.old)
            self.assertEqual(self.sent,
                [('auto_xy_v2_fault',0),('auto_xy_v2_in_progress',0),
                 ('auto_xy_v2_retry_available',0),('auto_xy_v2_retry_used',0)])
            self.sent.clear()
            mod.restore_migration_state(self.old)
        self.assertEqual(self.sent,list(self.old['flags'].items()))

    def test_no_flags_cleared_if_held_file_still_selected(self):
        selected=({'virtual_sdcard':{'is_active':False,
                                    'file_path':'pending_print.gcode'},
                   'print_stats':{'state':'complete'}},{'state':'ready'})
        with patch.object(mod,'read_migration_state',return_value=self.old), \
             patch.object(mod,'set_migration_flag') as send, \
             patch('install_autoxy.moonraker_status',return_value=selected):
            with self.assertRaisesRegex(mod.InstallationError,'Possible held'):
                mod.reset_stale_flags(self.old)
            send.assert_not_called()

    def test_saved_offsets_changing_during_upgrade_prevents_reset(self):
        changed={'flags':dict(self.old['flags']),
                 'offsets':dict(self.old['offsets'])}
        changed['offsets']['idex_xoffset']+=.01
        with patch.object(mod,'read_migration_state',return_value=changed), \
             patch.object(mod,'set_migration_flag') as send:
            with self.assertRaisesRegex(mod.InstallationError,'state changed'):
                mod.reset_stale_flags(self.old)
            send.assert_not_called()

    def test_only_explicit_fixed_commands_allowed(self):
        with patch.object(mod,'urlopen') as url:
            url.return_value.__enter__.return_value.read.return_value = b'{}'
            mod.post_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=0')
            self.assertEqual(url.call_count,1)
            with self.assertRaisesRegex(mod.InstallationError,'Disallowed'):
                mod.post_gcode('SAVE_VARIABLE VARIABLE=idex_xoffset VALUE=0')
            with self.assertRaisesRegex(mod.InstallationError,'Disallowed'):
                mod.post_gcode('G28')

if __name__=='__main__':
    unittest.main()
