"""Offline orchestration tests: fake workers only, never start a model."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import check_task33 as check
import standalone


class Task33TerminalTests(unittest.TestCase):
    def setUp(self):
        tmp = PROJECT / '.audit/task33-terminal-unit-tmp'
        tmp.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=tmp)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        task = self.root / 'data/input/task_33'
        (task / 'context').mkdir(parents=True)
        (task / 'task.json').write_text('{"task_id":"task_33","question":"artificial test"}')
        standalone.configure(self.root / 'data', 'https://example.invalid/v1', 'artificial', self.root / 'config')
        self.settings = self.root / 'config/settings.json'
        self.output = self.root / 'result'
        self.homes = []
        self.text = io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(self.text))
        self.stack.enter_context(contextlib.redirect_stderr(self.text))
        self.stack.enter_context(patch.dict(os.environ, {'EVIWEAVE_API_KEY': 'artificial-do-not-record-key', 'DASHSCOPE_API_KEY': 'borrowed-do-not-pass'}, clear=True))
        self.doctor = self.stack.enter_context(patch.object(check, 'doctor'))
        self.snapshot = self.stack.enter_context(patch.object(check, 'snapshot', return_value={'artificial': True}))
        self.worker = self.stack.enter_context(patch.object(check, 'run_trial', side_effect=self.fake_run))
        self.scorer = self.stack.enter_context(patch.object(check, 'score', side_effect=self.fake_score))

    def fake_run(self, task, config, root):
        self.assertNotIn('DASHSCOPE_API_KEY', os.environ)
        self.assertEqual(os.environ['EVIWEAVE_API_KEY'], 'artificial-do-not-record-key')
        self.homes.append(os.environ['HOME'])
        self.assertTrue(Path(os.environ['HOME']).is_relative_to(root))
        prediction = root / 'attempt/output/prediction.csv'
        prediction.parent.mkdir(parents=True)
        prediction.write_text('id,value\n001,artificial\n')
        return {'status': 'submitted', 'return_code': 0, 'model_turns': 1, 'tool_actions': 1,
                'wall_time_seconds': 1, 'usage': {'input_tokens': 2, 'output_tokens': 3}}

    def fake_score(self, evaluation, predictions, output, tasks):
        self.assertEqual(tasks, [33])
        self.assertEqual(list(predictions.iterdir())[0].name, 'task_33')
        output.mkdir()
        standalone.write_json(output / 'score.json', {'task_count': 1, 'tasks': [{'task_id': 'task_33', 'passed': False, 'error_code': 'artificial_mismatch'}]})
        return {'score_file': str(output / 'score.json')}

    def execute(self, check_only=False):
        return check.execute(self.settings, self.root / 'evaluation', self.output, check_only)

    def report(self):
        return json.loads((self.output / 'summary.json').read_text())

    def test_missing_key_never_runs_or_creates_output(self):
        del os.environ['EVIWEAVE_API_KEY']
        with self.assertRaisesRegex(ValueError, 'EVIWEAVE_API_KEY'):
            self.execute()
        self.worker.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_check_only_does_not_claim_authentication(self):
        self.assertEqual(self.execute(check_only=True), 0)
        self.worker.assert_not_called()
        self.scorer.assert_not_called()
        report = self.report()
        self.assertEqual(report['service_auth'], 'NOT_TESTED')
        self.assertTrue(report['api_key_env_readable'])
        self.assertEqual(report['status'], 'CHECK_ONLY')
        self.assertNotIn('artificial-do-not-record-key', (self.output / 'summary.json').read_text() + self.text.getvalue())

    def test_three_wrong_answers_still_run_three_fresh_trials(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.worker.call_count, 3)
        self.assertEqual(self.scorer.call_count, 3)
        self.assertEqual(len(set(self.homes)), 3)
        report = self.report()
        self.assertEqual(report['status'], 'COMPLETE')
        self.assertEqual(report['completed_scored_trials'], 3)
        self.assertEqual(report['passed_trials'], 0)
        self.assertIn('三次预测字节相同', report['conclusion'])
        self.assertEqual([r['trial'] for r in report['records']], [1, 2, 3])

    def test_infrastructure_failure_stops_and_preserves_report(self):
        self.worker.side_effect = None
        self.worker.return_value = {'status': 'runtime_error', 'return_code': 1, 'model_turns': 0, 'usage': {}}
        with self.assertRaisesRegex(ValueError, '未正常提交'):
            self.execute()
        self.assertEqual(self.worker.call_count, 1)
        self.scorer.assert_not_called()
        self.assertEqual(self.report()['status'], 'FAIL')
        self.assertEqual(self.report()['service_auth'], 'NOT_TESTED')

    def test_changed_inputs_prevent_model_call(self):
        self.snapshot.side_effect = [{'version': 1}, {'version': 2}]
        with self.assertRaisesRegex(ValueError, '发生变化'):
            self.execute()
        self.worker.assert_not_called()
        self.assertEqual(self.report()['completed_scored_trials'], 0)

    def test_interrupt_preserves_partial_evidence(self):
        self.worker.side_effect = KeyboardInterrupt
        self.assertEqual(self.execute(), 130)
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(self.report()['status'], 'INTERRUPTED')
        self.scorer.assert_not_called()

    def test_output_changes_do_not_prove_random_causation(self):
        records = [{'passed': False, 'prediction_sha256': 'a'}, {'passed': True, 'prediction_sha256': 'b'}, {'passed': False, 'prediction_sha256': 'c'}]
        message = check.conclusion(records)
        self.assertIn('通过1/3', message)
        self.assertIn('不能证明', message)
        self.assertIn('模型版本', message)


if __name__ == '__main__':
    unittest.main()
