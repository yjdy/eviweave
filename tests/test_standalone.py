"""Independent configuration/resource failures using project-local artificial inputs."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import standalone as s


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        tmp = PROJECT / '.audit/standalone-unit-tmp'
        tmp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=tmp)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'dataset'
        task = self.data / 'input/task_1'
        (task / 'context').mkdir(parents=True)
        (task / 'task.json').write_text('{"task_id":"task_1","question":"synthetic"}')

    def config(self):
        out = self.root / 'private'
        s.configure(self.data, 'https://example.invalid/v1', 'test-model', out)
        return out / 'settings.json'

    def resources(self):
        # Handwritten fake content; use the published member names, never real gold.
        source = self.root / 'source'
        source.mkdir()
        lock = copy.deepcopy(s.read_lock())
        for name in lock['files']:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('artificial fixture: ' + name)
            lock['files'][name] = s.digest(path)
        lock['evaluator_sha256'] = lock['files']['evaluate.py']
        path = self.root / 'test-lock.json'
        s.write_json(path, lock)
        return source, path

    def test_explicit_dataset_and_environment_only_credentials(self):
        with patch.dict(os.environ, {'EVIWEAVE_API_KEY': 'fixture-secret-must-not-save'}, clear=True):
            settings = self.config()
        text = ''.join(p.read_text() for p in settings.parent.iterdir())
        self.assertNotIn('fixture-secret-must-not-save', text)
        self.assertIn('EVIWEAVE_API_KEY', text)
        task, config = s.task_paths(settings, 1)
        self.assertEqual(task, self.data / 'input/task_1')
        self.assertTrue(config.is_file())

    def test_borrowed_credentials_never_satisfy_live_run(self):
        settings = self.config()
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'borrowed', 'OPENAI_API_KEY': 'borrowed'}, clear=True), patch.object(s.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'EVIWEAVE_API_KEY'):
                s.run_task(settings, 1, self.root / 'run')
            run.assert_not_called()
        self.assertFalse((self.root / 'run').exists())

    def test_missing_explicit_dataset_fails_without_discovery(self):
        settings = self.config()
        (self.data / 'input/task_1/task.json').unlink()
        with patch.object(s.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'no dataset fallback'):
                s.run_task(settings, 1, self.root / 'run')
            run.assert_not_called()

    def test_changed_config_and_wrong_key_name_are_rejected(self):
        settings = self.config()
        data = json.loads(settings.read_text())
        config_path = Path(data['config'])
        config = json.loads(config_path.read_text())
        config['experiment']['model']['api_key_env'] = 'DASHSCOPE_API_KEY'
        s.write_json(config_path, config)
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            s.task_paths(settings, 1)
        data['config_sha256'] = s.digest(config_path)
        s.write_json(settings, data)
        with self.assertRaisesRegex(ValueError, 'only EVIWEAVE_API_KEY'):
            s.task_paths(settings, 1)

    def test_configuration_outputs_do_not_overwrite(self):
        settings = self.config()
        before = settings.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.config()
        self.assertEqual(before, settings.read_bytes())

    def test_configuration_rejects_credential_in_endpoint(self):
        for url in ('http://example.invalid', 'https://user:secret@example.invalid/v1', 'https://example.invalid/v1?key=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                s.configure(self.data, url, 'test', self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())

    def test_symlink_paths_and_outside_outputs_are_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.data, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            s.configure(link, 'https://example.invalid/v1', 'test', self.root / 'bad')
        with self.assertRaisesRegex(ValueError, 'inside the project'):
            s.fresh(PROJECT.parent / 'must-not-create')
        self.assertFalse((PROJECT.parent / 'must-not-create').exists())

    def test_import_is_independent_and_source_is_unchanged(self):
        source, lock = self.resources()
        before = {p.relative_to(source): s.digest(p) for p in source.rglob('*') if p.is_file()}
        with patch.object(s, 'LOCK', lock):
            result = s.import_evaluation(source, self.root / 'independent')
            self.assertEqual(result['files'], 121)
            for name, sha in before.items():
                self.assertEqual(s.digest(source / name), sha)
                a, b = (source / name).stat(), (self.root / 'independent' / name).stat()
                self.assertNotEqual((a.st_dev, a.st_ino), (b.st_dev, b.st_ino))
            source.rename(self.root / 'source-unavailable')
            self.assertEqual(s.verify_evaluation(self.root / 'independent')['status'], 'PASS')

    def test_resource_tampering_missing_and_extra_are_rejected(self):
        source, lock = self.resources()
        with patch.object(s, 'LOCK', lock):
            evaluator = source / 'evaluate.py'
            before = evaluator.read_bytes()
            evaluator.write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                s.verify_evaluation(source)
            evaluator.unlink()
            with self.assertRaisesRegex(ValueError, 'file set mismatch'):
                s.verify_evaluation(source)
            evaluator.write_bytes(before)
            (source / 'unexpected').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'file set mismatch'):
                s.import_evaluation(source, self.root / 'must-not-create')
        self.assertFalse((self.root / 'must-not-create').exists())

    def test_resource_symlinks_are_rejected(self):
        source, lock = self.resources()
        p = source / 'evaluate.py'
        backup = self.root / 'evaluator-backup'
        p.rename(backup)
        p.symlink_to(backup)
        with patch.object(s, 'LOCK', lock), self.assertRaisesRegex(ValueError, 'symlink'):
            s.verify_evaluation(source)

    def test_scoring_environment_has_no_keys_or_user_pythonpath(self):
        output = self.root / 'env'
        output.mkdir()
        with patch.dict(os.environ, {'EVIWEAVE_API_KEY': 'secret', 'DASHSCOPE_API_KEY': 'borrowed', 'PYTHONPATH': '/untrusted', 'HOME': '/untrusted'}, clear=True):
            env = s.clean_env(output)
        self.assertFalse(any('KEY' in k for k in env))
        self.assertEqual(env['PYTHONPATH'], '')
        self.assertEqual(env['HOME'], str(output / 'home'))

    def test_scoring_timeout_keeps_private_report(self):
        source, lock = self.resources()
        predictions = self.root / 'predictions'
        predictions.mkdir()
        with patch.object(s, 'LOCK', lock), patch.object(s.subprocess, 'run', side_effect=s.subprocess.TimeoutExpired('frozen-evaluator', 300)):
            with self.assertRaises(s.subprocess.TimeoutExpired):
                s.score(source, predictions, self.root / 'score')
        report = json.loads((self.root / 'score/provenance.json').read_text())
        self.assertEqual(report['timeout_seconds'], 300)
        self.assertEqual(report['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
