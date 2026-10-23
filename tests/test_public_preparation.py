"""Artificial expectations for P5 preparation; no public network in unit tests."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import environment_lock as environments
import experiment


class PublicPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / '.audit', prefix='p5 regression ')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def valid_profile(self):
        import host_profile as profiles
        import public_resources as resources
        from experiment_store import sha
        doc = profiles.skeleton('artificial')
        doc['tools'] = {n: {'sha256': '1' * 64, 'version': 'fixture tool 1'} for n in ('docker', 'bwrap')}
        inv = {'schema': 1, 'entries': [{'path': '.', 'type': 'directory', 'mode': 493,
                                       'uid': 1000, 'gid': 1000, 'xattrs': {}}]}
        doc['runtime'] = {'kind': 'independent-public-rebuild', 'resource_lock_sha256': sha(resources.LOCK),
                          'codex_root': str(self.root / 'codex'), 'workbench_root': str(self.root / 'workbench'),
                          'docker_host': 'unix://' + str(self.root / 'daemon/docker.sock'),
                          'image': 'eviweave-workbench:p5-123456abcdef',
                          'codex_inventory': copy.deepcopy(inv), 'workbench_inventory': copy.deepcopy(inv)}
        return doc

    def test_bootstrap_import_needs_only_standard_library(self):
        # -S removes PyYAML and every other installed dependency.
        result = subprocess.run([sys.executable, '-S', '-B', str(PROJECT / 'tools/install_environment.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--environment-profile', result.stdout)

    def test_ci_accepts_a_verified_wheel_cache_without_index_resolution(self):
        result = subprocess.run([sys.executable, '-S', '-B', str(PROJECT / 'tools/ci_check.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--wheel-cache', result.stdout)

    def test_profile_selection_is_explicit_and_old_default_survives(self):
        import host_profile as profiles
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(environments.profile()['format_version'], 1)
        with self.assertRaises(ValueError):
            profiles.load_profile(self.root / 'missing.json')

    def test_both_exact_profiles_can_be_selected_before_target_python_checks(self):
        # Selection identifies a lock; check_python separately enforces its binary.
        for suffix in ('312', '313'):
            doc = environments.profile(PROJECT / 'locks' / f'cpython{suffix}-linux-x86_64.json')
            self.assertEqual(doc['profile_id'], f'cpython{suffix}-linux-x86_64')

    def test_profile_rejects_extra_fields_and_modified_dependency(self):
        import host_profile as profiles
        # A real profile is not needed to reject an executable/credential field.
        for field in ('api_key', 'endpoint', 'post_install', 'command'):
            with self.assertRaises(ValueError):
                profiles.validate({'format_version': 2, field: 'PRIVATE-SENTINEL'})
        doc = profiles.skeleton('artificial')
        doc['packages']['pip']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'dependency'):
            profiles.validate(doc)

    def test_valid_independent_profile_cannot_remove_checks_or_add_hooks(self):
        import host_profile as profiles
        valid = self.valid_profile()
        self.assertEqual(profiles.validate(valid), valid)
        for section, name, value in (('python', 'sha256', True), ('host', 'release', []),
                                     ('runtime', 'command', 'do something'), ('tools', 'docker', {}),
                                     ('runtime', 'docker_host', 'unix:///tmp/docker.sock'),
                                     ('runtime', 'workbench_root', str(PROJECT / 'src/fake'))):
            changed = copy.deepcopy(valid)
            changed[section][name] = value
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                profiles.validate(changed)
        changed = copy.deepcopy(valid); changed['online_collection'] = 'certified'
        with self.assertRaises(ValueError): profiles.validate(changed)

    def test_inventory_rejects_traversal_types_duplicates_and_unknown_fields(self):
        import host_profile as profiles
        original = self.valid_profile()['runtime']['codex_inventory']
        for field, value in (('path', '../escape'), ('mode', True), ('uid', '0'), ('xattrs', {'name': 'not hex'}), ('command', 'hook')):
            doc = copy.deepcopy(original); doc['entries'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): profiles.validate_inventory(doc)
        doc = copy.deepcopy(original); doc['entries'] *= 2
        with self.assertRaises(ValueError): profiles.validate_inventory(doc)

    def test_selected_profile_is_returned_only_when_requested(self):
        import host_profile as profiles
        doc = self.valid_profile(); path = self.root / 'candidate.json'; path.write_text(json.dumps(doc))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(environments.profile()['format_version'], 1)
            self.assertEqual(environments.profile(path), doc)
        with patch.dict(os.environ, {'EVIWEAVE_ENVIRONMENT_PROFILE': str(path)}, clear=True):
            self.assertEqual(environments.profile(), doc)
            original = PROJECT / 'locks' / (profiles.dependency_profile() + '.json')
            self.assertEqual(environments.profile(original)['format_version'], 1)

    def test_bad_old_profile_and_runtime_missing_are_rejected(self):
        import host_profile as profiles
        original = json.loads((PROJECT / 'locks' / (profiles.dependency_profile() + '.json')).read_text())
        original['host']['release'] = 'changed'; path = self.root / 'old.json'; path.write_text(json.dumps(original))
        with self.assertRaises(ValueError): profiles.load_profile(path)
        with self.assertRaises((ValueError, FileNotFoundError)): profiles.runtime_check(self.valid_profile())

    def test_missing_namespace_fails_without_fallback(self):
        import host_profile as profiles
        result = subprocess.CompletedProcess([], 1, '', 'fixture namespace denied')
        with patch('subprocess.run', return_value=result) as call:
            with self.assertRaisesRegex(ValueError, 'no host fallback'):
                profiles.capability(self.valid_profile(), self.root / 'capability')
        self.assertEqual(call.call_count, 1)
        command = call.call_args.args[0]
        self.assertIn('--unshare-net', command)
        self.assertIn('--unshare-user', command)
        self.assertNotIn('exec', command)

    def test_private_output_refuses_sources_links_and_escape(self):
        import public_resources as resources
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        for p in (PROJECT, PROJECT / 'src/new', PROJECT.parent / 'escape', self.root / 'link/out'):
            with self.assertRaises(ValueError):
                resources.private_path(p)

    def test_daemon_storage_rejects_existing_conflicts_and_linked_controls(self):
        import public_resources as resources
        occupied = self.root / 'occupied'; occupied.mkdir(); (occupied / 'user-file').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'storage conflict'):
            resources.prepare_storage(occupied)
        self.assertEqual((occupied / 'user-file').read_text(), 'keep')
        storage = self.root / 'new-daemon'
        resources.prepare_storage(storage); resources.prepare_storage(storage)
        victim = self.root / 'victim'; victim.write_text('unchanged')
        (storage / 'daemon.json').symlink_to(victim)
        with self.assertRaises(ValueError): resources.prepare_storage(storage)
        self.assertEqual(victim.read_text(), 'unchanged')

    def test_download_cache_hash_checked_and_corruption_not_replaced(self):
        import public_resources as resources
        path = self.root / 'cached'
        path.write_bytes(b'known fixture')
        entry = {'url': 'https://example.invalid/unused', 'sha256': hashlib.sha256(b'known fixture').hexdigest(), 'size': 13}
        with patch('urllib.request.urlopen', side_effect=AssertionError('no download for verified cache')):
            self.assertEqual(resources.fetch(entry, path), path)
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'digest|size'):
                resources.fetch(entry, path)
        self.assertEqual(path.read_bytes(), b'changed')

    def test_zip_path_duplicates_and_links_rejected_before_extracting(self):
        import public_resources as resources
        for names in (['../outside'], ['/absolute'], ['same', 'same']):
            stream = self.root / 'bad.zip'
            with zipfile.ZipFile(stream, 'w') as z:
                for n in names:
                    z.writestr(n, b'x')
            target = self.root / 'unpacked'
            with self.assertRaises(ValueError):
                resources.extract_dataset(stream, target)
            self.assertFalse(target.exists())

    def test_zip_extracts_expected_bytes_and_refuses_conflict(self):
        import public_resources as resources
        archive = self.root / 'good.zip'; target = self.root / 'data'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('DataSpace-Benchmark/input/task_1/task.json', b'{"task_id":"task_1"}')
        resources.extract_dataset(archive, target)
        self.assertEqual((target / 'input/task_1/task.json').read_bytes(), b'{"task_id":"task_1"}')
        with self.assertRaisesRegex(ValueError, 'conflict'): resources.extract_dataset(archive, target)

    def test_permission_denied_download_is_explicit(self):
        import public_resources as resources
        entry = {'url': 'https://example.invalid/fixture', 'sha256': '0' * 64, 'size': 3}
        with patch('tempfile.mkstemp', side_effect=PermissionError('fixture permission denied')):
            with self.assertRaises(PermissionError): resources.fetch(entry, self.root / 'cache/item')

    def test_failed_download_is_not_promoted_or_retried(self):
        import public_resources as resources
        response = io.BytesIO(b'abc'); response.status = 200
        entry = {'url': 'https://example.invalid/fixture', 'sha256': '0' * 64, 'size': 3}
        with patch('urllib.request.urlopen', return_value=response) as call:
            with self.assertRaisesRegex(ValueError, 'digest'):
                resources.fetch(entry, self.root / 'cache/item')
        self.assertEqual(call.call_count, 1)
        self.assertFalse((self.root / 'cache/item').exists())
        self.assertTrue((self.root / 'cache/item.failure.json').is_file())

    def test_range_requires_exact_content_range(self):
        import public_resources as resources
        response = io.BytesIO(b'abc')
        response.status = 206
        response.headers = {'Content-Range': 'bytes 2-4/10'}
        with patch('urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'Range'):
                resources.range_bytes('https://example.invalid/fixture', 1, 3, 10)

    def test_parallel_archive_complete_hash_and_reuse(self):
        import public_resources as resources
        content = b'0123456789abcdefghij'
        entry = {'url': 'https://example.invalid/data', 'size': len(content),
                 'sha256': hashlib.sha256(content).hexdigest()}
        target = self.root / 'archive.zip'
        with patch('public_resources.range_bytes', side_effect=lambda u, a, b, total: content[a:b+1]):
            self.assertEqual(resources.fetch_archive(entry, target, chunk_size=7).read_bytes(), content)
        with patch('public_resources.range_bytes', side_effect=AssertionError('verified reuse needs no network')):
            resources.fetch_archive(entry, target, chunk_size=7)
            target.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'digest|size'):
                resources.fetch_archive(entry, target, chunk_size=7)

    def test_resume_rejects_explicit_profile_before_loading_experiment(self):
        args = experiment.parser().parse_args(['run', '--resume', '--output', str(self.root / 'run'),
                                              '--environment-profile', str(self.root / 'p.json')])
        with self.assertRaisesRegex(ValueError, 'resume'):
            experiment.execute(args)

    def test_public_lock_still_covers_original_121_without_relaxation(self):
        import public_resources as resources
        lock = resources.resource_lock()
        original = json.loads((PROJECT / 'provenance/evaluation-lock.json').read_text())['files']
        self.assertEqual({x['path']: x['sha256'] for x in lock['scoring']}, original)
        self.assertEqual(len(lock['system']), 329)
        self.assertEqual(len(lock['wheels']), 21)
        self.assertEqual(lock['codex']['version'], '0.145.0')


if __name__ == '__main__':
    unittest.main()
