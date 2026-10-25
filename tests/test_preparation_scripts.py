"""Preparation contracts with artificial inputs; real preparation runs in cloud CI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import preparation as prep


class PreparationScriptTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.audit').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.audit', prefix='preparation tests ')
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *args):
        return prep.parser().parse_args([*args, '--output', str(self.base / 'out')])

    def test_default_data_mode_is_scoring_only(self):
        args = self.args('dataspace', '--evaluation-root', str(self.base / 'evaluation'))
        paths, details = prep.preflight(args)
        self.assertEqual(args.mode, 'evaluation')
        self.assertNotIn('dataset', paths)
        self.assertEqual(details['status'], 'STATIC_ONLY')
        self.assertFalse(paths['output'].exists())

    def test_external_and_parent_traversal_rejected(self):
        for name in ('/tmp/preparation-outside', '.audit/../.local/private', '.'):
            with self.assertRaises(ValueError):
                prep.local(name)

    def test_symlink_ancestor_rejected(self):
        (self.base / 'real').mkdir()
        (self.base / 'linked').symlink_to(self.base / 'real', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked'):
            prep.local(self.base / 'linked/child')

    def test_input_output_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            prep.preflight(self.args('dataspace', '--cache', str(self.base / 'cache'),
                                     '--evaluation-root', str(self.base / 'cache/evaluation')))

    def test_existing_data_requires_explicit_reuse(self):
        target = self.base / 'evaluation'
        target.mkdir()
        with self.assertRaisesRegex(ValueError, '--reuse'):
            prep.preflight(self.args('dataspace', '--evaluation-root', str(target)))
        prep.preflight(self.args('dataspace', '--evaluation-root', str(target), '--reuse'))

    def test_output_must_be_new(self):
        (self.base / 'out').mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            prep.preflight(self.args('dataspace'))

    def test_full_mode_checks_space_before_downloading(self):
        with patch.object(prep, 'available', return_value=0):
            with self.assertRaisesRegex(ValueError, 'space'):
                prep.preflight(self.args('dataspace', '--mode', 'full',
                                         '--dataset', str(ROOT / 'data/preparation-artificial-target')))

    def test_environment_rejects_unsupported_python(self):
        with patch.object(prep.sys, 'version_info', (3, 10, 22)):
            with self.assertRaisesRegex(ValueError, '3.12/3.13'):
                prep.preflight(self.args('environment'))

    def test_history_requires_baseline_interpreter(self):
        with patch.object(prep.platform, 'python_version', return_value='3.13.16'):
            with self.assertRaisesRegex(ValueError, '3.12.3'):
                prep.preflight(self.args('history'))

    def test_history_requires_external_checksum_even_for_static_check(self):
        evaluation = self.base / 'evaluation'
        evaluation.mkdir()
        archive = self.base / 'eviweave-v0.1.0-v6-evidence.zip'
        archive.write_bytes(b'artificial archive')
        with patch.object(prep.platform, 'python_version', return_value='3.12.3'):
            with self.assertRaisesRegex(ValueError, 'external'):
                prep.preflight(self.args('history', '--archive', str(archive), '--evaluation-root', str(evaluation)))

    def test_check_only_and_help_write_nothing_and_never_load_private_env(self):
        # A clean source copy removes access to developer runtime/configuration.
        source = self.base / 'clean source'
        for directory in ('tools', 'scripts'):
            (source / directory).mkdir(parents=True)
        shutil.copyfile(ROOT / 'tools/preparation.py', source / 'tools/preparation.py')
        for script in ('prepare_environment.sh', 'prepare_dataspace.sh', 'verify_history.sh'):
            shutil.copyfile(ROOT / 'scripts' / script, source / 'scripts' / script)
        (source / '.local').mkdir()
        (source / '.local/env.sh').write_text('exit 99\n')
        before = sorted(str(p.relative_to(source)) for p in source.rglob('*'))
        env = dict(os.environ, EVIWEAVE_API_KEY='artificial-test-value', HOME=str(self.base / 'absent-home'))
        for script in ('prepare_environment.sh', 'prepare_dataspace.sh', 'verify_history.sh'):
            result = subprocess.run(['bash', source / 'scripts' / script, '--python', sys.executable, '--help'],
                                    cwd=self.base, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('artificial-test-value', result.stdout + result.stderr)
        result = subprocess.run(['bash', source / 'scripts/prepare_dataspace.sh', '--python', sys.executable,
                                 '--check-only'], cwd=self.base, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'STATIC_ONLY')
        self.assertEqual(before, sorted(str(p.relative_to(source)) for p in source.rglob('*')))
        self.assertFalse((self.base / 'absent-home').exists())

    def test_subprocess_environment_excludes_credentials(self):
        paths, details = prep.preflight(self.args('dataspace', '--evaluation-root', str(self.base / 'evaluation')))
        with patch.dict(os.environ, EVIWEAVE_API_KEY='artificial-test-value', GH_TOKEN='artificial-token'):
            run = prep.Run(paths, details)
        for name in ('EVIWEAVE_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN', 'PYTHONPATH'):
            self.assertNotIn(name, run.env)
        self.assertTrue(Path(run.env['HOME']).is_relative_to(self.base))

    def test_full_mode_dispatch_and_reuse_are_explicit(self):
        paths = {'evaluation': self.base / 'evaluation', 'dataset': self.base / 'dataset', 'cache': self.base / 'cache'}
        run = unittest.mock.Mock(paths=paths, doc={})
        args = self.args('dataspace', '--mode', 'full')
        with patch.object(prep, 'resources') as resources:
            prep.dataspace(run, args)
            self.assertEqual([c.args[1] for c in resources.call_args_list], ['evaluation', 'dataset'])
        paths['dataset'].mkdir()
        with patch.object(prep, 'resources') as resources, patch.object(prep, 'verify_dataset') as verify:
            prep.dataspace(run, args)
            self.assertEqual([c.args[1] for c in resources.call_args_list], ['evaluation'])
            verify.assert_called_once_with(paths['cache'] / 'DataSpace-Benchmark.zip', paths['dataset'])

    def test_full_dataset_reuse_detects_changed_bytes(self):
        import public_resources
        archive = self.base / 'dataset.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('DataSpace-Benchmark/input/task_1/task.json', b'artificial')
        target = self.base / 'dataset'
        (target / 'input/task_1').mkdir(parents=True)
        (target / 'input/task_1/task.json').write_bytes(b'artificial')
        entry = {'size': archive.stat().st_size, 'sha256': prep.digest(archive)}
        with patch.object(public_resources, 'resource_lock', return_value={'dataset': entry}):
            prep.verify_dataset(archive, target)
            (target / 'input/task_1/task.json').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                prep.verify_dataset(archive, target)

    def test_foreign_daemon_pid_rejected(self):
        storage = self.base / 'storage'
        storage.mkdir()
        (storage / 'docker.pid').write_text(str(os.getpid()))
        with self.assertRaisesRegex(ValueError, 'foreign'):
            prep.daemon_live(storage)

    def test_lock_contention_is_not_silently_waited_out(self):
        import fcntl
        guard = ROOT / '.audit/preparation.lock'
        with guard.open('a') as first:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(['bash', ROOT / 'scripts/prepare_dataspace.sh', '--python', sys.executable,
                                     '--output', self.base / 'out', '--evaluation-root', self.base / 'evaluation'],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads((self.base / 'out/preparation-report.json').read_text())
        self.assertIn('lock occupied', report['error'])
        self.assertEqual(report['steps'], [])

    def test_interrupt_stops_the_child_process_group_and_records_130(self):
        paths, details = prep.preflight(self.args('dataspace', '--evaluation-root', str(self.base / 'evaluation')))
        run = prep.Run(paths, details)
        process = unittest.mock.Mock(pid=12345)
        process.wait.side_effect = [KeyboardInterrupt, 0]
        with patch.object(prep.subprocess, 'Popen', return_value=process), patch.object(prep.os, 'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                run.step('interrupted', 'tools/standalone.py', '--help')
        kill.assert_called_once_with(12345, prep.signal.SIGTERM)
        self.assertEqual(run.doc['steps'][-1]['returncode'], 130)

    def test_failure_stops_only_a_daemon_owned_by_this_run(self):
        import public_resources
        for prior in (False, True):
            root = self.base / str(prior)
            root.mkdir()
            paths = {'runtime': root / 'runtime', 'cache': root / 'cache', 'profile': root / 'profile.json',
                     'config': root / 'offline.json', 'venv': root / '.venv'}
            run = unittest.mock.Mock(paths=paths, out=root, env={}, doc={})
            def step(name, *args, **kwargs):
                if name == 'resource-workbench':
                    storage = paths['runtime'] / 'build'
                    storage.mkdir(parents=True)
                    (storage / 'docker.pid').write_text('12345')
                if name == 'collect-profile':
                    raise ValueError('artificial profile failure')
            run.step.side_effect = step
            with patch.object(prep, 'daemon_live', return_value=prior), patch.object(prep, 'resources'), \
                    patch.object(prep.subprocess, 'run'), patch.object(public_resources, 'prepare_storage'):
                with self.assertRaisesRegex(ValueError, 'artificial profile failure'):
                    prep.environment(run, self.args('environment'))
            stopped = any(c.args[0] == 'daemon-stop' for c in run.step.call_args_list)
            self.assertEqual(stopped, not prior)

    def test_history_download_is_opt_in(self):
        import public_resources
        run = unittest.mock.Mock(paths={'archive': self.base / 'missing.zip', 'sidecar': self.base / 'missing.zip.sha256'})
        with patch.object(public_resources, 'fetch') as fetch:
            with self.assertRaisesRegex(ValueError, 'historical asset'):
                prep.history(run, self.args('history'))
            fetch.assert_not_called()

    def test_history_rejects_per_task_score_drift(self):
        import evidence_verify
        archive = self.base / 'eviweave-v0.1.0-v6-evidence.zip'
        sidecar = archive.with_name(archive.name + '.sha256')
        archive.write_bytes(b'artificial archive')
        sidecar.write_text('artificial external digest')
        entries = [{'name': p.name, 'size': p.stat().st_size, 'sha256': prep.digest(p)} for p in (archive, sidecar)]
        expected = {'tasks': [{'task_id': 'task_1', 'passed': True}]}
        run = unittest.mock.Mock(paths={'archive': archive, 'sidecar': sidecar, 'evaluation': self.base / 'evaluation'},
                                 out=self.base / 'run', doc={})
        def step(name, *args, **kwargs):
            if name == 'verify-evidence':
                target = run.out / 'evidence-check/report.json'
                body = {'external_sha256': 'PASS'}
            else:
                target = run.out / 'score/score.json'
                body = {'tasks': [{'task_id': 'task_1', 'passed': False}]}
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(body))
        run.step.side_effect = step
        with patch.object(prep, 'evidence_assets', return_value=entries), \
                patch.object(evidence_verify, 'read_archive', return_value={'evaluation/public60.json': json.dumps(expected).encode()}):
            with self.assertRaisesRegex(ValueError, 'per-task'):
                prep.history(run, self.args('history'))

    def test_new_source_layer_retains_history_and_forbids_business_retirement(self):
        import copy
        from verify_release import effective_provenance
        members = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / 'provenance').glob('*.json')}
        layer_name = 'provenance/preparation-scripts-baseline.json'
        layer = json.loads(members[layer_name])
        corrupted = dict(members)
        corrupted['provenance/p5-documentation-baseline.json'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'retained provenance'):
            effective_provenance(corrupted)
        invalid = copy.deepcopy(layer)
        invalid['removed_files'][0]['destination'] = 'src/dataspace_baselines/cli.py'
        corrupted = dict(members, **{layer_name: json.dumps(invalid).encode()})
        with self.assertRaisesRegex(ValueError, 'retirement'):
            effective_provenance(corrupted)


if __name__ == '__main__':
    unittest.main()
