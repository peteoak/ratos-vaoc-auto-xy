"""Operator-owned ON/OFF invariants and existing-RC16 refresh tests."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / 'scripts/rc16_operator_enable_refresh.py'
spec = importlib.util.spec_from_file_location('rc16_operator_enable_refresh', FILE)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def source(name):
    return (ROOT / name).read_bytes()


class OperatorOwnedEnableTests(unittest.TestCase):

    def test_calibration_controller_never_writes_enabled_preference(self):
        gate = (ROOT / 'src/auto_xy_gate_rc16.py').read_text(encoding='utf-8')
        self.assertNotIn(
            'SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0', gate)
        self.assertNotIn(
            'SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=1', gate)
        self.assertIn(
            "self.run_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_fault VALUE=1')",
            gate)
        self.assertIn(
            "self.report['auto_xy_user_selection_preserved']=True", gate)

    def test_user_macros_own_the_off_transition(self):
        macro = (ROOT / 'config/auto_xy_macros.cfg').read_text()
        a = macro.index('[gcode_macro AUTO_XY_RETRY_DISMISS]')
        b = macro.index('[gcode_macro AUTO_XY_RETRY_PROMPT]', a)
        dismiss = macro[a:b]
        self.assertNotIn('auto_xy_v2_enabled VALUE=0', dismiss)
        self.assertNotIn('auto_xy_v2_enabled VALUE=1', dismiss)
        self.assertIn('auto_xy_v2_retry_available VALUE=0', dismiss)
        self.assertIn('AUTO_XY_OFF', macro)
        self.assertIn('AUTO_XY_TOGGLE', macro)

    def test_error_messages_name_the_real_problem(self):
        coordinator = (ROOT/'src/auto_xy_ratos_handoff_rc16.py').read_text()
        self.assertIn('AutoXY is OFF by operator selection.', coordinator)
        self.assertIn('AutoXY calibration fault/interrupted run is latched.', coordinator)
        self.assertIn('AutoXY calibration is busy.', coordinator)
        self.assertNotIn(
            "raise CoordinatorError('AutoXY cannot authorise independent dual-tool print')",
            coordinator)

    def test_real_pinned_source_matches_update_manifest(self):
        self.assertEqual(len(updater.FILES), 3)
        for target, origin, previous, reviewed in updater.FILES:
            with self.subTest(target=target):
                self.assertEqual(updater.blob(source(origin)), reviewed)
                self.assertNotIn(reviewed, previous)

    def test_idle_preflight_refuses_paused_or_fault(self):
        data = {
            'print_stats': {'state': 'standby'},
            'virtual_sdcard': {'is_active': False, 'file_path': None},
            'gcode_macro _VAOC': {'is_started': False},
            'auto_xy_ratos_handoff_rc16': {'armed': True, 'busy': False},
            'save_variables': {'variables': {'auto_xy_v2_enabled': 1}}
        }
        updater.require_idle(data)
        self.assertEqual(data['save_variables']['variables']['auto_xy_v2_enabled'], 1)
        for section, change in [
            ('paused', lambda d: d['print_stats'].update(state='paused')),
            ('file', lambda d: d['virtual_sdcard'].update(file_path='paused.gcode')),
            ('fault', lambda d: d['save_variables']['variables'].update(auto_xy_v2_fault=1)),
            ('interrupted', lambda d: d['save_variables']['variables'].update(auto_xy_v2_in_progress=1)),
            ('retry', lambda d: d['save_variables']['variables'].update(auto_xy_v2_retry_available=1)),
            ('VAOC', lambda d: d['gcode_macro _VAOC'].update(is_started=True)),
        ]:
            import copy
            clone = copy.deepcopy(data)
            change(clone)
            with self.subTest(section=section):
                with self.assertRaises(updater.Refuse):
                    updater.require_idle(clone)

    def make_fixture(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home/'auto_xy/rollbacks').mkdir(parents=True)
        mock_records = []
        for target, origin, originals, current in updater.FILES:
            path = home/target
            path.parent.mkdir(parents=True, exist_ok=True)
            old = ('old-version:'+target).encode()
            path.write_bytes(old)
            mock_records.append((target, origin, (updater.blob(old),), current))
        return home, tuple(mock_records)

    def test_offline_transaction_installs_and_snapshots_exact_bytes(self):
        home, records = self.make_fixture()
        with patch.object(updater, 'FILES', records):
            result, snapshot = updater.install(ROOT, home, live=False)
        self.assertEqual(result, 'updated')
        self.assertTrue((snapshot/'receipt.json').is_file())
        receipt = json.loads((snapshot/'receipt.json').read_text())
        self.assertEqual(len(receipt), 3)
        for idx, entry in enumerate(receipt):
            path = home/entry['path'].removeprefix(str(home)+'/')
            self.assertEqual(updater.blob(path.read_bytes()), entry['new_blob'])
            self.assertEqual(updater.blob(
                (snapshot/'originals'/('original_%d' % idx)).read_bytes()),
                entry['old_blob'])

    def test_failed_partial_install_restores_every_prior_byte(self):
        home, records = self.make_fixture()
        before = {p: (home/p).read_bytes() for p, _, _, _ in records}
        real = updater.atomic_replace
        count = [0]
        def fail_on_third(path, data, mode):
            count[0] += 1
            if count[0] == 3:
                raise OSError('injected failure')
            return real(path, data, mode)
        with patch.object(updater, 'FILES', records):
            with patch.object(updater, 'atomic_replace', side_effect=fail_on_third):
                with self.assertRaisesRegex(updater.Refuse, 'previous bytes restored'):
                    updater.install(ROOT, home, live=False)
        for relative, original in before.items():
            self.assertEqual((home/relative).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
