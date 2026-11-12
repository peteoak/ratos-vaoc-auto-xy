"""Offline RC16 active installer tests (never restart actual Klipper)."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'rc16_active_install_test_module', ROOT / 'scripts/rc16_active_install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
upgrade = installer.upgrade


def ready_ui(status):
    settings = status.get('configfile', {}).get('settings', {})
    settings.setdefault('gcode_macro auto_xy_recommission_nozzles', {})
    settings.setdefault('gcode_macro AUTO_XY_TEMPERATURE', {})
    status.setdefault('auto_xy_recommission_rc16', {'ready': True})
    return status


class ActiveInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'pi'
        for name in ('auto_xy/klipper', 'auto_xy/rollbacks',
                     'printer_data/config', 'klipper/klippy/extras'):
            (self.home / name).mkdir(parents=True)
        self.gate = self.home / upgrade.OLD_GATE
        self.previous_gate = (ROOT / 'src/auto_xy_gate.py').read_bytes()
        self.gate.write_bytes(self.previous_gate)
        vision = self.home / 'auto_xy/vision/auto_xy_vision_worker.py'
        vision.parent.mkdir(parents=True, exist_ok=True)
        vision.write_bytes((ROOT / 'src/auto_xy_vision_worker.py').read_bytes())
        (self.home / 'klipper/klippy/extras/auto_xy_gate.py').symlink_to(self.gate)
        self.settings = self.home / upgrade.GATE_CONFIG
        self.settings.write_text(
            '[auto_xy_gate]\nprotected_print_enabled: True\n'
            'camera_detector_mode: two_stage_roi\n')
        self.printer = self.home / upgrade.PRINTER
        self.printer.write_text(
            '[include auto_xy.cfg]\n#*# SAVE_CONFIG\n#*# do not touch\n')
        self.before = (self.settings.read_bytes(), self.printer.read_bytes())
        original_check = upgrade.check_active_installation
        guard = patch.object(upgrade, 'check_active_installation',
                             side_effect=lambda home, root, live:
                             original_check(home, root, live=False))
        guard.start()
        self.addCleanup(guard.stop)
        self.real_activate = installer.activate_persistent_gate
        self.real_saved_state = installer.saved_state
        self.real_read_migration_state = installer.read_migration_state
        self.real_restore = installer.restore_saved_state
        self.mock_original_state = {
            'flags': {key: 0 for key in installer.MIGRATED_FLAGS},
            'offsets': {'idex_xoffset': -0.1,
                        'idex_yoffset': -0.2,
                        'idex_zoffset': 0.02}}
        self.mock_original_state['flags']['auto_xy_v2_enabled'] = 1
        saved_guard = patch.object(installer, 'read_migration_state',
                                   return_value=self.mock_original_state)
        activation_guard = patch.object(installer, 'activate_persistent_gate')
        restore_guard = patch.object(installer, 'restore_saved_state')
        for item in (saved_guard, activation_guard, restore_guard):
            item.start()
            self.addCleanup(item.stop)

    def assert_restored(self):
        self.assertEqual(self.gate.read_bytes(), self.previous_gate)
        self.assertEqual(self.settings.read_bytes(), self.before[0])
        self.assertEqual(self.printer.read_bytes(), self.before[1])
        self.assertFalse((self.home / upgrade.CONFIG).exists())
        self.assertFalse((self.home / upgrade.LINK).exists())
        self.assertFalse((self.home / upgrade.LINK_RECOMMISSION).exists())
        self.assertFalse((self.home / 'printer_data/config/auto_xy_recommission_rc16_macros.cfg').exists())

    def test_active_install_is_single_transaction(self):
        calls = []
        variant, snapshot = installer.install(
            self.home, ROOT, live=True,
            restart=lambda: calls.append('restart'),
            verify=lambda: calls.append('verify'))
        self.assertEqual(variant, 'rc15-dev6-reference')
        self.assertEqual(calls, ['restart', 'verify'])
        self.assertTrue(snapshot.is_dir())
        self.assertIn('enabled: True', (self.home / upgrade.CONFIG).read_text())
        self.assertIn('protected_print_enabled: True', self.settings.read_text())
        upgrade.rollback(self.home, snapshot, live=False)
        self.assert_restored()

    def test_failed_verify_automatically_restores_prior_install(self):
        calls = []
        def failure():
            calls.append('verify')
            raise installer.InstallationError('simulated no-ready')
        with self.assertRaisesRegex(installer.InstallationError, 'prior installation restored'):
            installer.install(
                self.home, ROOT, live=True,
                restart=lambda: calls.append('restart'),
                verify=failure)
        self.assertEqual(calls, ['restart', 'verify', 'restart'])
        self.assert_restored()

    def test_snapshot_restored_after_failed_restart(self):
        attempts = []
        def restart():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('simulated first restart failure')
        with self.assertRaisesRegex(installer.InstallationError, 'prior installation restored'):
            installer.install(self.home, ROOT, live=True,
                              restart=restart, verify=lambda: None)
        self.assertEqual(len(attempts), 2)
        self.assert_restored()

    def test_runtime_requires_all_three_active_sections(self):
        expected = {
            'auto_xy_gate': {'protected_print_enabled': True},
            'auto_xy_ratos_handoff_rc16': {
                'enabled': True, 'retention_enabled': True},
            'gcode_macro AUTO_XY_RECOMMISSION_NOZZLES': {},
            'gcode_macro AUTO_XY_TEMPERATURE': {}}
        def response(url):
            if url == installer.INFO_URL:
                return {'result': {'state': 'ready'}}
            return {'result': {'status': {
                'configfile': {'settings': expected},
                'auto_xy_ratos_handoff_rc16': {'armed': True, 'sd_api_guarded': True, 'resume_api_guarded': True},
                'auto_xy_recommission_rc16': {'ready': True},
                'save_variables': {'variables': {
                    'auto_xy_v2_enabled': 1,
                    'auto_xy_v2_fault': 0,
                    'auto_xy_v2_in_progress': 0}}}}}
        with patch.object(installer, 'query_json', side_effect=response):
            installer.check_active_runtime(timeout=2, sleep=lambda _: None)
            expected['auto_xy_ratos_handoff_rc16']['enabled'] = False
            with self.assertRaisesRegex(installer.InstallationError,
                                        'handoff is not enabled'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)
            expected['auto_xy_ratos_handoff_rc16']['enabled'] = True
        def unarmed(url):
            data = response(url)
            if url == installer.CONFIG_URL:
                data['result']['status']['auto_xy_ratos_handoff_rc16']['armed'] = False
            return data
        with patch.object(installer, 'query_json', side_effect=unarmed):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'processor is not armed'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)

    def test_runtime_refuses_persistent_off_or_unresolved_fault(self):
        state = {
            'configfile': {'settings': {
                'auto_xy_gate': {'protected_print_enabled': True},
                'auto_xy_ratos_handoff_rc16': {
                    'enabled': True, 'retention_enabled': True}}},
            'auto_xy_ratos_handoff_rc16': {'armed': True, 'sd_api_guarded': True, 'resume_api_guarded': True},
            'save_variables': {'variables': {
                'auto_xy_v2_enabled': 0,
                'auto_xy_v2_fault': 0,
                'auto_xy_v2_in_progress': 0}}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(state)}})
        with patch.object(installer, 'query_json', side_effect=response):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'calibration enable is OFF'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)
            state['save_variables']['variables']['auto_xy_v2_enabled'] = 1
            state['save_variables']['variables']['auto_xy_v2_fault'] = 1
            with self.assertRaisesRegex(installer.InstallationError,
                                        'unresolved safety fault'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)
            state['save_variables']['variables']['auto_xy_v2_fault'] = 0
            installer.check_active_runtime(timeout=2, sleep=lambda _: None)

    def test_runtime_rejects_missing_direct_sd_api_guard(self):
        state = {'configfile': {'settings': {
                     'auto_xy_gate': {'protected_print_enabled': True},
                     'auto_xy_ratos_handoff_rc16': {
                         'enabled': True, 'retention_enabled': True}}},
                 'auto_xy_ratos_handoff_rc16': {
                     'armed': True, 'sd_api_guarded': False},
                 'save_variables': {'variables': {'auto_xy_v2_enabled': 1}}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(state)}})
        with patch.object(installer, 'query_json', side_effect=response):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'API guard is not active'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)

    def test_runtime_refuses_missing_resume_api_guard(self):
        state = {'configfile': {'settings': {
                     'auto_xy_gate': {'protected_print_enabled': True},
                     'auto_xy_ratos_handoff_rc16': {
                         'enabled': True, 'retention_enabled': True}}},
                 'auto_xy_ratos_handoff_rc16': {
                     'armed': True, 'sd_api_guarded': True,
                     'resume_api_guarded': False},
                 'save_variables': {'variables': {'auto_xy_v2_enabled': 1}}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(state)}})
        with patch.object(installer, 'query_json', side_effect=response):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'resume guard is not active'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)

    def test_activation_requires_armed_processor_before_auto_on(self):
        status = {
            'configfile': {'settings': {
                'auto_xy_gate': {'protected_print_enabled': True},
                'auto_xy_ratos_handoff_rc16': {'enabled': True}}},
            'auto_xy_ratos_handoff_rc16': {'armed': True, 'sd_api_guarded': True, 'resume_api_guarded': True}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(status)}})
        with patch.object(installer, 'query_json', side_effect=response), \
             patch.object(installer, 'saved_state', return_value=(False, False)), \
             patch.object(installer, 'post_gcode') as send, \
             patch.object(installer, 'activate_persistent_gate', wraps=self.real_activate):
            installer.activate_persistent_gate()
            send.assert_called_once_with('AUTO_XY_ON')
            status['auto_xy_ratos_handoff_rc16']['armed'] = False
            with self.assertRaisesRegex(installer.InstallationError, 'not.*armed'):
                installer.activate_persistent_gate()
            self.assertEqual(send.call_count, 1)

    def test_activation_clears_saved_measure_only_even_if_already_enabled(self):
        status = {
            'configfile': {'settings': {
                'auto_xy_gate': {'protected_print_enabled': True},
                'auto_xy_ratos_handoff_rc16': {'enabled': True}}},
            'auto_xy_ratos_handoff_rc16': {
                'armed': True, 'sd_api_guarded': True,
                'resume_api_guarded': True}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(status)}})
        with patch.object(installer, 'query_json', side_effect=response), \
             patch.object(installer, 'saved_state', return_value=(True, True)), \
             patch.object(installer, 'post_gcode') as send:
            self.real_activate()
            send.assert_called_once_with('AUTO_XY_ON')

    def test_runtime_rejects_measure_only_when_correction_required(self):
        status = {
            'configfile': {'settings': {
                'auto_xy_gate': {'protected_print_enabled': True},
                'auto_xy_ratos_handoff_rc16': {
                    'enabled': True, 'retention_enabled': True}}},
            'auto_xy_ratos_handoff_rc16': {
                'armed': True, 'sd_api_guarded': True,
                'resume_api_guarded': True},
            'save_variables': {'variables': {
                'auto_xy_v2_enabled': 1, 'auto_xy_v2_measure_only': 1,
                'auto_xy_v2_fault': 0, 'auto_xy_v2_in_progress': 0}}}
        def response(url):
            return ({'result': {'state': 'ready'}} if url == installer.INFO_URL
                    else {'result': {'status': ready_ui(status)}})
        with patch.object(installer, 'query_json', side_effect=response):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'measure-only mode remains enabled'):
                installer.check_active_runtime(timeout=2, sleep=lambda _: None)

    def test_rollback_restores_original_disabled_flags(self):
        old = {'flags': dict(self.mock_original_state['flags']),
               'offsets': dict(self.mock_original_state['offsets'])}
        old['flags']['auto_xy_v2_enabled'] = 0
        old['flags']['auto_xy_v2_measure_only'] = 1
        def restore(original):
            self.assertEqual(original, old)
        with patch.object(installer, 'read_migration_state', return_value=old), \
             patch.object(installer, 'restore_saved_state', side_effect=restore) as restored:
            with self.assertRaisesRegex(installer.InstallationError,
                                        'prior installation restored'):
                installer.install(self.home, ROOT, live=True,
                                  restart=lambda: None,
                                  verify=lambda: (_ for _ in ()).throw(
                                      installer.InstallationError('injected')))
            restored.assert_called_once_with(old)
        self.assert_restored()

    def test_preinstall_saved_state_does_not_query_uninstalled_rc16_handoff(self):
        observed = []
        def response(url):
            observed.append(url)
            self.assertEqual(url, installer.SAVED_URL)
            self.assertNotIn('auto_xy_ratos_handoff_rc16', url)
            return {'result': {'status': {'save_variables': {'variables': {
                'auto_xy_v2_enabled': 1,
                'auto_xy_v2_measure_only': 0,
                'auto_xy_v2_fault': 0,
                'auto_xy_v2_in_progress': 0,
                'auto_xy_v2_retry_available': 0,
                'idex_xoffset': -0.1, 'idex_yoffset': -0.2,
                'idex_zoffset': 0.02}}}}}
        with patch.object(installer, 'query_json', side_effect=response), \
             patch.object(installer, 'read_migration_state',
                          wraps=self.real_read_migration_state):
            self.assertEqual(self.real_saved_state(), (True, False))
        self.assertEqual(observed, [installer.SAVED_URL])

    def test_saved_state_refuses_unresolved_faults(self):
        status = {'save_variables': {'variables': {
            'auto_xy_v2_enabled': 0, 'auto_xy_v2_fault': 1,
            'auto_xy_v2_measure_only': 0,
            'idex_xoffset': -0.1, 'idex_yoffset': -0.2,
            'idex_zoffset': 0.02}}}
        with patch.object(installer, 'query_json',
                          return_value={'result': {'status': status}}), \
             patch.object(installer, 'read_migration_state',
                          wraps=self.real_read_migration_state), \
             patch.object(installer, 'saved_state', wraps=self.real_saved_state):
            with self.assertRaisesRegex(installer.InstallationError,
                                        'unresolved state'):
                installer.saved_state()

    def test_installer_gcode_allowlist_rejects_arbitrary_commands(self):
        with patch.object(installer, 'urlopen') as send:
            with self.assertRaisesRegex(installer.InstallationError, 'Disallowed'):
                installer.post_gcode('G28')
            with self.assertRaisesRegex(installer.InstallationError, 'Disallowed'):
                installer.post_gcode('SAVE_VARIABLE VARIABLE=idex_xoffset VALUE=999')
            send.assert_not_called()

    def test_restore_saved_state_reverts_enabled_and_measure_only(self):
        with patch.object(installer, 'saved_state', return_value=(True, False)), \
             patch.object(installer, 'post_gcode') as send, \
             patch.object(installer, 'restore_saved_state',
                          wraps=self.real_restore):
            installer.restore_saved_state((False, True))
            self.assertEqual(
                [call.args[0] for call in send.call_args_list],
                ['SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0',
                 'SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=1'])


if __name__ == '__main__':
    unittest.main()
