"""Regression: active RC15 owner variant may be enabled during safe RC16 upgrade."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import rc16_active_migration as migration

class RC15UpgradePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'pi'
        (self.home/'auto-xy-venv/bin').mkdir(parents=True)
        (self.home/'auto-xy-venv/bin/python').write_text('interpreter')
        self.saved = {'auto_xy_v2_enabled': 1,
                      'auto_xy_v2_measure_only': 0,
                      'auto_xy_v2_fault': 0,
                      'auto_xy_v2_in_progress': 0,
                      'auto_xy_v2_retry_available': 0,
                      'idex_xoffset': -0.127329409629779,
                      'idex_yoffset': -0.24902894095742162,
                      'idex_zoffset': .0225}
        self.status = {
            'save_variables': {'variables': self.saved},
            'gcode_macro _VAOC': {'is_started': False},
            'print_stats': {'state': 'complete'},
            'virtual_sdcard': {'is_active': False},
            'toolhead': {'velocity': 0},
            'extruder': {'target': 0, 'temperature': 25},
            'extruder1': {'target': 0, 'temperature': 25},
            'heater_bed': {'target': 0, 'temperature': 25},
            'configfile': {'settings': {
                'auto_xy_gate': {'worker': str(self.home/'auto_xy/vision/auto_xy_vision_worker.py')},
                'gcode_macro _VAOC': {},
                'gcode_macro RatOS': {}
            }}
        }
        self.reader = lambda: (self.status, {'state': 'ready'})
        self.deps = lambda command: SimpleNamespace(returncode=0,stderr='',stdout='ok')

    def inspect(self):
        return migration.live_rc15_upgrade_safe(self.home,
                                                 status_reader=self.reader,
                                                 python_check=self.deps)

    def test_known_enabled_owner_rc15_accepted_without_saved_variable_changes(self):
        before = dict(self.saved)
        result = self.inspect()
        self.assertTrue(result['existing_enabled'])
        self.assertFalse(result['measure_only'])
        self.assertEqual(before, self.saved)
        self.assertEqual(result['saved_offsets']['idex_zoffset'],.0225)

    def test_existing_measure_only_is_not_treated_as_unresolved_fault(self):
        self.saved['auto_xy_v2_measure_only'] = 1
        result=self.inspect()
        self.assertTrue(result['measure_only'])

    def test_disabled_rc15_is_also_accepted(self):
        self.saved['auto_xy_v2_enabled'] = 0
        self.assertFalse(self.inspect()['existing_enabled'])

    def test_fault_in_progress_and_retry_all_block(self):
        for key in ('auto_xy_v2_fault','auto_xy_v2_in_progress',
                    'auto_xy_v2_retry_available'):
            with self.subTest(key=key):
                self.saved[key]=1
                with self.assertRaisesRegex(migration.MigrationError,'Unresolved'):
                    self.inspect()
                self.saved[key]=0

    def test_legacy_retry_still_blocks(self):
        self.saved['auto_xy_retry_available']=1
        with self.assertRaisesRegex(migration.MigrationError,'Legacy'):
            self.inspect()

    def test_busy_printer_or_native_vaoc_blocks(self):
        self.status['virtual_sdcard']['is_active']=True
        with self.assertRaisesRegex(migration.MigrationError,'Virtual-SD'):
            self.inspect()
        self.status['virtual_sdcard']['is_active']=False
        self.status['gcode_macro _VAOC']['is_started']=True
        with self.assertRaisesRegex(migration.MigrationError,'VAOC'):
            self.inspect()

    def test_heater_and_motion_block(self):
        self.status['extruder']['target']=20
        with self.assertRaisesRegex(migration.MigrationError,'Heater'):
            self.inspect()
        self.status['extruder']['target']=0
        self.status['toolhead']['velocity']=10
        with self.assertRaisesRegex(migration.MigrationError,'moving'):
            self.inspect()

    def test_missing_offset_or_wrong_vision_environment_blocks(self):
        del self.saved['idex_zoffset']
        with self.assertRaisesRegex(migration.MigrationError,'offset'):
            self.inspect()
        self.saved['idex_zoffset']=.0225
        with self.assertRaisesRegex(migration.MigrationError,'OpenCV'):
            migration.live_rc15_upgrade_safe(
                self.home,status_reader=self.reader,
                python_check=lambda _:SimpleNamespace(returncode=1,stderr='missing'))

    def test_frozen_upgrade_planner_uses_the_right_preflight(self):
        text=(ROOT/'scripts/rc16_stage_upgrade.py').read_text()
        fn=text.split('def check_active_installation(',1)[1].split('\ndef write_atomic(',1)[0]
        self.assertIn('migration.live_rc15_upgrade_safe(home)', fn)
        self.assertNotIn("install_autoxy.live_safe(home, 'rollback')",fn)


if __name__=='__main__':
    unittest.main()
