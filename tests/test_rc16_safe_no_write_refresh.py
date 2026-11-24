"""Transactional existing-RC16 safe fresh retry refresh contract."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'rc16_safe_no_write_refresh',
    ROOT/'scripts/rc16_safe_no_write_refresh.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class SafeNoWriteUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home/'auto_xy/rollbacks').mkdir(parents=True)
        self.records = []
        for target, source, old_pins, new_blob in mod.FILES:
            data = (ROOT/source).read_bytes()
            self.assertEqual(mod.blob(data), new_blob)
            installed = self.home/target
            installed.parent.mkdir(parents=True, exist_ok=True)
            old = ('reviewed-old-fixture:'+target).encode()
            installed.write_bytes(old)
            self.records.append((target,source,(mod.blob(old),),new_blob))

    def test_refuses_active_or_selected_state(self):
        data = {
            'print_stats': {'state':'cancelled'},
            'virtual_sdcard': {'is_active':False, 'file_path':None},
            'gcode_macro _VAOC': {'is_started':False},
            'auto_xy_ratos_handoff_rc16': {'armed':True,'busy':False},
            'save_variables': {'variables': {'auto_xy_v2_enabled':1}}
        }
        mod.require_idle(data)
        import copy
        for field, change in [
            ('active',lambda d:d['virtual_sdcard'].update(is_active=True)),
            ('selected',lambda d:d['virtual_sdcard'].update(file_path='held.gcode')),
            ('paused',lambda d:d['print_stats'].update(state='paused')),
            ('vaoc',lambda d:d['gcode_macro _VAOC'].update(is_started=True)),
            ('fault',lambda d:d['save_variables']['variables'].update(auto_xy_v2_fault=1)),
            ('interrupted',lambda d:d['save_variables']['variables'].update(auto_xy_v2_in_progress=1))
        ]:
            trial=copy.deepcopy(data)
            change(trial)
            with self.subTest(field=field), self.assertRaises(mod.Refuse):
                mod.require_idle(trial)

    def test_installs_exact_sources_and_creates_recoverable_snapshot(self):
        with patch.object(mod, 'FILES', tuple(self.records)):
            result, snapshot = mod.install(ROOT,self.home,live=False)
        self.assertEqual(result,'updated')
        self.assertTrue((snapshot/'receipt.json').is_file())
        for i,(target,src,_,n) in enumerate(self.records):
            self.assertEqual(mod.blob((self.home/target).read_bytes()),n)
            self.assertTrue((snapshot/'originals'/('original_%d'%i)).exists())

    def test_unknown_installed_binary_is_not_replaced(self):
        with patch.object(mod,'FILES',tuple(self.records)):
            installed=self.home/self.records[0][0]
            installed.write_bytes(b'unrecognised')
            with self.assertRaises(mod.Refuse):
                mod.install(ROOT,self.home,live=False)
            self.assertEqual(installed.read_bytes(),b'unrecognised')

    def test_partial_install_failure_rolls_back(self):
        before={target:(self.home/target).read_bytes() for target,*_ in self.records}
        real=mod.atomic_replace
        n=[0]
        def fail_second(path,data,mode):
            n[0]+=1
            if n[0]==2:
                raise OSError('injected write failure')
            return real(path,data,mode)
        with patch.object(mod,'FILES',tuple(self.records)):
            with patch.object(mod,'atomic_replace',side_effect=fail_second):
                with self.assertRaises(mod.Refuse):
                    mod.install(ROOT,self.home,live=False)
        for target,old in before.items():
            self.assertEqual((self.home/target).read_bytes(),old)


if __name__=='__main__':
    unittest.main()
