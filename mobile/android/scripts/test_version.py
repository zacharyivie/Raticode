"""Tests for scripts/version.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""
import tempfile
import unittest
from pathlib import Path

import version

SAMPLE = '# comment kept\nversionName=0.9.0\nversionCode=14\n'


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.file = self.root / 'version.properties'
        self.file.write_text(SAMPLE, encoding='utf-8')

    def tearDown(self):
        self.dir.cleanup()

    def test_repository_version_is_valid(self):
        name, code = version.read()
        self.assertGreaterEqual(code, 14)
        version.parse_name(name)

    def test_gradle_reads_the_version_file(self):
        gradle = (version.ROOT / 'app/build.gradle.kts').read_text(encoding='utf-8')
        self.assertIn('versionCode = appVersionCode', gradle)
        self.assertIn('versionName = appVersionName', gradle)
        self.assertNotRegex(gradle, r'version(Code|Name)\s*=\s*["\d]')

    def test_bump_parts_add_one_to_the_code_and_keep_comments(self):
        for part, expected in (('patch', '0.9.1'), ('minor', '0.10.0'), ('major', '1.0.0')):
            self.file.write_text(SAMPLE, encoding='utf-8')
            self.assertEqual(version.bump(part, self.root), (('0.9.0', 14), (expected, 15)))
            self.assertEqual(self.file.read_text(encoding='utf-8'), f'# comment kept\nversionName={expected}\nversionCode=15\n')

    def test_prerelease_then_release(self):
        version.bump('1.0.0-rc.1', self.root)
        version.bump('1.0.0-rc.2', self.root)
        self.assertEqual(version.bump('1.0.0', self.root)[1], ('1.0.0', 17))

    def test_refuses_versions_that_do_not_move_forward(self):
        for target in ('0.9.0', '0.8.9', '0.9.0-rc.1', '1.0', 'v1.0.0', '01.0.0', '1.0.0+build'):
            with self.assertRaises(version.VersionError, msg=target):
                version.bump(target, self.root)
        with self.assertRaises(version.VersionError):
            version.bump('1.0.0', self.root, code=14)
        self.assertEqual(self.file.read_text(encoding='utf-8'), SAMPLE)

    def test_explicit_higher_code(self):
        self.assertEqual(version.bump('patch', self.root, code=100)[1], ('0.9.1', 100))

    def test_semver_precedence(self):
        ordered = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta',
                   '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '1.10.0', '2.0.0']
        self.assertEqual(sorted(reversed(ordered), key=version.precedence), ordered)

    def test_check_matches_release_tag(self):
        self.assertEqual(version.check(self.root, 'refs/tags/mobile-v0.9.0'), ('0.9.0', 14))
        self.assertEqual(version.check(self.root, 'mobile-v0.9.0'), ('0.9.0', 14))
        for tag in ('v0.9.0', 'mobile-v0.9.1', 'mobile-v0.9.0-rc.1'):
            with self.assertRaises(version.VersionError, msg=tag):
                version.check(self.root, tag)

    def test_rejects_malformed_files(self):
        for text in ('versionName=0.9.0\n', 'versionName=0.9.0\nversionCode=0\n', 'versionName=0.9\nversionCode=3\n',
                     'versionName=0.9.0\nversionCode=x\n', 'versionName=0.9.0\nversionCode=2100000001\n'):
            self.file.write_text(text, encoding='utf-8')
            with self.assertRaises(version.VersionError, msg=text):
                version.read(self.root)


if __name__ == '__main__':
    unittest.main()
