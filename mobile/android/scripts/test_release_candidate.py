"""Tests for scripts/release-candidate.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'

Mirrors the desktop's tests/unit/test_release_candidate.py, with a fake GitHub, fake
Android tools and no network.
"""
import copy
import importlib.util
import io
import json
import re
import struct
import subprocess
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
WORKFLOWS = REPOSITORY / '.github/workflows'
spec = importlib.util.spec_from_file_location('release_candidate', ROOT / 'scripts/release-candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

REPO = 'zacharyivie/raticode-mobile'
SHA = 'a' * 40
CERT = 'c' * 64
VERSION, CODE = candidate.versions()
TAG = f'mobile-v{VERSION}'


def write_json(path, value):
    path.write_text(json.dumps(value))


def prepare(root, signed=True, instrumented=None):
    """A complete pair of build artifacts, as the build job uploads them."""
    source = root / 'input'
    android = source / 'raticode-mobile-android'
    evidence = source / 'raticode-mobile-audit-evidence'
    android.mkdir(parents=True)
    evidence.mkdir()
    apk = android / candidate.apk_name(VERSION)
    apk.write_bytes(b'fixture apk')
    apk_hash = candidate.digest(apk)
    write_json(android / 'apk-checks.json', {
        'status': 'passed', 'version': VERSION, 'version_code': CODE, 'signed': signed,
        'certificate_sha256': CERT if signed else None, 'apk_sha256': apk_hash})
    write_json(android / 'device-tests.json', {
        'status': 'passed', 'version': VERSION, 'version_code': CODE, 'apk_sha256': apk_hash,
        'instrumented': instrumented or {'tests': 90, 'failures': 0, 'errors': 0, 'skipped': 30},
        'launch': {'launched': True, 'api_level': 36, 'signer': 'release key'}})
    if signed:
        aab = android / candidate.bundle_name(VERSION)
        aab.write_bytes(b'fixture aab')
        write_json(android / 'bundle-checks.json', {
            'status': 'passed', 'version': VERSION, 'version_code': CODE, 'signed': True,
            'certificate_sha256': CERT, 'aab_sha256': candidate.digest(aab)})
    candidate.seal(android)
    write_json(evidence / 'dependency-inventory.json', {'releaseRuntime': [{'group': 'g', 'name': 'n', 'version': '1'}]})
    write_json(evidence / 'dependency-audit.json', {'status': 'passed', 'checked': 1, 'findings': []})
    artifacts = [{'id': i, 'name': n, 'expired': False, 'digest': 'sha256:' + 'b' * 64}
                 for i, n in enumerate(('raticode-mobile-android', 'raticode-mobile-audit-evidence'), 1)]
    output = root / 'release'
    manifest = candidate.create_manifest(source, output, REPO, SHA, 42, 1, artifacts, signed=signed)
    return source, output, manifest


class GitHub:
    """Stateful fake for uploads, remote downloads and release publication."""

    def __init__(self, test):
        self.releases = []
        self.files = {}
        self.uploads = 0
        self.publish_calls = 0
        self.fail_upload_at = 0
        self.fail_after_publish = False
        self.attestation_valid = True
        self.sha = SHA
        self.head = SHA
        self.trees = {}
        self.notes_request = None
        self.run = {'conclusion': 'success', 'status': 'completed', 'head_sha': SHA, 'head_branch': 'stage',
                    'event': 'push', 'path': candidate.WORKFLOW, 'repository': {'full_name': REPO}}
        env = {'GITHUB_SHA': SHA, 'GITHUB_REF_NAME': TAG, 'ANDROID_SIGNING_CERT_SHA256': CERT}
        for patcher in (
            mock.patch.object(candidate, 'api', self.api),
            mock.patch.object(candidate, 'gh', self.gh),
            mock.patch.object(candidate, 'download', self.download),
            mock.patch.object(candidate.subprocess, 'run',
                              lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=self.head + '\n')),
            mock.patch.dict(candidate.os.environ, env),
            mock.patch('sys.stdout', new_callable=io.StringIO),
        ):
            patcher.start()
            test.addCleanup(patcher.stop)
        candidate.os.environ.pop('GITHUB_STEP_SUMMARY', None)

    def api(self, endpoint, payload=None, method='GET'):
        route = endpoint.split(f'repos/{REPO}/', 1)[1].split('?', 1)[0]
        if route.startswith('commits/'):
            return {'sha': self.sha}
        if route.startswith('git/commits/'):
            commit = route.rsplit('/', 1)[1]
            return {'sha': commit, 'tree': {'sha': self.trees.get(commit, f'tree-{commit}')}}
        if route.startswith('git/trees/'):
            tree = route.removeprefix('git/trees/')
            name = 'android' if tree.endswith('/mobile') else 'mobile'
            return {'tree': [{'path': name, 'type': 'tree', 'sha': f'{tree}/{name}'}]}
        if route.startswith('actions/runs/'):
            return copy.deepcopy(self.run)
        if route == 'releases/generate-notes':
            assert payload['tag_name'] == TAG
            self.notes_request = payload
            return {'body': 'Release notes'}
        if route == 'releases':
            if method == 'POST':
                release_id = len(self.releases) + 1
                release = {**payload, 'id': release_id, 'html_url': f'https://example/{release_id}'}
                self.releases.append(release)
                self.files[release_id] = {}
                return copy.deepcopy(release)
            return copy.deepcopy(self.releases)
        release_id = int(route.split('/')[1])
        release = next(r for r in self.releases if r['id'] == release_id)
        if route.endswith('/assets'):
            return [{'id': i, 'name': n, 'release_id': release_id} for i, n in enumerate(self.files[release_id], 1)]
        if method == 'PATCH':
            release.update(payload)
            if payload.get('draft') is False:
                self.publish_calls += 1
                if self.fail_after_publish:
                    self.fail_after_publish = False
                    raise subprocess.CalledProcessError(1, 'gh')
        return copy.deepcopy(release)

    def gh(self, *args, output=None):
        if args[:2] == ('attestation', 'verify'):
            assert f'{REPO}/{candidate.WORKFLOW}' in args
            assert args[args.index('--source-ref') + 1] == 'refs/heads/stage'
            if not self.attestation_valid or args[args.index('--source-digest') + 1] != SHA:
                raise subprocess.CalledProcessError(1, 'gh attestation verify')
            return 'verified'
        assert args[:2] == ('release', 'upload') and '--clobber' not in args
        self.uploads += 1
        if self.uploads == self.fail_upload_at:
            raise subprocess.CalledProcessError(1, 'gh release upload')
        release = next(r for r in self.releases if r['tag_name'] == args[2])
        path = Path(args[3])
        assert path.name not in self.files[release['id']]
        self.files[release['id']][path.name] = path.read_bytes()
        return ''

    def download(self, repo, asset, destination):
        assert repo == REPO
        destination.write_bytes(self.files[asset['release_id']][asset['name']])

    def published(self, tag, version_code, cert=CERT, signed=True):
        """An earlier public release with its manifest."""
        release_id = len(self.releases) + 1
        self.releases.append({'id': release_id, 'tag_name': tag, 'draft': False, 'body': '', 'html_url': ''})
        self.files[release_id] = {candidate.MANIFEST: json.dumps(
            {'version_code': version_code, 'signed': signed, 'certificate_sha256': cert if signed else None}).encode()}


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)


class ManifestTest(Base):
    def test_complete_manifest_is_bound_to_source_and_signing(self):
        for signed in (True, False):
            with self.subTest(signed=signed):
                root = self.root / str(signed)
                root.mkdir()
                _, output, manifest = prepare(root, signed)
                candidate.validate_manifest(manifest, REPO, SHA, VERSION, CODE)
                self.assertEqual(set(manifest['files']), {p.name for p in output.iterdir()} - {candidate.MANIFEST})
                self.assertEqual(manifest['certificate_sha256'], CERT if signed else None)
                self.assertEqual(manifest['prerelease'], '-' in VERSION)
                self.assertEqual('release-signature' in manifest['checks'], signed)

    def test_invalid_manifest_is_rejected(self):
        _, _, original = prepare(self.root)
        changes = {
            'missing-apk': lambda m: m['files'].pop(candidate.apk_name(VERSION)),
            'signing-type': lambda m: m.update(signed='true'),
            'signing-mismatch': lambda m: m.update(signed=False),
            'no-certificate': lambda m: m.update(certificate_sha256=None),
            'commit': lambda m: m.update(commit='wrong'),
            'version': lambda m: m.update(version='wrong'),
            'version-code': lambda m: m.update(version_code=CODE - 1),
            'prerelease': lambda m: m.update(prerelease=not m['prerelease']),
            'path': lambda m: m['files'].update({'../escape': next(iter(m['files'].values()))}),
        }
        for name, change in changes.items():
            with self.subTest(name):
                manifest = copy.deepcopy(original)
                change(manifest)
                with self.assertRaises(ValueError):
                    candidate.validate_manifest(manifest, REPO, SHA, VERSION, CODE)

    def test_incomplete_or_changed_build_outputs_are_rejected(self):
        source, _, _ = prepare(self.root)
        folder = source / 'raticode-mobile-android'
        apk = folder / candidate.apk_name(VERSION)
        checks = json.loads((folder / 'apk-checks.json').read_text())
        tests = json.loads((folder / 'device-tests.json').read_text())
        changes = {
            'missing': lambda: (folder / 'device-tests.json').unlink(),
            'extra': lambda: (folder / 'mapping.txt').write_text('x'),
            'corrupt': lambda: apk.write_bytes(b'different bytes'),
            'checksum-omission': lambda: (folder / 'checksums-android.txt').write_text(
                ''.join((folder / 'checksums-android.txt').read_text().splitlines(True)[1:])),
            'failed-checks': lambda: write_json(folder / 'apk-checks.json', {**checks, 'status': 'failed'}),
            'wrong-signing': lambda: write_json(folder / 'apk-checks.json', {**checks, 'signed': False}),
            'no-instrumented': lambda: write_json(folder / 'device-tests.json', {**tests, 'instrumented': None}),
            'no-tests-ran': lambda: write_json(folder / 'device-tests.json', {
                **tests, 'instrumented': {**tests['instrumented'], 'tests': 0}}),
            'not-launched': lambda: write_json(folder / 'device-tests.json', {**tests, 'launch': {'launched': False}}),
        }
        snapshot = {p.name: p.read_bytes() for p in folder.iterdir()}
        for name, change in changes.items():
            with self.subTest(name):
                for path in list(folder.iterdir()):
                    path.unlink()
                for file, data in snapshot.items():
                    (folder / file).write_bytes(data)
                change()
                if name not in ('missing', 'extra', 'corrupt', 'checksum-omission'):
                    candidate.seal(folder)
                with self.assertRaises(ValueError):
                    candidate.verify_group(folder, 'android', VERSION, CODE, signed=True)

    def test_failed_emulator_tests_are_recorded_but_do_not_block(self):
        failed = {'tests': 233, 'failures': 4, 'errors': 1, 'skipped': 53, 'exit_code': 1,
                  'failed': ['dev.raticode.mobile.ChromeBrowserTest.chromeAsThePhonesBrowser']}
        _, output, manifest = prepare(self.root, signed=False, instrumented=failed)
        self.assertNotIn('instrumented-tests', manifest['checks'])
        self.assertIn('release-launch', manifest['checks'])
        self.assertEqual(manifest['instrumented_tests'], {
            'tests': 233, 'failures': 4, 'errors': 1, 'skipped': 53, 'exit_code': 1, 'passed': False})
        candidate.validate_manifest(manifest, REPO, SHA, VERSION, CODE)
        github = GitHub(self)
        candidate.stage(REPO, output)
        body = github.releases[0]['body']
        self.assertIn('4 failures and 1 errors in 233 instrumented tests', body)
        self.assertIn('non-blocking', body)
        self.assertIn('Unsigned build evidence', body)

    def test_passing_emulator_tests_keep_their_check(self):
        _, _, manifest = prepare(self.root)
        self.assertIn('instrumented-tests', manifest['checks'])
        self.assertTrue(manifest['instrumented_tests']['passed'])
        self.assertIn('90 instrumented tests passed', candidate.emulator_note(manifest['instrumented_tests']))

    def test_failed_audit_is_rejected(self):
        source, _, _ = prepare(self.root)
        folder = source / 'raticode-mobile-audit-evidence'
        write_json(folder / 'dependency-audit.json', {'status': 'passed', 'checked': 1, 'findings': [
            {'package': 'g:n', 'version': '1', 'advisories': ['GHSA-1'], 'unreviewed': ['GHSA-1']}]})
        with self.assertRaises(ValueError):
            candidate.verify_group(folder, 'audit-evidence', VERSION, CODE)


class PublishTest(Base):
    def test_upload_retry_resumes_identical_assets_then_publish_is_idempotent(self):
        _, output, manifest = prepare(self.root)
        github = GitHub(self)
        github.fail_upload_at = 3
        with self.assertRaises(subprocess.CalledProcessError):
            candidate.stage(REPO, output)
        self.assertNotIn(candidate.READY, github.releases[0]['body'])
        candidate.stage(REPO, output)
        snapshot = copy.deepcopy(github.files)
        count = github.uploads
        candidate.stage(REPO, output)
        self.assertEqual(github.uploads, count)
        self.assertTrue(github.releases[0]['tag_name'].startswith('mobile-candidate-'))
        candidate.publish(REPO)
        candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 1)
        self.assertEqual(github.files, snapshot)
        release = github.releases[0]
        self.assertEqual(release['tag_name'], TAG)
        self.assertFalse(release['draft'])
        self.assertEqual(release['prerelease'], manifest['prerelease'])
        self.assertIn(CERT, release['body'])

    def test_squash_commit_with_same_files_publishes(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        candidate.stage(REPO, output)
        github.head = github.sha = 'd' * 40
        github.trees = {SHA: 'tree', 'd' * 40: 'tree'}
        candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 1)

    def test_a_published_app_never_becomes_the_repositorys_latest_release(self):
        # The desktop updater reads the repository's latest release.
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        candidate.stage(REPO, output)
        candidate.publish(REPO)
        self.assertEqual(github.releases[0]['make_latest'], 'false')

    def test_same_source_compares_only_the_apps_folder(self):
        folders = {'root-a': 'mobile-a', 'root-b': 'mobile-b', 'root-c': 'mobile-c',
                   'mobile-a': 'android-same', 'mobile-b': 'android-same', 'mobile-c': 'android-other'}
        def api(endpoint):
            route = endpoint.split(f'repos/{REPO}/', 1)[1]
            if route.startswith('git/commits/'):
                return {'tree': {'sha': 'root-' + route[-1]}}
            tree = route.removeprefix('git/trees/')
            name = 'mobile' if tree.startswith('root') else 'android'
            return {'tree': [{'path': 'src', 'type': 'tree', 'sha': tree + '-desktop'},
                             {'path': name, 'type': 'tree', 'sha': folders[tree]}]}
        with mock.patch.object(candidate, 'api', api):
            self.assertTrue(candidate.same_source(REPO, 'commit-a', 'commit-b'))
            self.assertFalse(candidate.same_source(REPO, 'commit-a', 'commit-c'))

    def test_release_notes_start_at_this_apps_previous_release(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        github.releases.append({'id': 1, 'tag_name': 'v9.9.9', 'draft': False, 'body': '', 'html_url': '',
                                'published_at': '2026-11-02T00:00:00Z'})
        github.files[1] = {}
        github.published('mobile-v0.0.1', 1)
        github.releases[-1]['published_at'] = '2026-11-01T00:00:00Z'
        candidate.stage(REPO, output)
        self.assertEqual(github.notes_request['previous_tag_name'], 'mobile-v0.0.1')

    def test_retry_after_publication_response_loss_verifies_existing_release(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        candidate.stage(REPO, output)
        github.fail_after_publish = True
        with self.assertRaises(subprocess.CalledProcessError):
            candidate.publish(REPO)
        candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 1)

    def test_publication_fails_closed(self):
        changes = {
            'wrong-tag': lambda g, o, m: candidate.os.environ.update(GITHUB_REF_NAME='mobile-v9.9.9'),
            'desktop-style-tag': lambda g, o, m: candidate.os.environ.update(GITHUB_REF_NAME=f'v{VERSION}'),
            'moved-tag': lambda g, o, m: setattr(g, 'sha', 'b' * 40),
            'different-files': lambda g, o, m: (setattr(g, 'head', 'd' * 40), setattr(g, 'sha', 'd' * 40)),
            'corrupt': lambda g, o, m: g.files[1].update({candidate.apk_name(VERSION): b'tampered'}),
            'extra-asset': lambda g, o, m: g.files[1].update({'surprise.apk': b'extra'}),
            'missing-asset': lambda g, o, m: g.files[1].pop(candidate.apk_name(VERSION)),
            'failed-run': lambda g, o, m: g.run.update(conclusion='failure'),
            'pending-run': lambda g, o, m: g.run.update(status='in_progress'),
            'main-branch-run': lambda g, o, m: g.run.update(head_branch='main'),
            'untrusted-workflow': lambda g, o, m: g.run.update(path='.github/workflows/mobile-android-ci.yml'),
            'fork': lambda g, o, m: g.run.update(repository={'full_name': 'other/raticode-mobile'}),
            'bad-attestation': lambda g, o, m: setattr(g, 'attestation_valid', False),
            'not-ready': lambda g, o, m: g.releases[0].update(body='Uploading'),
            'other-certificate': lambda g, o, m: candidate.os.environ.update(ANDROID_SIGNING_CERT_SHA256='e' * 64),
            'no-certificate-variable': lambda g, o, m: candidate.os.environ.pop('ANDROID_SIGNING_CERT_SHA256'),
            'lower-than-published': lambda g, o, m: g.published('mobile-v99.0.0', CODE + 1),
            'equal-to-published': lambda g, o, m: g.published('mobile-v0.0.1', CODE),
            'key-differs-from-published': lambda g, o, m: g.published('mobile-v0.0.1', CODE - 1, cert='f' * 64),
        }
        for name, change in changes.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                _, output, manifest = prepare(Path(directory))
                github = GitHub(self)
                candidate.stage(REPO, output)
                github.releases[0]['tag_name'] = 'untagged-renamed-draft'
                change(github, output, manifest)
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    candidate.publish(REPO)
                self.assertEqual(github.publish_calls, 0)
                self.assertTrue(github.releases[0]['draft'])

    def test_expired_candidate_is_refused(self):
        _, output, manifest = prepare(self.root)
        manifest['created_at'] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        (output / candidate.MANIFEST).write_text(json.dumps(manifest))
        github = GitHub(self)
        candidate.stage(REPO, output)
        with self.assertRaisesRegex(ValueError, 'seven days'):
            candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 0)

    def test_unsigned_candidate_stages_but_never_publishes(self):
        _, output, _ = prepare(self.root, signed=False)
        github = GitHub(self)
        candidate.stage(REPO, output)
        self.assertIn('Unsigned build evidence', github.releases[0]['body'])
        with self.assertRaisesRegex(ValueError, 'Unsigned candidates cannot be published'):
            candidate.read_candidate(REPO, github.releases[0], self.root, SHA, VERSION, CODE)
        with self.assertRaises(ValueError):
            candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 0)

    def test_older_release_with_same_key_allows_publication(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        github.published('mobile-v0.0.1', CODE - 1)
        github.published('v0.4.0', CODE + 100)  # not a mobile tag
        candidate.stage(REPO, output)
        candidate.publish(REPO)
        self.assertEqual(github.publish_calls, 1)

    def test_stage_refuses_a_version_code_phones_would_reject(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        github.published('mobile-v0.0.1', CODE)
        with self.assertRaisesRegex(ValueError, 'not higher'):
            candidate.stage(REPO, output)
        self.assertEqual(len(github.releases), 1)

    def test_stage_refuses_to_overwrite_changed_remote_asset(self):
        _, output, _ = prepare(self.root)
        github = GitHub(self)
        candidate.stage(REPO, output)
        github.files[1][candidate.apk_name(VERSION)] = b'tampered'
        with self.assertRaisesRegex(ValueError, 'Existing draft asset differs'):
            candidate.stage(REPO, output)
        self.assertEqual(github.files[1][candidate.apk_name(VERSION)], b'tampered')

    def test_tag_without_candidate_never_creates_a_release(self):
        github = GitHub(self)
        with self.assertRaisesRegex(ValueError, 'No verified draft'):
            candidate.publish(REPO)
        self.assertEqual(github.releases, [])

    def test_signing_policy_is_explicit(self):
        for value, expected in ((None, False), ('false', False), ('true', True)):
            with self.subTest(value), mock.patch.dict(candidate.os.environ, {}, clear=False):
                candidate.os.environ.pop('SIGNED_RELEASE', None)
                if value is not None:
                    candidate.os.environ['SIGNED_RELEASE'] = value
                self.assertIs(candidate.signing_policy(), expected)
        for value in ('', 'True', '1'):
            with self.subTest(value), mock.patch.dict(candidate.os.environ, {'SIGNED_RELEASE': value}):
                with self.assertRaises(ValueError):
                    candidate.signing_policy()


XMLTREE = '''N: android=http://schemas.android.com/apk/res/android (line=2)
  E: manifest (line=2)
    A: http://schemas.android.com/apk/res/android:versionCode(0x0101021b)=34
    A: package="dev.raticode.mobile" (Raw: "dev.raticode.mobile")
      E: uses-permission (line=3)
        A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.permission.CAMERA" (Raw: "android.permission.CAMERA")
      E: application (line=20)
        A: http://schemas.android.com/apk/res/android:allowBackup(0x01010280)=(type 0x12)0x0
        A: http://schemas.android.com/apk/res/android:usesCleartextTraffic(0x010104ec)=false
          E: activity (line=21)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="dev.raticode.mobile.MainActivity" (Raw: "dev.raticode.mobile.MainActivity")
            A: http://schemas.android.com/apk/res/android:exported(0x01010010)=(type 0x12)0xffffffff
              E: intent-filter (line=22)
                  E: action (line=23)
                    A: http://schemas.android.com/apk/res/android:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")
          E: service (line=30)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="dev.raticode.mobile.voice.RemVoiceTile" (Raw: "dev.raticode.mobile.voice.RemVoiceTile")
            A: http://schemas.android.com/apk/res/android:permission(0x01010006)="android.permission.BIND_QUICK_SETTINGS_TILE" (Raw: "android.permission.BIND_QUICK_SETTINGS_TILE")
            A: http://schemas.android.com/apk/res/android:exported(0x01010010)=true
          E: service (line=40)
            A: http://schemas.android.com/apk/res/android:name(0x01010003)="dev.raticode.mobile.live.DeviceSyncService" (Raw: "dev.raticode.mobile.live.DeviceSyncService")
            A: http://schemas.android.com/apk/res/android:exported(0x01010010)=false
'''


class ManifestTreeTest(unittest.TestCase):
    def test_parses_manifest_tree_in_both_boolean_forms(self):
        application, exported = candidate.exported_components(candidate.parse_manifest_tree(XMLTREE))
        self.assertEqual(application['allowBackup'], False)
        self.assertEqual(application['usesCleartextTraffic'], False)
        self.assertEqual(exported, {'dev.raticode.mobile.MainActivity': None,
                                    'dev.raticode.mobile.voice.RemVoiceTile': 'android.permission.BIND_QUICK_SETTINGS_TILE'})

    def test_parses_a_bundles_proto_manifest_tree(self):
        proto = XMLTREE.replace('allowBackup(0x01010280)=(type 0x12)0x0', 'allowBackup(0x01010280)=false (Raw: "false")')
        proto = proto.replace('exported(0x01010010)=(type 0x12)0xffffffff', 'exported(0x01010010)=true (Raw: "true")')
        application, exported = candidate.exported_components(candidate.parse_manifest_tree(proto))
        self.assertIs(application['allowBackup'], False)
        self.assertIn('dev.raticode.mobile.MainActivity', exported)

    def test_policy_matches_the_manifest_source(self):
        source = (ROOT / 'app/src/main/AndroidManifest.xml').read_text(encoding='utf8')
        declared = set(re.findall(r'uses-permission android:name="([^"]+)"', source))
        self.assertLessEqual(declared, set(candidate.policy()['permissions']))
        for name in candidate.policy()['exported']:
            if name.startswith('dev.raticode.mobile.'):
                self.assertIn(name.removeprefix('dev.raticode.mobile'), source)


class ApkPolicyTest(Base):
    def setUp(self):
        super().setUp()
        self.apk = self.root / 'app.apk'
        self.apk.write_bytes(b'apk')
        rules = candidate.policy()
        self.info = {'package': rules['package'], 'version_code': CODE, 'version_name': VERSION,
                     'min_sdk': rules['min_sdk'], 'target_sdk': rules['target_sdk'], 'debuggable': False,
                     'test_only': False, 'permissions': sorted(rules['permissions'])}
        self.application = {'allowBackup': False, 'usesCleartextTraffic': False}
        self.exported = dict(rules['exported'])
        self.verdict = {'signers': 1, 'schemes': ['v2', 'v3'], 'names': ['CN=Raticode'], 'certificates': [CERT]}
        for patcher in (
            mock.patch.object(candidate, 'badging', lambda apk: copy.deepcopy(self.info)),
            mock.patch.object(candidate, 'run', lambda *a, **kw: ''),
            mock.patch.object(candidate, 'build_tool', lambda name: Path('/sdk') / name),
            mock.patch.object(candidate, 'parse_manifest_tree', lambda text: {}),
            mock.patch.object(candidate, 'exported_components',
                              lambda tree: (dict(self.application), dict(self.exported))),
            mock.patch.object(candidate, 'signature', lambda apk: copy.deepcopy(self.verdict)),
            mock.patch.object(candidate, 'native_alignment', lambda *a, **kw: {'libraries': 2}),
            mock.patch.dict(candidate.os.environ, {'ANDROID_SIGNING_CERT_SHA256': CERT}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


    def test_conforming_signed_apk_passes_and_records_evidence(self):
        evidence = candidate.check_apk(self.apk, signed=True)
        self.assertEqual((evidence['version'], evidence['version_code'], evidence['certificate_sha256']),
                         (VERSION, CODE, CERT))
        self.assertEqual(evidence['apk_sha256'], candidate.digest(self.apk))

    def test_policy_violations_fail(self):
        changes = {
            'version': lambda: self.info.update(version_name=VERSION + '-debug'),
            'version-code': lambda: self.info.update(version_code=CODE + 1),
            'package': lambda: self.info.update(package='dev.raticode.other'),
            'target-sdk': lambda: self.info.update(target_sdk=35),
            'debuggable': lambda: self.info.update(debuggable=True),
            'new-permission': lambda: self.info['permissions'].append('android.permission.READ_CONTACTS'),
            'backup': lambda: self.application.update(allowBackup=True),
            'cleartext': lambda: self.application.update(usesCleartextTraffic=None),
            'new-export': lambda: self.exported.update({'dev.raticode.mobile.Debug': None}),
            'export-lost-permission': lambda: self.exported.update(
                {'dev.raticode.mobile.voice.RemVoiceTile': None}),
            'two-signers': lambda: self.verdict.update(signers=2),
            'v1-only': lambda: self.verdict.update(schemes=['v1']),
            'debug-key': lambda: self.verdict.update(names=['CN=Android Debug, O=Android, C=US']),
            'other-key': lambda: self.verdict.update(certificates=['e' * 64]),
            'unverified': lambda: setattr(self, 'verdict', None),
        }
        saved = (copy.deepcopy(self.info), dict(self.application), dict(self.exported), copy.deepcopy(self.verdict))
        for name, change in changes.items():
            with self.subTest(name):
                self.info, self.application, self.exported, self.verdict = copy.deepcopy(saved)
                change()
                with self.assertRaises(ValueError):
                    candidate.check_apk(self.apk, signed=True)

    def test_unsigned_mode_refuses_a_signed_apk(self):
        with self.assertRaisesRegex(ValueError, 'Expected an unsigned APK'):
            candidate.check_apk(self.apk, signed=False)
        self.verdict = None
        self.assertIsNone(candidate.check_apk(self.apk, signed=False)['certificate_sha256'])

    def test_fingerprint_accepts_keytool_form(self):
        colon = ':'.join(CERT[i:i + 2] for i in range(0, 64, 2)).upper()
        self.assertEqual(candidate.fingerprint(colon), CERT)
        with self.assertRaises(ValueError):
            candidate.fingerprint('abc')


class BundleManifestTest(Base):
    def test_signed_candidate_carries_a_checked_bundle_and_unsigned_never_does(self):
        _, _, signed = prepare(self.root / 'signed')
        self.assertIn(candidate.bundle_name(VERSION), signed['files'])
        self.assertIn('release-bundle-policy', signed['checks'])
        _, _, unsigned = prepare(self.root / 'unsigned', signed=False)
        self.assertNotIn(candidate.bundle_name(VERSION), unsigned['files'])
        self.assertNotIn('release-bundle-policy', unsigned['checks'])

    def test_bundle_evidence_must_match_the_bundle_and_the_apk_key(self):
        changes = {
            'other-key': lambda c: c.update(certificate_sha256='e' * 64),
            'unsigned': lambda c: c.update(signed=False),
            'other-bundle': lambda c: c.update(aab_sha256='f' * 64),
            'failed': lambda c: c.update(status='failed'),
        }
        for name, change in changes.items():
            with self.subTest(name):
                root = self.root / name
                source, _, _ = prepare(root)
                android = source / 'raticode-mobile-android'
                checks = json.loads((android / 'bundle-checks.json').read_text())
                change(checks)
                write_json(android / 'bundle-checks.json', checks)
                candidate.seal(android)
                with self.assertRaisesRegex(ValueError, 'App Bundle checks'):
                    candidate.verify_group(android, 'android', VERSION, CODE, signed=True)

    def test_a_signed_candidate_without_its_bundle_is_refused(self):
        source, _, _ = prepare(self.root)
        android = source / 'raticode-mobile-android'
        (android / candidate.bundle_name(VERSION)).unlink()
        candidate.seal(android)
        with self.assertRaisesRegex(ValueError, 'missing'):
            candidate.verify_group(android, 'android', VERSION, CODE, signed=True)

    def test_manifest_bundle_check_must_agree_with_signing(self):
        _, _, manifest = prepare(self.root)
        manifest['checks'].remove('release-bundle-policy')
        with self.assertRaisesRegex(ValueError, 'App Bundle'):
            candidate.validate_manifest(manifest, REPO, SHA, VERSION, CODE)


def elf(align, bits=64):
    """A minimal little-endian ELF header with one LOAD segment aligned to `align`."""
    header = bytearray(64)
    header[:4] = b'\x7fELF'
    header[4] = 2 if bits == 64 else 1
    header[5] = 1
    struct.pack_into('<Q', header, 0x20, 64)
    struct.pack_into('<HH', header, 0x36, 56, 1)
    segment = bytearray(56)
    struct.pack_into('<I', segment, 0, 1)
    struct.pack_into('<Q', segment, 0x30, align)
    return bytes(header + segment)


class BundlePolicyTest(Base):
    def setUp(self):
        super().setUp()
        self.aab = self.root / 'app.aab'
        self.aab.write_bytes(b'aab')
        rules = candidate.policy()
        self.info = {'package': rules['package'], 'version_code': CODE, 'version_name': VERSION,
                     'min_sdk': rules['min_sdk'], 'target_sdk': rules['target_sdk'], 'debuggable': False,
                     'test_only': False, 'permissions': sorted(rules['permissions'])}
        self.exported = dict(rules['exported'])
        self.verdict = {'signers': 1, 'names': ['CN=Raticode Mobile, O=Raticode'], 'certificates': [CERT]}
        for patcher in (
            mock.patch.object(candidate, 'bundle_base', lambda aab, d: d / 'base.apk'),
            mock.patch.object(candidate, 'badging', lambda apk: copy.deepcopy(self.info)),
            mock.patch.object(candidate, 'run', lambda *a, **kw: ''),
            mock.patch.object(candidate, 'build_tool', lambda name: Path('/sdk') / name),
            mock.patch.object(candidate, 'parse_manifest_tree', lambda text: {}),
            mock.patch.object(candidate, 'exported_components',
                              lambda tree: ({'allowBackup': False, 'usesCleartextTraffic': False},
                                            dict(self.exported))),
            mock.patch.object(candidate, 'native_alignment', lambda *a, **kw: {'libraries': 2}),
            mock.patch.object(candidate, 'bundle_download_estimate', lambda aab: {'largest_download_estimate': 1}),
            mock.patch.object(candidate, 'jar_signature', lambda aab: copy.deepcopy(self.verdict)),
            mock.patch.dict(candidate.os.environ, {'ANDROID_SIGNING_CERT_SHA256': CERT}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_conforming_signed_bundle_passes(self):
        evidence = candidate.check_bundle(self.aab, signed=True, apk_certificate=CERT)
        self.assertEqual((evidence['version'], evidence['certificate_sha256'], evidence['signed']),
                         (VERSION, CERT, True))
        self.assertEqual(evidence['aab_sha256'], candidate.digest(self.aab))

    def test_policy_violations_fail(self):
        changes = {
            'version-code': lambda: self.info.update(version_code=CODE + 1),
            'new-permission': lambda: self.info['permissions'].append('android.permission.READ_SMS'),
            'new-export': lambda: self.exported.update({'dev.raticode.mobile.Debug': None}),
            'debuggable': lambda: self.info.update(debuggable=True),
            'other-key': lambda: self.verdict.update(certificates=['e' * 64]),
            'debug-key': lambda: self.verdict.update(names=['CN=Android Debug, O=Android, C=US']),
            'two-signers': lambda: self.verdict.update(signers=2),
            'unverified': lambda: setattr(self, 'verdict', None),
        }
        saved = (copy.deepcopy(self.info), dict(self.exported), copy.deepcopy(self.verdict))
        for name, change in changes.items():
            with self.subTest(name):
                self.info, self.exported, self.verdict = copy.deepcopy(saved)
                change()
                with self.assertRaises(ValueError):
                    candidate.check_bundle(self.aab, signed=True)

    def test_bundle_and_apk_must_share_a_key(self):
        with self.assertRaisesRegex(ValueError, 'different keys'):
            candidate.check_bundle(self.aab, signed=True, apk_certificate='e' * 64)

    def test_unsigned_mode_refuses_a_signed_bundle(self):
        with self.assertRaisesRegex(ValueError, 'Expected an unsigned App Bundle'):
            candidate.check_bundle(self.aab, signed=False)
        self.verdict = None
        self.assertIsNone(candidate.check_bundle(self.aab, signed=False)['certificate_sha256'])


class BundleArchiveTest(Base):
    def zip(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return path

    def test_native_libraries_must_be_aligned_for_16_kb_pages(self):
        good = self.zip('good.apk', {'lib/arm64-v8a/libxul.so': elf(0x4000), 'lib/x86_64/libxul.so': elf(0x10000),
                                     'lib/armeabi-v7a/old.so': elf(0x1000, bits=32), 'classes.dex': b'dex'})
        self.assertEqual(candidate.native_alignment(good)['libraries'], 2)
        bad = self.zip('bad.aab', {'base/lib/arm64-v8a/libxul.so': elf(0x1000)})
        with self.assertRaisesRegex(ValueError, '16 KB'):
            candidate.native_alignment(bad, prefix='base/lib/')

    def test_base_module_becomes_a_readable_apk(self):
        aab = self.zip('app.aab', {'base/manifest/AndroidManifest.xml': b'manifest', 'base/resources.pb': b'pb',
                                   'base/res/drawable/icon.xml': b'icon', 'base/dex/classes.dex': b'dex',
                                   'BUNDLE-METADATA/x': b'meta'})
        with zipfile.ZipFile(candidate.bundle_base(aab, self.root)) as base:
            self.assertEqual(sorted(base.namelist()), ['AndroidManifest.xml', 'res/drawable/icon.xml', 'resources.pb'])
        with self.assertRaisesRegex(ValueError, 'no base module'):
            candidate.bundle_base(self.zip('empty.aab', {'BundleConfig.pb': b''}), self.root)

    def test_download_estimate_counts_one_abi_and_enforces_plays_limit(self):
        aab = self.zip('app.aab', {'base/dex/classes.dex': b'd' * 10, 'base/lib/arm64-v8a/a.so': b'a' * 4000,
                                   'base/lib/x86_64/a.so': b'x' * 10, 'BUNDLE-METADATA/m': b'm' * 9000})
        estimate = candidate.bundle_download_estimate(aab)
        self.assertEqual(estimate['abis'], ['arm64-v8a', 'x86_64'])
        self.assertFalse(estimate['mobile_data_warning'])
        with mock.patch.object(candidate, 'PLAY_DOWNLOAD_LIMIT', 10):
            with self.assertRaisesRegex(ValueError, 'Google Play allows'):
                candidate.bundle_download_estimate(aab)

    def test_jar_signature_reads_keytool_and_treats_unsigned_as_none(self):
        colon = ':'.join(CERT[i:i + 2] for i in range(0, 64, 2)).upper()
        printed = f'Signer #1:\n\nCertificate #1:\nOwner: CN=Raticode Mobile, O=Raticode\n\t SHA256: {colon}\n'
        outputs = {'jarsigner': 'jar verified.\n', 'keytool': printed}

        def fake(args, **kw):
            return subprocess.CompletedProcess(args, 0, stdout=outputs[Path(args[0]).name])
        with mock.patch.object(candidate, 'java_tool', lambda name: Path('/jdk/bin') / name), \
                mock.patch.object(candidate.subprocess, 'run', fake):
            self.assertEqual(candidate.jar_signature(self.root / 'app.aab'),
                             {'signers': 1, 'names': ['CN=Raticode Mobile, O=Raticode'], 'certificates': [CERT]})
            outputs['jarsigner'] = 'jar is unsigned.\n'
            self.assertIsNone(candidate.jar_signature(self.root / 'app.aab'))


class AuditTest(Base):
    def setUp(self):
        super().setUp()
        self.inventory = self.root / 'inventory.json'
        write_json(self.inventory, {'build': [], 'releaseRuntime': [
            {'group': 'com.google.code.gson', 'name': 'gson', 'version': '2.13.2'},
            {'group': 'org.commonmark', 'name': 'commonmark', 'version': '0.24.0'}]})

    def audit(self, answer, accepted=None):
        rules = {**candidate.policy(), 'accepted_advisories': accepted or {}}
        with mock.patch.object(candidate, 'osv_query', lambda queries: answer), \
                mock.patch.object(candidate, 'policy', lambda: rules):
            return candidate.audit(self.inventory, self.root / 'out')

    def test_clean_answer_passes(self):
        report = self.audit([{}, {}])
        self.assertEqual((report['status'], report['checked']), ('passed', 2))
        self.assertTrue((self.root / 'out/dependency-inventory.json').is_file())

    def test_advisory_fails_unless_reviewed(self):
        answer = [{'vulns': [{'id': 'GHSA-test'}]}, {}]
        with self.assertRaisesRegex(ValueError, 'GHSA-test'):
            self.audit(answer)
        self.assertEqual(json.loads((self.root / 'out/dependency-audit.json').read_text())['status'], 'failed')
        self.assertEqual(self.audit(answer, {'GHSA-test': 'not reachable'})['status'], 'passed')

    def test_incomplete_answer_fails(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.audit([{}])
        self.assertFalse((self.root / 'out/dependency-audit.json').exists())


class DeviceTest(Base):
    def test_instrumented_results_are_summed_and_failures_rejected(self):
        folder = self.root / 'connected/debug'
        folder.mkdir(parents=True)
        (folder / 'TEST-a.xml').write_text('<testsuite tests="4" failures="0" errors="0" skipped="1"/>')
        (folder / 'TEST-b.xml').write_text('<testsuite tests="3" failures="0" errors="0" skipped="2"/>')
        self.assertEqual(candidate.instrumented_results(self.root),
                         {'tests': 7, 'failures': 0, 'errors': 0, 'skipped': 3})
        (folder / 'TEST-c.xml').write_text('<testsuite tests="1" failures="1" errors="0" skipped="0"/>')
        with self.assertRaises(ValueError):
            candidate.instrumented_results(self.root)
        with self.assertRaises(ValueError):
            candidate.instrumented_results(self.root / 'empty')

    def test_without_strict_failures_are_counted_but_no_tests_is_still_refused(self):
        folder = self.root / 'connected/debug'
        folder.mkdir(parents=True)
        (folder / 'TEST-a.xml').write_text(
            '<testsuite tests="2" failures="1" errors="0" skipped="0">'
            '<testcase classname="dev.raticode.mobile.A" name="ok"/>'
            '<testcase classname="dev.raticode.mobile.A" name="broken"><failure message="x"/></testcase>'
            '</testsuite>')
        self.assertEqual(candidate.instrumented_results(self.root, strict=False),
                         {'tests': 2, 'failures': 1, 'errors': 0, 'skipped': 0})
        self.assertEqual(candidate.failed_tests(self.root), ['dev.raticode.mobile.A.broken'])
        empty = self.root / 'none/connected'
        empty.mkdir(parents=True)
        (empty / 'TEST-z.xml').write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"/>')
        with self.assertRaisesRegex(ValueError, 'ran no tests'):
            candidate.instrumented_results(self.root / 'none', strict=False)
        with self.assertRaisesRegex(ValueError, 'No instrumented test results'):
            candidate.instrumented_results(self.root / 'missing', strict=False)

    def test_device_test_records_failed_tests_and_still_requires_the_launch(self):
        folder = self.root / 'candidate'
        folder.mkdir()
        (folder / candidate.apk_name(VERSION)).write_bytes(b'apk')
        results = self.root / 'results'
        results.mkdir()
        (results / 'TEST-a.xml').write_text(
            '<testsuite tests="3" failures="1" errors="0" skipped="1">'
            '<testcase classname="T" name="bad"><failure/></testcase></testsuite>')
        launched = {'launched': True, 'api_level': 36, 'signer': 'throwaway test key'}
        with mock.patch.object(candidate, 'launch', lambda apk, serial=None: launched):
            evidence = candidate.device_test(folder, results, instrumented_exit=1)
        self.assertEqual(evidence['status'], 'passed')
        self.assertEqual(evidence['instrumented']['passed'], False)
        self.assertEqual(evidence['instrumented']['exit_code'], 1)
        self.assertEqual(evidence['instrumented']['failed'], ['T.bad'])

        def refuse(apk, serial=None):
            raise ValueError('The app crashed after launch')

        with mock.patch.object(candidate, 'launch', refuse), self.assertRaisesRegex(ValueError, 'crashed'):
            candidate.device_test(folder, results, instrumented_exit=0)

    def test_report_marks_failures_non_blocking_in_the_summary_and_outputs(self):
        folder = self.root / 'candidate'
        folder.mkdir()
        summary, outputs = self.root / 'summary.md', self.root / 'outputs'
        for instrumented, passed in (
            ({'tests': 5, 'failures': 2, 'errors': 0, 'skipped': 0, 'exit_code': 1, 'failed': ['T.a']}, 'false'),
            ({'tests': 5, 'failures': 0, 'errors': 0, 'skipped': 1, 'exit_code': 0}, 'true'),
        ):
            with self.subTest(passed):
                summary.write_text(''); outputs.write_text('')
                write_json(folder / 'device-tests.json', {'instrumented': instrumented})
                with mock.patch.dict('os.environ', {'GITHUB_STEP_SUMMARY': str(summary), 'GITHUB_OUTPUT': str(outputs)}), \
                        mock.patch('sys.stdout', new_callable=io.StringIO) as printed:
                    candidate.report_instrumented(folder)
                self.assertIn(f'instrumented_passed={passed}', outputs.read_text())
                if passed == 'false':
                    self.assertIn('failed (non-blocking)', summary.read_text())
                    self.assertIn('`T.a`', summary.read_text())
                    self.assertIn('::warning title=Emulator tests failed (non-blocking)', printed.getvalue())
                else:
                    self.assertIn('Emulator tests passed', summary.read_text())

    def test_launch_refuses_a_real_phone(self):
        calls = []

        def fake_run(*args, **kw):
            calls.append([str(a) for a in args])
            return '0\n'

        with mock.patch.object(candidate, 'run', fake_run), \
                mock.patch.object(candidate, 'sdk_tool', lambda *parts: Path('/sdk/adb')):
            for serial in ('R58M123ABC', 'emulator-5554'):
                with self.subTest(serial), self.assertRaisesRegex(ValueError, 'not an emulator'):
                    candidate.launch(self.root / 'app.apk', serial)
        self.assertFalse(any('install' in c or 'uninstall' in c for c in calls))


@unittest.skipUnless(WORKFLOWS.is_dir(), 'an export of the app alone has no repository workflows')
class WorkflowTest(unittest.TestCase):
    def test_actions_are_pinned_to_commits(self):
        for path in WORKFLOWS.glob('mobile-*.yml'):
            for line in path.read_text(encoding='utf8').splitlines():
                match = re.search(r'uses: (\S+)', line)
                if match and not match.group(1).startswith('./'):
                    self.assertRegex(match.group(1), r'@[0-9a-f]{40}$', f'{path.name}: {line.strip()}')

    def test_candidate_and_publish_workflows_agree_with_the_script(self):
        prepare_text = (REPOSITORY / candidate.WORKFLOW).read_text(encoding='utf8')
        publish_text = (WORKFLOWS / 'mobile-release.yml').read_text(encoding='utf8')
        self.assertIn('branches:\n      - stage', prepare_text)
        self.assertIn('pattern: raticode-mobile-*', prepare_text)
        self.assertIn(f"- '{candidate.TAG_PREFIX}*'", publish_text)
        for name in ('android', 'audit-evidence'):
            self.assertIn(f'name: raticode-mobile-{name}', prepare_text)
        self.assertNotIn('tags:', (WORKFLOWS / 'mobile-android-ci.yml').read_text(encoding='utf8'))

    def test_emulator_tests_are_non_blocking_but_build_and_launch_still_block(self):
        text = (REPOSITORY / candidate.WORKFLOW).read_text(encoding='utf8')
        build = text.split('  build:\n', 1)[1].split('\n  emulator-tests:\n', 1)[0]
        # The Gradle exit code is recorded and passed on, not used to stop the step;
        # device-test still fails the job when no tests ran or the release APK won't launch.
        self.assertIn('connectedDebugAndroidTest --console=plain; python3 scripts/release-candidate.py '
                      'device-test', build)
        self.assertIn('--instrumented-exit $?', build)
        self.assertNotIn('connectedDebugAndroidTest --console=plain &&', build)
        self.assertNotIn('continue-on-error:', build)
        self.assertIn('assembleRelease bundleRelease lintRelease', build)
        self.assertIn('release-candidate.py collect', build)
        self.assertIn('report-instrumented', build)
        jobs = text.split('\n  emulator-tests:\n', 1)[1]
        self.assertIn('name: Emulator tests (non-blocking)', jobs)
        self.assertIn('continue-on-error: true', jobs.split('\n  stage:\n', 1)[0])
        stage = text.split('\n  stage:\n', 1)[1]
        self.assertIn('needs: [credentials, build]', stage)
        self.assertNotIn('emulator-tests', stage)

    def test_mobile_signing_settings_are_separate_from_the_desktops(self):
        text = (REPOSITORY / candidate.WORKFLOW).read_text(encoding='utf8')
        self.assertNotIn('vars.RELEASE_SIGNING', text)
        self.assertNotIn("'release-signing'", text)
        self.assertNotIn("'release-unsigned'", text)
        self.assertIn('vars.ANDROID_RELEASE_SIGNING', text)


if __name__ == '__main__':
    unittest.main()
