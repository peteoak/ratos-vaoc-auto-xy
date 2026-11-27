"""Offline regression: retire exact legacy Trial04 print interceptor only.

All filesystem fixtures are synthetic. No printer, Moonraker or sudo access.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rc16_retire_trial04_recovery',
    ROOT/'scripts/rc16_retire_trial04_recovery.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Trial04RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.cfg = self.home/mod.PRINTER_CFG
        self.cfg.parent.mkdir(parents=True)
        self.before = (
            b'# other settings\n'
            b'[include auto_xy_local_recovery.cfg]\n'
            b'[include auto_xy.cfg]\n'
            b'[include auto_xy_rc16.cfg]\n')
        self.cfg.write_bytes(self.before)
        self.legacy_cfg = self.home/mod.LEGACY_CFG
        self.legacy_cfg.write_text(
            '[auto_xy_local_recovery]\nenabled: True\n'
            'registry_directory: /home/pi/auto_xy/trial04_registry\n')
        self.src = self.home/mod.LEGACY_SOURCE
        self.src.parent.mkdir(parents=True)
        self.src.write_bytes(b'fixture legacy Trial04')
        self.ext = self.home/mod.LEGACY_EXTENSION
        self.ext.parent.mkdir(parents=True)
        self.ext.symlink_to(self.src)
        (self.home/'auto_xy/rollbacks').mkdir(parents=True)
        self.known = patch.object(mod, 'LEGACY_SHA256',
                                  mod.sha256(self.src.read_bytes()))
        self.known.start()
        self.addCleanup(self.known.stop)

    def test_one_line_is_changed_and_rest_is_byte_preserved(self):
        cfg, old, updated, change = mod.inspect(self.home)
        self.assertTrue(change)
        self.assertEqual(old, self.before)
        self.assertEqual(
            updated, self.before.replace(
                b'[include auto_xy_local_recovery.cfg]',
                b'# [include auto_xy_local_recovery.cfg]'))

    def test_duplicate_live_includes_refused(self):
        self.cfg.write_bytes(self.before + b'[include auto_xy_local_recovery.cfg]\n')
        with self.assertRaisesRegex(mod.Refuse, 'precisely one'):
            mod.inspect(self.home)

    def test_modified_legacy_binary_refused(self):
        self.src.write_bytes(b'unknown modified extension')
        with self.assertRaisesRegex(mod.Refuse, 'reviewed Trial04'):
            mod.inspect(self.home)

    def test_changed_extension_symlink_refused(self):
        self.ext.unlink()
        self.ext.write_bytes(b'unmanaged module')
        with self.assertRaisesRegex(mod.Refuse, 'expected source symlink'):
            mod.inspect(self.home)

    @staticmethod
    def idle_status(*, legacy=False):
        settings = {'auto_xy_ratos_handoff_rc16': {'enabled': True}}
        if legacy:
            settings['auto_xy_local_recovery'] = {'enabled': True}
        return {
            'print_stats': {'state': 'standby'},
            'virtual_sdcard': {'is_active': False, 'file_path': None,
                               'filename': None},
            'gcode_macro _VAOC': {'is_started': False},
            'auto_xy_ratos_handoff_rc16': {
                'enabled': True, 'armed': True, 'busy': False,
                'sd_api_guarded': True, 'resume_api_guarded': True},
            'save_variables': {'variables': {
                'auto_xy_v2_enabled': 1, 'auto_xy_v2_fault': 0,
                'auto_xy_v2_in_progress': 0}},
            'configfile': {'settings': settings},
        }

    def test_preflight_rejects_held_print_or_fault(self):
        import copy
        for mutation in (
            lambda s: s['print_stats'].update(state='paused'),
            lambda s: s['virtual_sdcard'].update(file_path='selected.gcode'),
            lambda s: s['virtual_sdcard'].update(is_active=True),
            lambda s: s['gcode_macro _VAOC'].update(is_started=True),
            lambda s: s['auto_xy_ratos_handoff_rc16'].update(armed=False),
            lambda s: s['save_variables']['variables'].update(auto_xy_v2_fault=1),
            lambda s: s['save_variables']['variables'].update(auto_xy_v2_in_progress=1),
            lambda s: s['save_variables']['variables'].update(auto_xy_v2_retry_available=1),
        ):
            state = copy.deepcopy(self.idle_status(legacy=True))
            mutation(state)
            with self.assertRaises(mod.Refuse):
                mod.require_idle(state)

    def test_postflight_requires_absence_of_legacy_config_and_rc16_guards(self):
        with self.assertRaisesRegex(mod.Refuse, 'still loaded'):
            mod.require_rc16_post(self.idle_status(legacy=True))
        state = self.idle_status()
        state['auto_xy_ratos_handoff_rc16']['resume_api_guarded'] = False
        with self.assertRaisesRegex(mod.Refuse, 'API guards'):
            mod.require_rc16_post(state)
        mod.require_rc16_post(self.idle_status())

    def test_offline_install_preserves_original_in_verified_backup(self):
        result, backup = mod.install(self.home, live=False)
        self.assertEqual(result, 'trial04-retired')
        self.assertEqual((backup/'printer.cfg.original').read_bytes(), self.before)
        self.assertIn(b'# [include auto_xy_local_recovery.cfg]',
                      self.cfg.read_bytes())
        self.assertTrue(self.src.is_file())
        self.assertTrue(self.ext.is_symlink())

    def test_postflight_error_restores_original_and_attempts_second_restart(self):
        count = []
        def restarting():
            count.append('restart')
        with patch.object(mod, 'assert_ready'), \
             patch.object(mod, 'status',
                          return_value=self.idle_status(legacy=True)):
            with self.assertRaisesRegex(mod.Refuse, 'previous printer.cfg restored'):
                mod.install(self.home, live=True, restart_service=restarting,
                            await_ready=lambda: None,
                            read_status=lambda: self.idle_status(legacy=True))
        self.assertEqual(count, ['restart', 'restart'])
        self.assertEqual(self.cfg.read_bytes(), self.before)

    def test_idempotent_after_retirement(self):
        mod.install(self.home, live=False)
        result, backup = mod.install(self.home, live=False)
        self.assertEqual((result, backup), ('already-retired', None))


if __name__ == '__main__':
    unittest.main()
