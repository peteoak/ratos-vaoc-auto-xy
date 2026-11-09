"""RC16 fresh installation simulator: two-phase install and complete rollback."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('rc16_fresh', SCRIPTS / 'rc16_fresh_install.py')
fresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fresh)


class FreshRC16Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'pi'
        for rel in ('auto_xy/rollbacks', 'printer_data/config', 'klipper/klippy/extras'):
            (self.home / rel).mkdir(parents=True)
        self.printer = self.home / 'printer_data/config/printer.cfg'
        self.original = '[mcu]\nserial: /dev/ttyS0\n\n#*# SAVE_CONFIG\n#*# untouched\n'
        self.printer.write_text(self.original)

    def test_fresh_preflight_is_read_only(self):
        plan, offsets = fresh.preflight(self.home, ROOT, live=False)
        self.assertIsNone(offsets)
        self.assertIn('auto_xy/klipper/auto_xy_gate.py', plan)
        self.assertEqual(self.printer.read_text(), self.original)
        self.assertFalse((self.home / 'auto_xy/klipper/auto_xy_gate.py').exists())

    def test_fresh_staging_then_complete_rollback(self):
        receipt = fresh.fresh_install(self.home, ROOT, live=False)
        self.assertTrue((receipt / fresh.RECEIPT).is_file())
        self.assertIn(fresh.rc16.INCLUDE, self.printer.read_text())
        self.assertTrue((self.home / 'klipper/klippy/extras/auto_xy_gate.py').is_symlink())
        self.assertTrue((self.home / 'klipper/klippy/extras/auto_xy_ratos_handoff_rc16.py').is_symlink())
        self.assertIn('enabled: False',
            (self.home / 'printer_data/config/auto_xy_rc16.cfg').read_text())
        self.assertIn('0.5.0-rc16-dev1',
            (self.home / 'auto_xy/klipper/auto_xy_gate.py').read_text())
        self.assertTrue((self.home / 'auto_xy/workers/rc16_prepare_worker.py').exists())
        fresh.fresh_rollback(self.home, receipt, live=False)
        self.assertEqual(self.printer.read_text(), self.original)
        self.assertFalse((self.home / 'auto_xy/klipper/auto_xy_gate.py').exists())
        self.assertFalse((self.home / 'printer_data/config/auto_xy.cfg').exists())
        self.assertFalse((self.home / 'printer_data/config/auto_xy_rc16.cfg').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_gate.py').exists())
        self.assertFalse((self.home / 'klipper/klippy/extras/auto_xy_ratos_handoff_rc16.py').exists())

    def test_fresh_refuses_existing_legacy_source(self):
        (self.home / 'printer_data/config/auto_xy.cfg').write_text('existing')
        with self.assertRaises(Exception):
            fresh.preflight(self.home, ROOT, live=False)

    def test_fresh_rollback_refuses_if_managed_printer_changed(self):
        receipt = fresh.fresh_install(self.home, ROOT, live=False)
        self.printer.write_text(self.printer.read_text() + '\n# local modification\n')
        with self.assertRaises(Exception):
            fresh.fresh_rollback(self.home, receipt, live=False)
        self.assertIn(fresh.rc16.INCLUDE, self.printer.read_text())

    def test_fresh_cli_requires_explicit_confirmation(self):
        result = fresh.main(['--install', '--offline', '--home', str(self.home)])
        self.assertEqual(result, 2)
        self.assertEqual(self.printer.read_text(), self.original)

    def test_fresh_cli_check_does_not_write(self):
        result = fresh.main(['--check', '--offline', '--home', str(self.home)])
        self.assertEqual(result, 0)
        self.assertEqual(self.printer.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
