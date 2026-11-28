"""Offline regression tests: audit reads only code/config metadata."""
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('audit',ROOT/'scripts/rc16_printer_integrity_audit.py')
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'pi'
        self.repo=Path(self.tmp.name)/'repo'
        self.repo.mkdir()
        config=self.home/'printer_data/config'
        config.mkdir(parents=True)
        (config/'printer.cfg').write_text(
            '[include machine.cfg]\n[include auto_xy_local_recovery.cfg]\n'
            '# [include secrets.cfg]\n[include sub/*.cfg]\n')
        (config/'machine.cfg').write_text('[gcode_macro START_PRINT]\ngcode: G28\n')
        (config/'auto_xy_local_recovery.cfg').write_text(
            '[auto_xy_local_recovery]\nenabled: true\npassword: PRIVATE_PASSWORD\n')
        (config/'secrets.cfg').write_text('SECRET_ACCESS_KEY_1234\n')
        (config/'sub').mkdir()
        (config/'sub/rc16.cfg').write_text('[auto_xy_ratos_handoff_rc16]\nenabled: true\n')
        legacy=self.home/'auto_xy/klipper/auto_xy_local_recovery.py'
        legacy.parent.mkdir(parents=True)
        legacy.write_text("def on_ready(self):\n self.gcode.register_command('PROCESS_GCODE_FILE', self.cmd)\nSECRET_HIDDEN_SOURCE_VALUE\n")
        current=self.home/'auto_xy/klipper/auto_xy_gate.py'
        current.write_text('class Gate: pass\n')
        extras=self.home/'klipper/klippy/extras'
        extras.mkdir(parents=True)
        (extras/'auto_xy_local_recovery.py').symlink_to(legacy)
        (extras/'weird_plugin.py').write_text('PROCESS_GCODE_FILE\n')
        (extras/'ratos.py').write_text('PROCESS_GCODE_FILE\n')
        secret=self.home/'auto_xy/print_gate_runs/2026_PRIVATE_CUSTOMER/secret.jpg'
        secret.parent.mkdir(parents=True)
        secret.write_bytes(b'SECRET_IMAGE_BYTES')
        fake_ref=self.home/'auto_xy/camera_commission.json'
        fake_ref.write_text('{"secret": "HIDDEN_CAMERA_PROFILE"}')
        manifest=self.repo/'docs/RC16_SOURCE_MANIFEST.json'
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({'hash_algorithm':'git-blob-sha1',
            'source_files':{'src/auto_xy_gate_rc16.py':audit.git_blob(current.read_bytes())}}))

    def test_include_traversal_and_legacy_detection(self):
        cfg=audit.config_includes(self.home)
        self.assertEqual(sorted(cfg['active_files']), sorted([
            'printer.cfg','machine.cfg','auto_xy_local_recovery.cfg','sub/rc16.cfg']))
        self.assertTrue(cfg['active_legacy_trial04'])
        self.assertNotIn('secrets.cfg',cfg['active_files'])

    def test_audit_privacy_does_not_read_image_config_values_source_lines(self):
        observed=audit.collect(self.home,self.repo,runtime={
            'connected':True,'rc16_handoff':{'armed':True},
            'loaded_auto_xy_sections':['auto_xy_local_recovery','auto_xy_ratos_handoff_rc16']})
        payload=json.dumps(observed)
        for forbidden in ('PRIVATE_PASSWORD','SECRET_ACCESS_KEY_1234','SECRET_IMAGE_BYTES',
                          '2026_PRIVATE_CUSTOMER','HIDDEN_CAMERA_PROFILE','SECRET_HIDDEN_SOURCE_VALUE'):
            self.assertNotIn(forbidden,payload)
        self.assertIn('ACTIVE_LEGACY_TRIAL04_REVIEW_REQUIRED',observed['review_indicators'])
        self.assertIn('COMPETING_TRIAL04_AND_RC16_PRINT_HANDOFFS',observed['review_indicators'])
        self.assertIn('OTHER_PRINT_PROCESSOR_HOOK_CANDIDATES',observed['review_indicators'])
        self.assertEqual(observed['installed_source_vs_repo'][0]['result'],'MATCH_CURRENT_SOURCE')
        self.assertEqual(observed['other_processor_hook_paths'],['klipper/klippy/extras/weird_plugin.py'])

    def test_storage_metadata_does_not_export_private_file_names_or_contents(self):
        report=audit.collect(self.home,self.repo,runtime={'connected':False})
        entries=report['storage_footprint']['directories']
        self.assertTrue(entries)
        self.assertTrue(all(x['bytes']>=0 and x['file_count']>=0 for x in entries))
        snapshot=json.dumps(entries)
        self.assertNotIn('2026_PRIVATE_CUSTOMER',snapshot)
        self.assertNotIn('secret.jpg',snapshot)
        self.assertNotIn('SECRET_IMAGE_BYTES',snapshot)

    def test_zip_is_private_and_does_not_overwrite_or_mutate_code(self):
        before=(self.home/'printer_data/config/printer.cfg').read_bytes()
        report=audit.collect(self.home,self.repo,runtime={'connected':False})
        path=self.home/'report.zip'
        audit.write_zip(report,path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()),{'inventory.json','SUMMARY.txt'})
            self.assertIn('READ_ONLY_AUTOXY_CODE_AND_HOOK_AUDIT',archive.read('inventory.json').decode())
        with self.assertRaises(FileExistsError):
            audit.write_zip(report,path)
        self.assertEqual((self.home/'printer_data/config/printer.cfg').read_bytes(),before)

    def test_runtime_redacts_offsets_and_printer_name(self):
        s=audit.filtered_runtime({'status':{
            'print_stats':{'state':'standby','filename':'PRIVATE_CUSTOMER.gcode'},
            'virtual_sdcard':{'is_active':False,'file_path':None},
            'gcode_macro _VAOC':{'is_started':False},
            'auto_xy_ratos_handoff_rc16':{'armed':True,'busy':False},
            'save_variables':{'variables':{'idex_xoffset':-0.3,'api_token':'Secret',
                                         'auto_xy_v2_enabled':1,'auto_xy_v2_fault':0}},
            'configfile':{'settings':{'auto_xy_gate':{'secret':'PRIVATE_PASSWORD'},
                                      'extruder':{'printer_password':'Hello'}}}}})
        serialized=json.dumps(s)
        for forbidden in ('PRIVATE_CUSTOMER','PRIVATE_PASSWORD','idex_xoffset','api_token','Secret'):
            self.assertNotIn(forbidden,serialized)
        self.assertEqual(s['saved_flags']['auto_xy_v2_enabled'],True)
        self.assertEqual(s['saved_flags']['auto_xy_v2_fault'],False)

    def test_already_retired_legacy_is_a_disk_leftover_not_active(self):
        config=self.home/'printer_data/config/printer.cfg'
        config.write_text('# [include auto_xy_local_recovery.cfg]\n[include sub/*.cfg]\n')
        report=audit.collect(self.home,self.repo,runtime={'connected':False})
        self.assertFalse(report['legacy_trial04']['active_in_configuration'])
        self.assertNotIn('ACTIVE_LEGACY_TRIAL04_REVIEW_REQUIRED',report['review_indicators'])

    def test_symlink_outside_active_config_tree_is_not_followed(self):
        config=self.home/'printer_data/config'
        (config/'sub/rc16.cfg').unlink()
        (config/'sub/rc16.cfg').symlink_to(config/'secrets.cfg')
        cfg=audit.config_includes(self.home)
        self.assertNotIn('sub/rc16.cfg',cfg['active_files'])
        self.assertTrue(cfg['warnings'])


if __name__=='__main__':
    unittest.main()