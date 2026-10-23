"""Handwritten lifecycle and crash fixtures; never invoke a model or private data."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import standalone as s
import experiment as cli
import experiment_config as cfg
import experiment_store as store
import experiment_scheduler as sched
import experiment_verify as ver
import environment_lock as el


class FakeRunner:
    def __init__(self, outcomes=None, delay=0):
        self.outcomes = list(outcomes or [])
        self.calls = []
        self.cancelled = False
        self.delay = delay
        self.active = set()
        self.overlap = False
        self.lock = threading.Lock()

    def cancel(self):
        self.cancelled = True

    def run(self, task, root):
        with self.lock:
            self.overlap |= task.task_id in self.active
            self.active.add(task.task_id)
            self.calls.append((task.task_id, root))
            outcome = self.outcomes.pop(0) if self.outcomes else 'submitted'
        if self.delay:
            time.sleep(self.delay)
        if outcome == 'crash':
            raise RuntimeError('artificial worker exception')
        status = 'stopped' if self.cancelled else outcome
        (root / 'output').mkdir(exist_ok=True)
        prediction = root / 'output/prediction.csv'
        prediction.write_text('code\n001\n')
        usage = {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 8} if len(self.calls) % 2 else {}
        run = {'task_id': task.task_id, 'status': status, 'return_code': 0 if status == 'submitted' else 1,
               'usage': usage, 'model_turns': 1, 'wall_time_seconds': 0.01}
        store.write(root / 'run.json', run)
        events = [{'event': 'run_started', 'task_id': task.task_id}]
        if status == 'submitted':
            events.append({'event': 'prediction_validated', 'prediction': {'sha256': store.sha(prediction)}})
        events.append({'event': 'run_finished', 'status': status, 'return_code': run['return_code']})
        (root / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (root / 'stdout.log').write_text('')
        (root / 'stderr.log').write_text('')
        if status == 'auth':
            (root / 'stdout.log').write_text(json.dumps({'type':'turn.failed','error':{'code':'invalid_api_key'}})+'\n')
        if status == 'tool401':
            (root / 'stdout.log').write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution','output':'401 unauthorized'}})+'\n')
        with self.lock:
            self.active.remove(task.task_id)


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        parent = PROJECT / '.audit/unified-unit-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / 'dataset'
        for number in (1, 2, 3):
            task = self.dataset / f'input/task_{number}'
            (task / 'context').mkdir(parents=True)
            (task / 'task.json').write_text(json.dumps({'task_id':f'task_{number}', 'question':'artificial fixture'}))
            (task / 'context/record.csv').write_text('a\n1\n')
        settings = self.root / 'settings'
        s.configure(self.dataset, 'https://fixture.example/v1', 'fixture-model', settings)
        self.config = settings / 'codex.yaml'
        # Public member names with artificial content, no real evaluator or gold.
        self.evaluation = self.root / 'evaluation'
        self.lock = json.loads((PROJECT / 'provenance/evaluation-lock.json').read_text())
        public = sorted(n for n in self.lock['files'] if n.startswith('configs/'))[:3]
        for number, old in enumerate(public, 1):
            task = Path(old).stem
            self.lock['files'][f'configs/task_{number}.json'] = self.lock['files'].pop(old)
            self.lock['files'][f'output/task_{number}/gold.csv'] = self.lock['files'].pop(f'output/{task}/gold.csv')
        for name in self.lock['files']:
            path = self.evaluation / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('artificial:' + name)
            self.lock['files'][name] = store.sha(path)
        self.lock['evaluator_sha256'] = self.lock['files']['evaluate.py']
        lockfile = self.root / 'evaluation-lock.json'
        store.write(lockfile, self.lock)
        self.patch = patch.object(s, 'LOCK', lockfile)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.experiment = self.root / 'experiment'
        self.experiment.mkdir()

    def prepared(self, tasks=(1, 2)):
        def environment(config):
            profile = {'format_version': 1, 'fixture': 'no runtime or model'}
            return {'profile_id':'fixture', 'profile': profile, 'profile_sha256': store.fingerprint(profile)}
        return cfg.prepare(self.config, self.dataset, self.evaluation, list(tasks), environment)

    def initialize(self, tasks=(1, 2), repeat=1):
        prepared = self.prepared(tasks)
        self.doc = sched.initialize(self.experiment, prepared)
        self.plan = sched.add_plan(self.experiment, self.doc, list(tasks), repeat, prepared['inputs'])
        return prepared['config']

    def run_fake(self, runner, resume=False, config=None):
        from dataspace_baselines.config import load_harness_experiment_config
        config = config or load_harness_experiment_config(self.config)
        return sched.schedule(self.experiment, self.doc, self.plan, config, self.dataset, resume=resume,
                              runner_factory=lambda c: runner, doctor_fn=lambda c, o: None, live=False)

    def verify(self, passed=False):
        return ver.verify(self.experiment, scorer=lambda e,p,t,o: {'task_id':f'task_{t}', 'passed':passed})

    def test_selection_scopes_repeat_and_invalid_parameters(self):
        self.assertEqual(cfg.tasks_for(tasks=[33, 35, 33]), [33,35])
        self.assertEqual(len(cfg.tasks_for()), 60)
        self.assertEqual(cfg.tasks_for('all'), list(range(1,411)))
        for scope,tasks in [('all',[1]), (None,[0]), (None,[411])]:
            with self.assertRaises(ValueError): cfg.tasks_for(scope,tasks)
        with self.assertRaises(SystemExit): cli.parser().parse_args(['run','--scope','all','--tasks','33'])
        with self.assertRaises(ValueError): cli.parameters(cli.parser().parse_args(['run','--repeat','0']))

    def test_ordinary_failure_continues_and_resume_wrong_answer_skips(self):
        self.initialize(tasks=(1,2,3))
        first=FakeRunner(['submitted','wall_time_exhausted','submitted'])
        self.assertEqual(self.run_fake(first),1)
        self.verify(passed=False)
        second=FakeRunner()
        self.assertEqual(self.run_fake(second,resume=True),0)
        self.assertEqual([t for t,p in second.calls],['task_2'])
        self.assertEqual(self.run_fake(FakeRunner(),resume=True),0)
        self.assertEqual(len(store.attempts(self.experiment,self.doc)),4)

    def test_three_repeat_success_failure_unstarted_resume(self):
        self.initialize(tasks=(1,),repeat=3)
        first=FakeRunner(['submitted','auth'])
        self.assertEqual(self.run_fake(first),1)
        self.assertEqual(len(first.calls),2)
        second=FakeRunner()
        self.assertEqual(self.run_fake(second,resume=True),0)
        records=store.attempts(self.experiment,self.doc)
        self.assertEqual([a['repeat'] for a in records],[1,2,2,3])
        self.assertEqual(records[2]['previous_attempt'],records[1]['attempt_id'])
        self.assertEqual(len(second.calls),2)
        last=FakeRunner()
        with patch.dict(os.environ,{},clear=True): self.assertEqual(self.run_fake(last,resume=True),0)
        self.assertEqual(last.calls,[])

    def test_new_plan_failure_removes_old_current_and_keeps_history(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        prepared=self.prepared((1,))
        self.plan=sched.add_plan(self.experiment,self.doc,[1],1,prepared['inputs'])
        self.run_fake(FakeRunner(['no_prediction']))
        self.assertFalse((self.experiment/'predictions/task_1/prediction.csv').exists())
        records=store.attempts(self.experiment,self.doc)
        self.assertTrue(store.original(self.experiment,records[0]).exists())
        report=self.verify()
        self.assertEqual(report['current_plan_accuracy']['public_tasks'],1)
        self.assertEqual(report['current_plan_accuracy']['passed'],0)
        self.assertEqual(report['current_status_counts'],{'no_prediction':1})
        self.assertEqual(report['current_tasks']['1']['status'],'no_prediction')

    def test_collection_repair_no_calls_original_corruption_retries_once(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        current=self.experiment/'predictions/task_1/prediction.csv'
        current.unlink()
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(runner.calls,[])
        a=store.attempts(self.experiment,self.doc)[0]
        store.original(self.experiment,a).write_text('code\nchanged\n')
        runner=FakeRunner(['no_prediction'])
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)
        self.assertFalse(current.exists())

    def test_complete_runner_result_recovered_after_outer_crash(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        a.update(status='running',prediction_sha256=None)
        store.write(self.experiment/a['attempt_id']/'attempt.json',a)
        (self.experiment/'selection.json').unlink()
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(runner.calls,[])
        self.assertEqual(store.attempts(self.experiment,self.doc)[0]['status'],'submitted')

    def test_nonobject_event_evidence_is_retried_without_acceptance(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        (self.experiment/a['attempt_id']/'events.jsonl').write_text('null\n')
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)

    def test_incomplete_event_evidence_is_not_recovered(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        a.update(status='running')
        store.write(self.experiment/a['attempt_id']/'attempt.json',a)
        (self.experiment/a['attempt_id']/'events.jsonl').write_text('{}\n')
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)

    def test_orphan_attempt_number_is_never_reused(self):
        self.initialize(tasks=(1,))
        (self.experiment/'tasks/task_1/run_1').mkdir(parents=True)
        runner=FakeRunner()
        self.run_fake(runner)
        self.assertEqual(runner.calls[0][1].name,'run_2')

    def test_structured_auth_stops_but_tool_401_continues(self):
        self.initialize(tasks=(1,2,3))
        runner=FakeRunner(['tool401','submitted','submitted'])
        self.run_fake(runner)
        self.assertEqual(len(runner.calls),3)
        runner=FakeRunner(['auth'])
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)

    def test_concurrency_is_bounded_and_same_task_rounds_never_overlap(self):
        config=self.initialize(repeat=3)
        runner=FakeRunner(delay=0.02)
        self.run_fake(runner,config=replace(config,concurrency=2))
        self.assertEqual(len(runner.calls),6)
        self.assertFalse(runner.overlap)

    def test_lock_excludes_other_writer_and_releases_after_process_death(self):
        self.initialize()
        with store.locked(self.experiment):
            with self.assertRaisesRegex(ValueError,'in use'):
                with store.locked(self.experiment): pass
        with store.locked(self.experiment): pass

    def test_root_plan_and_attempt_association_damage_rejected(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        a['slot']='2:1'
        store.write(self.experiment/a['attempt_id']/'attempt.json',a)
        with self.assertRaisesRegex(ValueError,'slot'): store.attempts(self.experiment,self.doc)
        (self.experiment/f'plans/{self.plan["plan_id"]}/plan.json').write_text('{}')
        with self.assertRaises((ValueError,KeyError)): store.load(self.experiment)

    def test_input_snapshot_detects_file_addition_and_change(self):
        self.initialize(tasks=(1,))
        initial=self.prepared((1,))['inputs']
        (self.dataset/'input/task_1/context/record.csv').write_text('a\n2\n')
        self.assertNotEqual(self.prepared((1,))['inputs'],initial)
        (self.dataset/'input/task_1/context/extra').write_text('x')
        self.assertIn('context/extra',self.prepared((1,))['inputs']['1'])

    def test_offline_verify_detached_from_original_data_config_and_scoring(self):
        self.initialize(tasks=(1,2),repeat=2)
        self.run_fake(FakeRunner())
        self.dataset.rename(self.root/'unavailable-dataset')
        self.config.unlink()
        self.evaluation.rename(self.root/'unavailable-scoring')
        report=self.verify(passed=True)
        self.assertEqual(report['current_plan_accuracy']['public_tasks'],2)
        self.assertEqual(report['current_plan_accuracy']['accuracy'],1)
        self.assertEqual(report['verification_model_calls'],0)
        for name in ('summary.json','score.json'):
            self.assertEqual(store.read(self.experiment/name)['report_id'],report['report_id'])

    def test_usage_missing_and_subfields_not_added_and_amount_unknown(self):
        report=ver.usage_summary([{'usage':None},{'usage':{'input_tokens':10,'cached_input_tokens':8,'output_tokens':4,'reasoning_output_tokens':3}}])
        self.assertEqual(report['fields']['input_tokens']['recorded_sum'],10)
        self.assertEqual(report['fields']['cache_write_input_tokens']['recorded_sum'],None)
        self.assertEqual(report['fields']['input_tokens']['coverage'],1)
        self.assertIsNone(report['amount'])

    def test_unstarted_rows_denominator_and_history_reports(self):
        self.initialize(tasks=(1,2,3),repeat=2)
        self.run_fake(FakeRunner(['auth']))
        report=self.verify()
        self.assertEqual(report['plan_progress']['not_started'],5)
        self.assertEqual(report['current_plan_accuracy']['public_tasks'],3)
        self.assertEqual(report['current_plan_accuracy']['accuracy'],0)
        import csv
        with (self.experiment/'summary.csv').open(newline='') as f:
            self.assertEqual(len(list(csv.DictReader(f))),6)

    def test_missing_collection_is_reported_without_historical_fallback(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        (self.experiment/'predictions/task_1/prediction.csv').unlink()
        report=self.verify(passed=True)
        self.assertEqual(report['integrity'],'FAIL')
        self.assertEqual(report['current_plan_accuracy']['accuracy'],0)

    def test_report_regeneration_and_unknown_custom_files_protected(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        first=self.verify()
        (self.experiment/'summary.json').unlink()
        second=self.verify()
        self.assertNotEqual(first['report_id'],second['report_id'])
        self.assertEqual(len(list((self.experiment/'reports').glob('*/report-set.json'))),2)
        target=self.root/'custom-report'
        target.mkdir()
        (target/'summary.json').write_text('unknown')
        with self.assertRaises(ValueError): ver.report_target(target,self.experiment)

    def test_old_format_unknown_root_and_path_protection(self):
        store.write(self.experiment/'experiment.json',{'format_version':1})
        with self.assertRaisesRegex(ValueError,'unsupported'): store.load(self.experiment)
        for path in (PROJECT,PROJECT/'src/new-results',PROJECT/'.local/new-results',PROJECT.parent/'escape'):
            with self.assertRaises(ValueError): store.output_path(path)
        link=self.root/'link';link.symlink_to(self.experiment,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'): store.output_path(link)

    def test_refusal_leaves_directory_untouched_and_never_schedules(self):
        self.initialize(tasks=(1,))
        before={p.relative_to(self.experiment):store.sha(p) for p in self.experiment.rglob('*') if p.is_file()}
        args=cli.parser().parse_args(['run','--output',str(self.experiment),'--tasks','1','--config',str(self.config),'--dataset',str(self.dataset),'--evaluation-root',str(self.evaluation)])
        with patch.object(sys.stdin,'isatty',return_value=False), patch.object(cli,'schedule') as run:
            with self.assertRaisesRegex(ValueError,'not confirmed'):
                cli.execute(args,prepare_fn=lambda c,d,e,t:self.prepared(t))
            run.assert_not_called()
        after={p.relative_to(self.experiment):store.sha(p) for p in self.experiment.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_resume_rejects_parameter_changes(self):
        for flag in (['--repeat','2'],['--scope','all'],['--tasks','1'],['--config','other']):
            args=cli.parser().parse_args(['run','--resume','--output',str(self.experiment),*flag])
            with self.assertRaises(ValueError): cli.execute(args)

    def test_lock_snapshot_tampering_blocks_offline_verify(self):
        self.initialize(tasks=(1,))
        store.write(self.experiment/'resources/environment-lock.json',{'tampered':True})
        with self.assertRaisesRegex(ValueError,'environment lock'): self.verify()


class EnvironmentLockTests(unittest.TestCase):
    def test_profile_python_binary_and_patch_drift_rejected(self):
        import platform, sysconfig
        doc={'python':{'version':platform.python_version(),'full_version':sys.version,
             'implementation':platform.python_implementation(),'machine':platform.machine(),
             'soabi':sysconfig.get_config_var('SOABI'),'sha256':store.sha(Path(sys.executable).resolve())}}
        el.check_python(doc)
        doc['python']['version']='0.0.0'
        with self.assertRaisesRegex(ValueError,'Python'): el.check_python(doc)

    def test_package_version_and_same_version_content_drift_rejected(self):
        doc={'packages':{'PyYAML':{'version':'6.0.3','files':{'fixture':'0'*64}}}}
        # Only a fixture distribution is patched, never modify installed libraries.
        fake=type('FakeDistribution',(),{'version':'0','locate_file':lambda s,p:Path('/missing')})()
        with patch.object(el.metadata,'distribution',return_value=fake):
            with self.assertRaisesRegex(ValueError,'version'): el.check_packages(doc,{'PyYAML'})
            fake.version='6.0.3'
            with self.assertRaisesRegex(ValueError,'content'): el.check_packages(doc,{'PyYAML'})

    def test_artifact_hash_is_checked_before_installer(self):
        root=PROJECT/'.audit/unified-unit-tmp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            p=Path(temp)/'fixture.whl';p.write_bytes(b'not a wheel')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                el.check_artifact(p,{'filename':p.name,'sha256':'0'*64})

class EnvironmentScriptTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT/'.audit/unified-unit-tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir();(self.root/'configs').mkdir()
        import shutil
        shutil.copyfile(PROJECT/'scripts/env.sh',self.root/'scripts/env.sh')
        shutil.copyfile(PROJECT/'configs/env.sh.example',self.root/'configs/env.sh.example')

    def bash(self,script,env=None):
        return subprocess.run(['bash','--noprofile','--norc','-c',script],cwd=self.root,
                              env=dict(PATH=os.environ['PATH'],**(env or {})),capture_output=True,text=True,timeout=20)

    def test_source_preserves_options_and_cwd_and_does_not_save_key(self):
        r=self.bash('before=$-; source scripts/env.sh; [[ "$before" = "$-" ]]; [[ "$EVIWEAVE_API_KEY" = "fixture-private" ]]; stat -c %a .local/env.sh',{'EVIWEAVE_API_KEY':'fixture-private'})
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout.strip(),'600')
        self.assertNotIn('fixture-private',(self.root/'.local/env.sh').read_text())

    def test_nonempty_private_values_override_inherited_blank_inherits(self):
        (self.root/'.local').mkdir()
        private=self.root/'.local/env.sh'
        private.write_text('EVIWEAVE_DATASET="/private/dataset"\nEVIWEAVE_API_KEY=""\n')
        r=self.bash('source scripts/env.sh; [[ "$EVIWEAVE_API_KEY" = "inherited" && "$EVIWEAVE_DATASET" = "/private/dataset" ]]; before=$(sha256sum .local/env.sh); source scripts/env.sh; [[ "$before" = "$(sha256sum .local/env.sh)" ]]',{'EVIWEAVE_API_KEY':'inherited','EVIWEAVE_DATASET':'/inherited/dataset'})
        self.assertEqual(r.returncode,0,r.stderr)

    def test_other_working_directory_and_trace_do_not_leak_key(self):
        script=f'set -x; source "{self.root}/scripts/env.sh"; set +x; [[ "$EVIWEAVE_API_KEY" = "never-print-fixture" ]]'
        r=self.bash(script,{'EVIWEAVE_API_KEY':'never-print-fixture'})
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('never-print-fixture',r.stdout+r.stderr)


class AdditionalLifecycleTests(UnifiedTests):
    # Reuse fixture helpers, exclude inherited tests from the duplicate suite below.
    def test_no_public_gold_has_null_denominator(self):
        number=410
        import shutil
        shutil.copytree(self.dataset/'input/task_1',self.dataset/f'input/task_{number}')
        (self.dataset/f'input/task_{number}/task.json').write_text(json.dumps({'task_id':f'task_{number}','question':'artificial'}))
        self.initialize(tasks=(number,))
        self.run_fake(FakeRunner())
        report=self.verify()
        self.assertIsNone(report['current_plan_accuracy']['accuracy'])
        self.assertEqual(report['current_plan_accuracy']['unscored_tasks'],1)

    def test_malformed_and_missing_original_predictions_retry(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        store.original(self.experiment,a).unlink()
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)
        a=store.attempts(self.experiment,self.doc)[-1]
        store.original(self.experiment,a).write_bytes(b'\xff')
        runner=FakeRunner()
        self.run_fake(runner,resume=True)
        self.assertEqual(len(runner.calls),1)

    def test_disk_error_preserves_previous_complete_report_version(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        previous=self.verify()
        original=ver.atomic_bytes
        def fail(path,value):
            if path == self.experiment/'summary.csv': raise OSError('artificial disk failure')
            return original(path,value)
        with patch.object(ver,'atomic_bytes',side_effect=fail):
            with self.assertRaises(OSError): self.verify()
        old=self.experiment/f'reports/{previous["report_id"]}'
        self.assertTrue((old/'report-set.json').is_file())
        self.assertEqual(store.read(old/'summary.json')['report_id'],previous['report_id'])
        report=self.verify()
        for name in ('summary.json','score.json'):
            self.assertEqual(store.read(self.experiment/name)['report_id'],report['report_id'])

    def test_input_snapshot_publication_window_preserves_committed_plan(self):
        self.initialize(tasks=(1,))
        prepared=self.prepared((1,2))
        original=sched.write
        def fail(path,value):
            if path == self.experiment/'experiment.json': raise OSError('artificial crash')
            return original(path,value)
        with patch.object(sched,'write',side_effect=fail):
            with self.assertRaises(OSError): sched.add_plan(self.experiment,self.doc,[1,2],1,prepared['inputs'])
        committed=store.load(self.experiment)
        self.assertEqual(store.plan_for(self.experiment,committed)['tasks'],[1])

    def test_completed_model_result_recovers_outer_collection_error(self):
        self.initialize(tasks=(1,))
        self.run_fake(FakeRunner())
        a=store.attempts(self.experiment,self.doc)[0]
        a['status']='execution_error'
        a['error']='artificial crash while collecting completed prediction'
        store.write(self.experiment/a['attempt_id']/'attempt.json',a)
        (self.experiment/'predictions/task_1/prediction.csv').unlink()
        runner=FakeRunner()
        self.assertEqual(self.run_fake(runner,resume=True),0)
        self.assertEqual(runner.calls,[])
        self.assertEqual(store.attempts(self.experiment,self.doc)[0]['status'],'submitted')

    def test_cancel_during_worker_stops_dispatch_and_preserves_progress(self):
        self.initialize(tasks=(1,2,3))
        class SignalRunner(FakeRunner):
            def run(inner,task,root):
                os.kill(os.getpid(),signal.SIGINT)
                super(SignalRunner,inner).run(task,root)
        runner=SignalRunner(delay=0.05)
        self.assertEqual(self.run_fake(runner),130)
        self.assertEqual(len(runner.calls),1)
        self.assertTrue(runner.cancelled)
        last=FakeRunner()
        self.assertEqual(self.run_fake(last,resume=True),0)
        self.assertEqual(len(last.calls),3)

    def test_global_sandbox_failure_is_confirmed_before_stopping(self):
        config=self.initialize(tasks=(1,2,3))
        runner=FakeRunner(['runtime_error'])
        def diagnosis(config,path):
            if path.name.startswith('recheck'): raise RuntimeError('synthetic unavailable sandbox')
        code=sched.schedule(self.experiment,self.doc,self.plan,config,self.dataset,runner_factory=lambda c:runner,doctor_fn=diagnosis,live=False)
        self.assertEqual(code,1)
        self.assertEqual(len(runner.calls),1)

    def test_live_run_missing_key_is_zero_calls_but_no_pending_resume_needs_no_key(self):
        config=self.initialize(tasks=(1,))
        runner=FakeRunner()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'EVIWEAVE_API_KEY'):
                sched.schedule(self.experiment,self.doc,self.plan,config,self.dataset,runner_factory=lambda c:runner)
        self.assertEqual(runner.calls,[])
        self.run_fake(runner)
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(sched.schedule(self.experiment,self.doc,self.plan,config,self.dataset,resume=True,
                             runner_factory=lambda c:(_ for _ in ()).throw(AssertionError('must not construct'))),0)

    def test_run_identity_drift_is_rejected_before_scheduling(self):
        self.initialize(tasks=(1,))
        args=cli.parser().parse_args(['run','--resume','--yes','--output',str(self.experiment)])
        prepared=self.prepared((1,))
        prepared['identity']['environment_sha256']='0'*64
        with patch.object(cli,'schedule') as runner:
            with self.assertRaisesRegex(ValueError,'identity differs'):
                cli.execute(args,prepare_fn=lambda *a:prepared)
            runner.assert_not_called()

    def test_nested_symlink_and_unknown_root_file_rejected(self):
        self.initialize(tasks=(1,))
        p=self.experiment/'resources/link';p.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError,'symlink'): store.load(self.experiment)
        p.unlink();(self.experiment/'unknown').write_text('not owned')
        with self.assertRaisesRegex(ValueError,'unknown'): store.load(self.experiment)

    def test_lock_released_when_owner_process_is_killed(self):
        self.initialize(tasks=(1,))
        code='import fcntl,sys,time; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); time.sleep(30)'
        p=subprocess.Popen([sys.executable,'-B','-c',code,str(self.experiment/'.experiment.lock')],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(),'ready')
            with self.assertRaises(ValueError):
                with store.locked(self.experiment): pass
        finally:
            p.kill();p.wait();p.stdout.close()
        with store.locked(self.experiment): pass


# unittest otherwise repeats inherited tests; helpers are retained as fixture methods.
for _name in list(UnifiedTests.__dict__):
    if _name.startswith('test_'):
        setattr(AdditionalLifecycleTests,_name,None)

class AdditionalLockTests(unittest.TestCase):
    def test_host_binary_os_and_runtime_inventory_drift_fail_closed(self):
        import platform
        doc=store.read(PROJECT/'locks/cpython312-linux-x86_64.json')
        doc['host']['release']='artificial-different-kernel'
        with self.assertRaisesRegex(ValueError,'OS/kernel'):
            el.check_host(doc)
        doc['host']={'system':platform.system(),'release':platform.release(),'machine':platform.machine(),'os_release':store.sha('/etc/os-release')}
        doc['tools']['bwrap']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'bwrap'):
            el.check_host(doc)
        # Only fabricated inventory return values change; runtime files stay untouched.
        sys.path.insert(0,str(PROJECT/'scripts'))
        import runtime_support
        from dataspace_baselines.config import load_harness_experiment_config
        import yaml
        raw=yaml.safe_load((PROJECT/'configs/codex.example.yaml').read_text())
        parent=PROJECT/'.audit/unified-unit-tmp';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            p=Path(temp)/'config.yaml'
            raw['experiment']['workbench']['rootfs_path']=str(PROJECT/'.runtimes/data-workbench-1.0/rootfs')
            raw['harnesses']['codex']['runtime_path']=str(PROJECT/'.runtimes/codex-{version}/platform-package/vendor/x86_64-unknown-linux-musl')
            store.write(p,raw)
            config=load_harness_experiment_config(p)
            doc=store.read(PROJECT/'locks/cpython312-linux-x86_64.json')
            # Real runtime presence is not required in CI. Supply a read-only artificial inventory.
            inv=PROJECT/'.runtimes/codex-0.145.0/runtime-files.json'
            original_sha=el.sha
            original_read=el.read
            def fake_sha(path):
                if Path(path)==inv: return doc['runtime']['codex_inventory_sha256']
                return original_sha(path)
            def fake_read(path):
                if Path(path)==inv: return {'schema':1,'entries':[]}
                return original_read(path)
            with patch.object(Path,'is_file',return_value=True), patch.object(el,'sha',side_effect=fake_sha), patch.object(el,'read',side_effect=fake_read), patch.object(runtime_support,'inventory',return_value={'schema':1,'entries':[{'path':'changed-binary','sha256':'0'*64}]}):
                with self.assertRaisesRegex(ValueError,'full runtime contents'):
                    el.check_runtime(config,doc)

    def test_git_exclusion_is_literal_and_preserves_existing_rules(self):
        parent=PROJECT/'.audit/unified-unit-tmp';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            root=Path(temp);(root/'.git/info').mkdir(parents=True)
            exclude=root/'.git/info/exclude';exclude.write_text('# keep existing\nold/\n')
            with patch.object(store,'PROJECT',root):
                store.exclude_output(root/'results [v1]* space')
                store.exclude_output(root/'other')
                text=exclude.read_text()
                self.assertTrue(text.startswith('# keep existing\nold/\n'))
                self.assertIn(r'/results\ \[v1\]\*\ space/',text)
                self.assertIn('/other/',text)
                with self.assertRaisesRegex(ValueError,'control'):
                    store.output_path(root/'bad\nline')

    def test_profiles_and_generic_ci_dependency_versions_are_exact(self):
        for version,role in [('312','reproduction'),('313','compatibility')]:
            p=PROJECT/f'locks/cpython{version}-linux-x86_64'
            doc=store.read(p.with_suffix('.json'))
            self.assertEqual(doc['role'],role)
            for name,item in doc['packages'].items():
                self.assertIn(f'{name}=={item["version"]} --hash=sha256:{item["sha256"]}',p.with_suffix('.txt').read_text())
        self.assertIn('PyYAML==6.0.3',(PROJECT/'locks/ci-tools.txt').read_text())


if __name__ == '__main__':
    unittest.main()
