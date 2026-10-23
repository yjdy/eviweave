#!/usr/bin/env python3
"""Offline integration of unified scheduling with the real Bubblewrap/exporter.

This developer-only tool substitutes a local executor under an additional network
namespace. It cannot run benchmark tasks or start the Codex model process.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from experiment_config import prepare
from experiment_scheduler import add_plan, doctor, initialize, schedule
from experiment_store import attempts, load, locked, read, sha, write
from experiment_verify import verify
from standalone import PROJECT, clean_env, fresh


def synthetic_runner(config):
    from dataspace_baselines.harnesses import build_harness
    runner = build_harness('codex', config)
    original = runner.invocation_factory
    executor = PROJECT / 'tools/synthetic_executor.py'
    mount = '/mnt/runtime/eviweave-synthetic.py'

    def invocation(task, run_root, output_root):
        expected = PROJECT / f'examples/synthetic/tasks/{task.task_id}/context'
        if task.task_id not in ('task_1', 'task_2') or not expected.is_dir():
            raise ValueError('only shipped artificial task IDs are accepted')
        files = lambda root: {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*') if p.is_file()}
        if files(task.context_dir) != files(expected):
            raise ValueError('synthetic integration accepts only the shipped artificial input bytes')
        result = original(task, run_root, output_root)
        command = list(result.command)
        boundary = command.index('--')
        command[boundary:] = ['--ro-bind', str(executor), mount, '--', '/usr/local/bin/python3', mount]
        command.insert(1, '--unshare-net')
        if command[-3:] != ['--', '/usr/local/bin/python3', mount]:
            raise ValueError('refusing non-synthetic command')
        write(run_root / 'synthetic-invocation.json', {'model_calls':0, 'command':command})
        return replace(result, command=tuple(command), environment={'LANG':'C.UTF-8'}, inherit_environment=False)

    runner.invocation_factory = invocation
    return runner


def synthetic_scorer(evaluation, prediction, task, output):
    # Real frozen evaluator, artificial reference rows. Frozen public materials
    # are independently verified by verify() but never interpreted as synthetic gold.
    output.mkdir(parents=True)
    predictions = output / f'predictions/task_{task}'
    predictions.mkdir(parents=True)
    shutil.copyfile(prediction, predictions / 'prediction.csv')
    gold = output / f'gold/task_{task}'
    gold.mkdir(parents=True)
    shutil.copyfile(PROJECT / f'examples/synthetic/expected/task_{task}.csv', gold / 'gold.csv')
    import csv
    with (gold / 'gold.csv').open() as f:
        columns = next(csv.reader(f))
    configs = output / 'configs'
    configs.mkdir()
    write(configs / f'task_{task}.json', {'schema_version':'1.0','task_id':f'task_{task}', 'order_sensitive':True,
          'columns':[{'gold_index':i,'gold_name':name,'type':'text'} for i,name in enumerate(columns)]})
    command = [sys.executable, '-B', str(evaluation / 'evaluate.py'), '--prediction-root', str(output / 'predictions'),
               '--gold-root', str(output / 'gold'), '--config-root', str(configs), '--output', str(output / 'score.json')]
    subprocess.run(command, env=clean_env(output), cwd=output, check=True, capture_output=True, timeout=300)
    result = read(output / 'score.json')
    if result['passed_task_count'] != 1:
        raise ValueError('artificial frozen scoring failed')
    return result['tasks'][0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    args = p.parse_args()
    output = fresh(args.output_root)
    output.mkdir(parents=True)
    import yaml
    raw = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
    # This address is metadata only. The command substitution below is mandatory.
    raw['experiment']['model']['openai_base_url'] = 'https://synthetic.example/v1'
    raw['experiment']['concurrency'] = 2
    raw['experiment']['workbench']['rootfs_path'] = str(PROJECT / '.runtimes/data-workbench-1.0/rootfs')
    raw['harnesses']['codex']['runtime_path'] = str(PROJECT / '.runtimes/codex-{version}/platform-package/vendor/x86_64-unknown-linux-musl')
    config_path = output / 'config.yaml'
    write(config_path, raw)
    dataset = output / 'dataset'
    dataset.mkdir()
    # Context copies, no symbolic links; runner guard accepts those registered here.
    shutil.copytree(PROJECT / 'examples/synthetic/tasks', dataset / 'input')
    # The integration runner uses the shipped tasks, snapshots use the identical copy.
    prepared = prepare(config_path, dataset, args.evaluation_root, [1,2])
    root = output / 'experiment'
    root.mkdir()
    doc = initialize(root, prepared)
    plan = add_plan(root, doc, [1,2], 2, prepared['inputs'])
    config = prepared['config']
    # Provide only an explicitly invalid fixture key, before any threads start.
    os.environ['EVIWEAVE_API_KEY'] = 'invalid-no-model-call'
    with locked(root):
        code = schedule(root,doc,plan,config,dataset,runner_factory=synthetic_runner)
        if code:
            raise ValueError('synthetic initial schedule failed')
        first = attempts(root,doc)
        if len(first) != 4 or not all(a['status'] == 'submitted' for a in first):
            raise ValueError('synthetic repeat scheduling failed')
        verify(root)
        (root / 'predictions/task_1/prediction.csv').unlink()
        def never(config):
            raise AssertionError('completed resume must not construct a runner')
        if schedule(root,doc,plan,config,dataset,resume=True,runner_factory=never) != 0:
            raise ValueError('completed resume failed')
        verify(root)
        for a in first:
            actual=root / a['attempt_id'] / 'output/prediction.csv'
            if sha(actual) != sha(PROJECT / f'examples/synthetic/expected/task_{a["task"]}.csv'):
                raise ValueError('synthetic prediction bytes differ')
        # Interrupted real Bubblewrap invocation, followed by recovery of pending slots.
        plan=add_plan(root,doc,[1,2],1,prepared['inputs'])
        def interrupted_runner(config):
            runner=synthetic_runner(config)
            original=runner.invocation_factory
            def factory(*args):
                result=original(*args)
                os.kill(os.getpid(),signal.SIGINT)
                return result
            runner.invocation_factory=factory
            return runner
        interrupted=schedule(root,doc,plan,replace(config,concurrency=1),dataset,runner_factory=interrupted_runner)
        if interrupted != 130:
            raise ValueError('synthetic cancellation did not return130')
        verify(root)
        if schedule(root,doc,plan,config,dataset,resume=True,runner_factory=synthetic_runner):
            raise ValueError('synthetic interrupted resume failed')
        report=verify(root)
        for task in (1,2):
            synthetic_scorer(root/'resources/evaluation',root/f'predictions/task_{task}/prediction.csv',task,output/f'synthetic-scoring/{task}')
        before=len(attempts(root,doc))
        if schedule(root,doc,plan,config,dataset,resume=True,runner_factory=never) or len(attempts(root,doc))!=before:
            raise ValueError('final resume created extra calls')
    write(output/'acceptance.json', {'status':'PASS','model_calls':0,'mode':'synthetic/mock; additional unshare-net',
          'initial_attempts':4,'interrupted_returncode':interrupted,'final_attempts':before,
          'completed_resume_new_calls':0,'synthetic_frozen_score':'2/2','report_id':report['report_id']})
    print(json.dumps(read(output/'acceptance.json')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
