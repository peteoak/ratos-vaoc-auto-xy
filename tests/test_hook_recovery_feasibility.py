import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("hookcheck",ROOT/"scripts/audit_hook_recovery.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class HookRecoveryFeasibilityTests(unittest.TestCase):
    def test_continuation_is_in_same_expanded_macro(self):
        source = """M190 S60
_USER_START_PRINT_AFTER_HEATING_BED BED_TEMP=60
_START_PRINT_AFTER_HEATING_BED T=0
_START_PRINT_BED_MESH
"""
        result=module.inspect_start_print(source)
        self.assertEqual(result["hook_line"],2)
        self.assertEqual(result["continuation_line"],3)
        self.assertFalse(result["safe_to_deploy_hook_only_hold_resume"])
        self.assertIn("same START_PRINT", " ".join(result["observations"]))
    def test_embedding_hold_check_still_fails_closed(self):
        source="""_USER_START_PRINT_AFTER_HEATING_BED
AUTOXY_PRINT_HOLD
AUTOXY_PRINT_CHECK
_START_PRINT_AFTER_HEATING_BED
"""
        result=module.inspect_start_print(source)
        self.assertFalse(result["safe_to_deploy_hook_only_hold_resume"])
        self.assertIn("Embedding HOLD and CHECK", " ".join(result["observations"]))
    def test_missing_hook_is_not_approved(self):
        result=module.inspect_start_print("M190 S60\n_START_PRINT_AFTER_HEATING_BED\n")
        self.assertIsNone(result["hook_line"])
        self.assertFalse(result["safe_to_deploy_hook_only_hold_resume"])

if __name__=="__main__":
    unittest.main()
