#!/usr/bin/env python3
"""Regression tests: Klipper SAVE_CONFIG must survive install, rollback and repair."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import install_stage6
from printer_cfg_editor import (
    PrinterCfgError, install_include, remove_include,
    repair_legacy_tail, NEW_INCLUDE, OLD_INCLUDE,
)

BASE = ('[include RatOS.cfg]\n\n'
        '[printer]\nkinematics: corexy\n\n'
        '[gcode_macro USER_SAMPLE]\ngcode:\n  G28\n\n')
HEADER = ('\n#*# <---------------------- SAVE_CONFIG ---------------------->\n'
          '#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n'
          '#*#\n')
AUTOSAVE = (HEADER + '#*# [extruder]\n#*# control = pid\n'
            '#*# pid_kp = 22.0\n#*#\n#*# [probe]\n#*# z_offset = 0.815\n')


def verify_saved_block(text):
    # Klipper's ConfigAutoSave uses this exact header including two leading \n.
    official = '\n' + HEADER
    if '#*#' in text:
        assert official in text, 'Klipper would not recognise SAVE_CONFIG header'
        saved = text.split(official, 1)[1].strip()
        assert saved and all(line.startswith('#*#') for line in saved.splitlines()), (
            'Klipper would consider data after SAVE_CONFIG modified/corrupt')


class CfgEditorTests(unittest.TestCase):
    def test_new_no_saved_settings(self):
        fixed = install_include(BASE)
        self.assertEqual(fixed.count(NEW_INCLUDE), 1)
        self.assertEqual(install_include(fixed), fixed)

    def test_valid_autosave_untouched_and_before_header(self):
        source = BASE + AUTOSAVE
        fixed = install_include(source)
        self.assertTrue(fixed.endswith(AUTOSAVE))
        self.assertLess(fixed.index(NEW_INCLUDE), fixed.index('SAVE_CONFIG'))
        verify_saved_block(fixed)
        self.assertEqual(install_include(fixed), fixed)

    def test_replace_old_include_in_place(self):
        source = BASE.replace('[printer]', OLD_INCLUDE + '\n\n[printer]') + AUTOSAVE
        fixed = install_include(source)
        self.assertNotIn(OLD_INCLUDE, fixed)
        self.assertEqual(fixed.count(NEW_INCLUDE), 1)
        self.assertTrue(fixed.endswith(AUTOSAVE))
        verify_saved_block(fixed)

    def test_restore_legacy_include_prior_to_saved_block(self):
        fixed = install_include(BASE + AUTOSAVE)
        disabled = remove_include(fixed, restore_legacy=True)
        self.assertTrue(disabled.endswith(AUTOSAVE))
        self.assertIn(OLD_INCLUDE, disabled.split('SAVE_CONFIG')[0])
        self.assertNotIn(NEW_INCLUDE, disabled)
        verify_saved_block(disabled)

    def test_disable_without_legacy(self):
        fixed = install_include(BASE + AUTOSAVE)
        disabled = remove_include(fixed)
        self.assertTrue(disabled.endswith(AUTOSAVE))
        self.assertNotIn(NEW_INCLUDE, disabled)
        verify_saved_block(disabled)

    def test_crlf_saved_block_unchanged(self):
        source = (BASE + AUTOSAVE).replace('\n', '\r\n')
        fixed = install_include(source)
        self.assertTrue(fixed.endswith(AUTOSAVE.replace('\n','\r\n')))
        self.assertEqual(fixed.count(NEW_INCLUDE), 1)
        self.assertEqual(fixed, install_include(fixed))

    def test_refuse_existing_corruption(self):
        corrupt = BASE + AUTOSAVE + '[include auto_xy_print.cfg]\n'
        with self.assertRaisesRegex(PrinterCfgError, 'Unexpected content'):
            install_include(corrupt)
        with self.assertRaisesRegex(PrinterCfgError, 'Unexpected content'):
            remove_include(corrupt)

    def test_repair_only_known_include(self):
        corrupt = BASE + AUTOSAVE + '\n' + NEW_INCLUDE + '\n'
        repaired = repair_legacy_tail(corrupt)
        self.assertLess(repaired.index(NEW_INCLUDE), repaired.index('SAVE_CONFIG'))
        self.assertIn('#*# z_offset = 0.815', repaired)
        verify_saved_block(repaired)
        self.assertEqual(repair_legacy_tail(repaired), repaired)

    def test_repair_existing_header_and_correct_include(self):
        source = install_include(BASE + AUTOSAVE)
        corrupt = source + NEW_INCLUDE + '\n'
        repaired = repair_legacy_tail(corrupt)
        self.assertEqual(repaired.count(NEW_INCLUDE), 1)
        verify_saved_block(repaired)

    def test_unsafe_tail_refuses_repair(self):
        source = BASE + AUTOSAVE + '\n' + NEW_INCLUDE + '\n[gcode_macro SOMETHING]\n'
        with self.assertRaisesRegex(PrinterCfgError,'Unexpected content'):
            repair_legacy_tail(source)

    def test_missing_saved_header_refuses(self):
        with self.assertRaisesRegex(PrinterCfgError,'without a recognisable header'):
            install_include(BASE + '#*# [heater_bed]\n#*# control = pid\n')

    def test_duplicate_saved_headers_refuse(self):
        with self.assertRaisesRegex(PrinterCfgError,'Multiple SAVE_CONFIG'):
            install_include(BASE + AUTOSAVE + AUTOSAVE)

    def test_duplicate_includes_refuse(self):
        with self.assertRaisesRegex(PrinterCfgError,'Duplicate Auto XY includes'):
            install_include(BASE + NEW_INCLUDE + '\n' + OLD_INCLUDE + '\n' + AUTOSAVE)


class InstallerIntegrationTests(unittest.TestCase):
    def test_installer_preserves_realistic_autosave_with_backup(self):
        """Exercise actual install_stage6.py --install with a mock printer filesystem."""
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            cfg=home/'printer_data/config'
            cfg.mkdir(parents=True)
            k=home/'klipper/klippy/extras/auto_xy_gate.py'
            k.parent.mkdir(parents=True)
            k.write_text('# existing previous extension\n')
            original=BASE + AUTOSAVE
            (cfg/'printer.cfg').write_text(original)
            (cfg/'auto_xy_control.cfg').write_text('# previous\n')
            dest=home/'auto_xy/stage6'
            stdout=io.StringIO()
            with (mock.patch.object(install_stage6,'HOME',home),
                  mock.patch.object(install_stage6,'CFG',cfg),
                  mock.patch.object(install_stage6,'DEST',dest),
                  mock.patch.object(install_stage6,'KLIPPER',k),
                  mock.patch.object(install_stage6,'verify_local_ratos_hook_order',lambda: None),
                  mock.patch.object(install_stage6,'live_status',lambda: {
                      'auto_xy_enabled':0,'idex_xoffset':0,'idex_yoffset':0,'idex_zoffset':0}),
                  mock.patch.object(sys,'argv',['install_stage6.py','--install']),
                  contextlib.redirect_stdout(stdout)):
                install_stage6.run()
            result=(cfg/'printer.cfg').read_text()
            self.assertTrue(result.endswith(AUTOSAVE))
            self.assertLess(result.index(NEW_INCLUDE),result.index('SAVE_CONFIG'))
            self.assertEqual(result.count(NEW_INCLUDE),1)
            verify_saved_block(result)
            self.assertTrue((dest/'printer_cfg_editor.py').is_file())
            self.assertTrue((dest/'repair_save_config.py').is_file())
            backup=list((home/'auto_xy/stage6_backups').glob('*/printer.cfg'))
            self.assertEqual(len(backup),1)
            self.assertEqual(backup[0].read_text(), original)


if __name__=='__main__':
    unittest.main(verbosity=2)
