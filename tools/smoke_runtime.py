#!/usr/bin/env python3
"""Run synthetic tasks through the frozen sandbox runner; never invoke a model."""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

from dataspace_baselines import cli
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.harnesses import build_harness

PROJECT = Path(__file__).resolve().parents[1]
EXECUTOR_MOUNT = '/mnt/runtime/eviweave-synthetic.py'


def local(path):
    path = Path(path).resolve()
    if path == PROJECT or not path.is_relative_to(PROJECT):
        raise ValueError('diagnostic outputs must be inside the project')
    return path


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def diagnostic_runner(config):
    if config.model.openai_base_url != 'https://example.invalid/v1' or config.model.anthropic_base_url:
        raise ValueError('synthetic diagnostic accepts only https://example.invalid/v1 and no Anthropic URL')
    if not config.harnesses['codex'].options.get('simple_fixes'):
        raise ValueError('diagnostic requires the frozen exporter binding (simple_fixes=true)')
    for path in [config.workbench.rootfs_path,
                 Path(config.harnesses['codex'].options['runtime_path'].replace('{version}', '0.145.0'))]:
        local(path)
    runner = build_harness('codex', config)
    original_factory = runner.invocation_factory
    runner.resume_identity['diagnostic'] = {'mode': 'synthetic/mock', 'model_calls': 0,
        'executor_sha256': digest(PROJECT/'tools/synthetic_executor.py')}
    def factory(task, run_root, output_root):
        # Only the deliberately invalid key exists while generating the original invocation.
        original_env = dict(os.environ)
        try:
            os.environ.clear(); os.environ.update({config.model.api_key_env: 'synthetic-invalid-no-model-call', 'LANG': 'C.UTF-8'})
            invocation = original_factory(task, run_root, output_root)
        finally:
            os.environ.clear(); os.environ.update(original_env)
        command = list(invocation.command)
        boundary = command.index('--')
        if command[boundary+1] != config.harnesses['codex'].executable or command[boundary+2] != 'exec':
            raise ValueError('unexpected frozen Codex invocation')
        command[boundary:] = ['--ro-bind', str(PROJECT/'tools/synthetic_executor.py'), EXECUTOR_MOUNT,
                             '--', '/usr/local/bin/python3', EXECUTOR_MOUNT]
        # Offline diagnostic adds a network namespace; the production jail is unchanged.
        command.insert(1, '--unshare-net')
        if command[-3:] != ['--', '/usr/local/bin/python3', EXECUTOR_MOUNT]:
            raise ValueError('refusing to start a non-synthetic command')
        metadata = dict(invocation.metadata, diagnostic={'mode': 'synthetic/mock', 'model_calls': 0})
        (run_root/'synthetic-invocation.json').write_text(json.dumps({'command': command, 'mode': 'synthetic/mock', 'model_calls': 0},ensure_ascii=False,indent=2)+'\n')
        return replace(invocation, command=tuple(command), metadata=metadata,
                       environment={config.model.api_key_env: 'synthetic-invalid-no-model-call', 'LANG': 'C.UTF-8'},
                       inherit_environment=False)
    runner.invocation_factory = factory
    return runner


def validate_attempt(attempt, number):
    run = json.loads((attempt/'run.json').read_text())
    if run['status'] != 'submitted': raise ValueError(f'synthetic task failed: {attempt}: {run}')
    expected = PROJECT/'examples/synthetic/expected'/f'task_{number}.csv'
    actual = attempt/'output/prediction.csv'
    metadata = json.loads((attempt/'output/export-metadata.json').read_text())
    if not digest(actual) == digest(expected) == metadata['sha256']:
        raise ValueError('exported CSV differs from expected bytes or exporter digest')
    for name in ['events.jsonl', 'stdout.log', 'stderr.log', 'synthetic-invocation.json']:
        if not (attempt/name).is_file(): raise ValueError('missing '+name)
    if run['usage'] != {}: raise ValueError('synthetic diagnostic must not claim real usage')
    return {'task': number, 'sha256': digest(actual), 'status': run['status'], 'usage': 'no model call'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args(argv)
    output = local(args.output_root)
    if output.exists() and any(output.iterdir()): raise ValueError('output root must be new or empty')
    output.mkdir(parents=True, exist_ok=True)
    config = load_harness_experiment_config(args.config)
    # Validate config before any CLI entry can be invoked.
    diagnostic_runner(config)
    original_builder = cli.build_harness
    def builder(key, supplied):
        if key != 'codex': raise ValueError('only Codex diagnostic is supported')
        return diagnostic_runner(supplied)
    cli.build_harness = builder
    try:
        common = ['--config', str(args.config.resolve())]
        single = output/'single'
        code = cli.main(['run', *common, '--task-dir', str(PROJECT/'examples/synthetic/tasks/task_1'), '--run-dir', str(single)])
        if code: raise ValueError(f'synthetic single failed: {code}')
        records = [validate_attempt(single, 1)]
        batch = output/'batch'
        arguments = ['batch', *common, '--input-root', str(PROJECT/'examples/synthetic/tasks'), '--run-root', str(batch), '--concurrency', '1']
        if cli.main(arguments): raise ValueError('synthetic batch failed')
        before = sorted(str(p.relative_to(batch)) for p in batch.glob('tasks/task_*/run_*/run.json'))
        for number in (1, 2):
            attempt = batch/f'tasks/task_{number}/run_1'
            records.append(validate_attempt(attempt, number))
            if digest(attempt/'output/prediction.csv') != digest(batch/f'predictions/task_{number}/prediction.csv'):
                raise ValueError('collected prediction differs')
        if cli.main([*arguments, '--resume']): raise ValueError('synthetic resume failed')
        after = sorted(str(p.relative_to(batch)) for p in batch.glob('tasks/task_*/run_*/run.json'))
        if before != after or len(before) != 2: raise ValueError('resume created extra attempts')
        identity = json.loads((batch/'experiment.json').read_text())
        summary = json.loads((batch/'batch_summary.json').read_text())
        if summary['status_counts'] != {'submitted': 2}: raise ValueError('incorrect batch status')
        result = {'status': 'PASS', 'mode': 'synthetic/mock', 'model_calls': 0, 'checks': records,
                  'resume_new_attempts': 0, 'installed_package': str(Path(cli.__file__).resolve()),
                  'network': 'unshare-net', 'experiment': identity}
        (output/'smoke-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status': 'PASS', 'mode': 'synthetic/mock', 'model_calls': 0, 'resume_new_attempts': 0}))
    finally:
        cli.build_harness = original_builder


if __name__ == '__main__':
    raise SystemExit(main())
