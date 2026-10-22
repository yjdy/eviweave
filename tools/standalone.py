#!/usr/bin/env python3
"""Explicit EviWeave configuration, frozen-resource import and offline scoring.

This orchestration tool never discovers neighbouring repositories or credentials.
Business code, prompts, runtimes and export policy remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
LOCK = PROJECT / 'provenance/evaluation-lock.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plain(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink paths are not accepted: ' + str(path))
    return path.resolve()


def fresh(path):
    path = plain(path)
    if path == PROJECT or not path.is_relative_to(PROJECT):
        raise ValueError('output must be inside the project')
    if path.exists():
        raise ValueError('output already exists: ' + str(path))
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def clean_env(output, live=False):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL')}
    for name in ('tmp', 'home', 'cache'):
        (output / name).mkdir(exist_ok=True)
    env.update(HOME=str(output / 'home'), TMPDIR=str(output / 'tmp'),
               XDG_CACHE_HOME=str(output / 'cache'), PYTHONPATH='',
               PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    if live:
        key = os.environ.get('EVIWEAVE_API_KEY', '').strip()
        if not key:
            raise ValueError('set your dedicated EVIWEAVE_API_KEY; no credential fallback')
        env['EVIWEAVE_API_KEY'] = key
    return env


def read_lock():
    lock = json.loads(LOCK.read_text())
    files = lock['files']
    if len(files) != 121 or lock['evaluator_sha256'] != files.get('evaluate.py'):
        raise ValueError('invalid frozen evaluation lock')
    for name, sha in files.items():
        if (not re.fullmatch(r'(evaluate\.py|configs/task_[1-9][0-9]*\.json|output/task_[1-9][0-9]*/gold\.csv)', name)
                or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            raise ValueError('invalid evaluation member')
    return lock


def check_files(root, expected):
    root = plain(root)
    if not root.is_dir():
        raise ValueError('resource directory missing: ' + str(root))
    actual = set()
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('resource symlink: ' + str(p))
        if p.is_file():
            actual.add(p.relative_to(root).as_posix())
        elif not p.is_dir():
            raise ValueError('non-regular resource member')
    if actual != set(expected):
        raise ValueError('resource file set mismatch')
    for name, sha in expected.items():
        if digest(root / name) != sha:
            raise ValueError('resource checksum mismatch: ' + name)
    return root


def verify_evaluation(root):
    lock = read_lock()
    check_files(root, lock['files'])
    return {'status': 'PASS', 'version': lock['version'], 'files': len(lock['files']),
            'lock_sha256': digest(LOCK), 'evaluator_sha256': lock['evaluator_sha256']}


def import_evaluation(source, output):
    lock = read_lock()
    source = check_files(source, lock['files'])
    output = fresh(output)
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('source and destination overlap')
    output.mkdir(parents=True, mode=0o700)
    for name in sorted(lock['files']):
        original, target = source / name, output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy bytes, never link to the historical source or execute within it.
        shutil.copyfile(original, target)
        target.chmod(0o400)
        if original.stat().st_ino == target.stat().st_ino and original.stat().st_dev == target.stat().st_dev:
            raise ValueError('resource copy shares source inode')
    return verify_evaluation(output)


def configure(dataset, endpoint, model, output):
    dataset = plain(dataset)
    if not (dataset / 'input').is_dir():
        raise ValueError('dataset root must contain input/')
    parts = urlsplit(endpoint)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('endpoint must be an HTTPS URL without embedded credentials')
    if not model.strip():
        raise ValueError('model must be nonempty')
    output = fresh(output)
    output.mkdir(parents=True, mode=0o700)
    # JSON is valid YAML. Keep the shipped offline template's budgets and policy.
    import yaml
    config = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
    config['experiment']['model'].update(model=model, key=model,
        api_key_env='EVIWEAVE_API_KEY', openai_base_url=endpoint)
    config['experiment']['workbench']['rootfs_path'] = str(PROJECT / '.runtimes/data-workbench-1.0/rootfs')
    config['harnesses']['codex']['runtime_path'] = str(PROJECT / '.runtimes/codex-{version}/platform-package/vendor/x86_64-unknown-linux-musl')
    config_path = output / 'codex.yaml'
    write_json(config_path, config)
    settings = {'schema': 1, 'dataset_root': str(dataset), 'input_root': str(dataset / 'input'),
                'config': str(config_path), 'config_sha256': digest(config_path),
                'api_key_env': 'EVIWEAVE_API_KEY', 'credential_storage': 'environment only'}
    write_json(output / 'settings.json', settings)
    for path in output.iterdir():
        path.chmod(0o600)
    return {'status': 'PASS', 'settings': str(output / 'settings.json'),
            'credential_value_saved': False, 'dataset_mode': 'explicit path; no discovery or fallback'}


def task_paths(settings_path, task):
    settings = json.loads(plain(settings_path).read_text())
    root = plain(settings['input_root'])
    task_path = plain(root / f'task_{task}')
    if not (task_path / 'task.json').is_file() or not (task_path / 'context').is_dir():
        raise ValueError('configured task is inaccessible or missing; no dataset fallback')
    config = plain(settings['config'])
    if digest(config) != settings['config_sha256']:
        raise ValueError('configuration changed; reconfigure explicitly')
    import yaml
    parsed = yaml.safe_load(config.read_text())
    if (settings.get('api_key_env') != 'EVIWEAVE_API_KEY'
            or parsed['experiment']['model']['api_key_env'] != 'EVIWEAVE_API_KEY'):
        raise ValueError('only EVIWEAVE_API_KEY is accepted')
    return task_path, config


def run_task(settings, task, output):
    # Resolve the explicit input before creating outputs or checking credentials.
    task_path, config = task_paths(settings, task)
    output = fresh(output)
    if not os.environ.get('EVIWEAVE_API_KEY', '').strip():
        raise ValueError('set your dedicated EVIWEAVE_API_KEY; no credential fallback')
    output.mkdir(parents=True, mode=0o700)
    env = clean_env(output, live=True)
    command = [sys.executable, '-B', '-m', 'dataspace_baselines', 'run', '--config', str(config),
               '--task-dir', str(task_path), '--run-dir', str(output / 'attempt')]
    write_json(output / 'invocation.json', {'command': command, 'credential_env': 'EVIWEAVE_API_KEY',
        'config_sha256': digest(config), 'task_json_sha256': digest(task_path / 'task.json')})
    with (output / 'run.log').open('w') as log:
        process = subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1900)
    return {'status': 'PASS' if process.returncode == 0 else 'FAIL', 'returncode': process.returncode}


def score(evaluation, predictions, output, tasks=None):
    verified = verify_evaluation(evaluation)
    evaluation, predictions, output = plain(evaluation), plain(predictions), fresh(output)
    if not predictions.is_dir():
        raise ValueError('prediction directory missing')
    output.mkdir(parents=True, mode=0o700)
    # Execute a private copy, with no credentials or user Python path.
    script = output / 'evaluate.py'
    shutil.copyfile(evaluation / 'evaluate.py', script)
    configs = evaluation / 'configs'
    if tasks:
        configs = output / 'configs'
        configs.mkdir()
        for task in sorted(set(tasks)):
            shutil.copyfile(evaluation / 'configs' / f'task_{task}.json', configs / f'task_{task}.json')
    command = [sys.executable, '-B', str(script), '--prediction-root', str(predictions),
               '--gold-root', str(evaluation / 'output'), '--config-root', str(configs),
               '--output', str(output / 'score.json')]
    env = clean_env(output)
    write_json(output / 'provenance.json', dict(verified, command=command, environment_keys=sorted(env),
               model_calls=0, timeout_seconds=300, predictions=str(predictions)))
    with (output / 'score.log').open('w') as log:
        result = subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    if result.returncode:
        raise ValueError('frozen evaluator failed; see private score.log')
    # Recheck the material after executing the evaluator as well.
    verify_evaluation(evaluation)
    actual = json.loads((output / 'score.json').read_text())
    return {'status': 'PASS', 'task_count': actual['task_count'], 'passed_task_count': actual['passed_task_count'],
            'failed_task_count': actual['failed_task_count'], 'score_file': str(output / 'score.json')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    config = sub.add_parser('configure')
    config.add_argument('--dataset-root', type=Path, required=True)
    config.add_argument('--base-url', required=True)
    config.add_argument('--model', default='qwen3.8-flash')
    config.add_argument('--output-root', type=Path, required=True)
    imp = sub.add_parser('import-evaluation')
    imp.add_argument('--source', type=Path, required=True)
    imp.add_argument('--output-root', type=Path, required=True)
    ver = sub.add_parser('verify-evaluation')
    ver.add_argument('--evaluation-root', type=Path, required=True)
    run = sub.add_parser('run')
    run.add_argument('--settings', type=Path, required=True)
    run.add_argument('--task', type=int, required=True)
    run.add_argument('--output-root', type=Path, required=True)
    scoring = sub.add_parser('score')
    scoring.add_argument('--evaluation-root', type=Path, required=True)
    scoring.add_argument('--prediction-root', type=Path, required=True)
    scoring.add_argument('--task', type=int, action='append')
    scoring.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            result = configure(args.dataset_root, args.base_url, args.model, args.output_root)
        elif args.command == 'import-evaluation':
            result = import_evaluation(args.source, args.output_root)
        elif args.command == 'verify-evaluation':
            result = verify_evaluation(args.evaluation_root)
        elif args.command == 'run':
            if args.task < 1:
                raise ValueError('task must be positive')
            result = run_task(args.settings, args.task, args.output_root)
        else:
            result = score(args.evaluation_root, args.prediction_root, args.output_root, args.task)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'PASS' else 1
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        print('standalone: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
