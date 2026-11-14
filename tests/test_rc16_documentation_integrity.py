"""Current RC16 documentation, code-inventory and archived RC15 integrity checks."""
import hashlib
import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
CURRENT = [
    ROOT / 'README.md',
    ROOT / 'docs/RC16_START_HERE.md',
    ROOT / 'docs/RC16_RELEASE_STATUS.md',
    ROOT / 'docs/RC16_DIAGNOSTICS.md',
    ROOT / 'docs/HISTORICAL_DOCUMENTS.md',
    ROOT / 'docs/RC16_RECOMMISSION_MAINTENANCE.md',
    ROOT / 'docs/RC16_SUPERVISED_QUALIFICATION.md',
]
HISTORY = [
    'RC16_DEV1_QUALIFICATION.md',
    'RC16_RESOURCE_AWARE_INTEGRATION_PLAN.md',
    'RC16_VISION_DEPENDENCY_TRIAL.md',
    'RC16_TRIAL04_SOURCE_AUDIT.md',
    'RC16_IMPLEMENTATION_AUDIT.md',
    'RC16_FIRST_COMPLETE_TRIAL.md',
    'RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md',
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(
        b'blob ' + str(len(data)).encode('ascii') + b'\0' + data
    ).hexdigest()


class RC16DocumentationIntegrityTests(unittest.TestCase):

    def test_main_is_rc16_and_directs_to_current_start_and_status(self):
        text = (ROOT/'README.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# AutoXY RC16'))
        self.assertIn('RC16_START_HERE.md', text)
        self.assertIn('RC16_RELEASE_STATUS.md', text)
        self.assertIn('not yet qualified for unattended', text)

    def test_archived_rc15_package_is_bitwise_preserved(self):
        archived = json.loads((ROOT/'docs/legacy/RC15_MANIFEST.json').read_text())
        current = json.loads((ROOT/'MANIFEST.json').read_text())
        self.assertEqual(archived['version'], '0.5.0-rc15-dev6')
        self.assertEqual(current['version'], '0.5.0-rc15-dev6')
        original_readme = (ROOT/'docs/legacy/RC15_README.md').read_bytes()
        self.assertEqual(sha256(original_readme), archived['sha256']['README.md'])
        self.assertEqual(sha256((ROOT/'README.md').read_bytes()),
                         current['sha256']['README.md'])
        # Only the main-branch landing page's checksum differs; all
        # original source and other historical documentation pins remain.
        for path, value in archived['sha256'].items():
            if path != 'README.md':
                self.assertEqual(current['sha256'][path], value, path)
        self.assertEqual(set(current['sha256']), set(archived['sha256']))

    def test_rc16_source_inventory_matches_repository_files(self):
        manifest = json.loads((ROOT/'docs/RC16_SOURCE_MANIFEST.json').read_text())
        self.assertEqual(manifest['hash_algorithm'], 'git-blob-sha1')
        self.assertEqual(manifest['controller_version'], '0.5.0-rc16-dev1')
        self.assertGreaterEqual(len(manifest['source_files']), 30)
        for relative, expected in manifest['source_files'].items():
            with self.subTest(relative=relative):
                file = ROOT/relative
                self.assertTrue(file.is_file(), relative)
                self.assertEqual(git_blob(file.read_bytes()), expected, relative)

    def test_current_markdown_links_resolve_locally(self):
        links = re.compile(r'(?<!!)\[[^\]]+\]\(([^)]+)\)')
        for file in CURRENT:
            with self.subTest(file=file.name):
                for href in links.findall(file.read_text(encoding='utf-8')):
                    href = href.strip().split(' ', 1)[0]
                    if href.startswith(('https://', 'http://', '#', 'mailto:')):
                        continue
                    relative = unquote(href.split('#', 1)[0])
                    if not relative:
                        continue
                    target = file.parent/relative
                    self.assertTrue(target.exists(), '%s: broken link %s' % (file, href))

    def test_historical_engineering_documents_are_explicitly_labelled(self):
        for name in HISTORY:
            with self.subTest(name=name):
                text = (ROOT/'docs'/name).read_text(encoding='utf-8')
                self.assertIn('**HISTORICAL ENGINEERING RECORD', text[:650])
                self.assertIn('RC16_RELEASE_STATUS.md', text[:650])


if __name__ == '__main__':
    unittest.main()
