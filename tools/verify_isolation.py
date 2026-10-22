#!/usr/bin/env python3
"""Verify installation and offline execution with an explicit source path hidden.

Uses mount/PID namespaces, never renames or chmods the real source directory.
Requires a build-capable Python, Bubblewrap and independently prepared runtimes.
No credentials or real model calls are used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from standalone import PROJECT, clean_env, digest, fresh, plain, score, task_paths, write_json


def child(args, output):
    # The real directory must be unreadable, not merely omitted from a search path.
    denied = []
    for name in ('', 'README.md', 'api.env'):
        target = args.block_path / name
        try:
            if name:
                target.read_bytes()
            else:
                list(target.iterdir())
        except (PermissionError, FileNotFoundError):
            denied.append(name or '.')
        else:
            raise ValueError('blocked source remains accessible')
    if (args.block_path.stat().st_mode & 0o777) != 0:
        raise ValueError('blocked mount has unexpected permissions')
    write_json(output / 'denial.json', {'status': 'PASS', 'denied': denied,
        'blocked_mode': '000', 'pid_namespace': os.readlink('/proc/self/ns/pid'),
        'mount_namespace': os.readlink('/proc/self/ns/mnt'), 'pid_namespace_init': Path('/proc/1/comm').read_text().strip()})
    if args.settings:
        try:
            task_paths(args.settings, 33)
        except (OSError, ValueError):
            write_json(output / 'configured-source-denial.json', {'status': 'PASS',
                'reason': 'currently configured external dataset is unavailable; no automatic fallback'})
        else:
            raise ValueError('expected the currently configured external dataset to be blocked')
    report = {'status': 'FAIL', 'model_calls': 0, 'real_benchmark_tasks_executed': 0, 'steps': []}
    env = clean_env(output)
    env.update(PIP_CACHE_DIR=str(output / 'cache/pip'), UV_CACHE_DIR=str(output / 'cache/uv'))

    def run(command, name, timeout=300):
        command = [str(c) for c in command]
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        report['steps'].append({'step': name, 'returncode': result.returncode, 'command': command})
        if result.returncode:
            raise ValueError(name + ' failed; see private log')

    try:
        run([sys.executable, '-B', PROJECT / 'tools/ci_check.py', '--output-root', output / 'ci'], 'ci', 1200)
        py = output / 'ci/wheel-env/bin/python'
        run([py, '-B', '-m', 'dataspace_baselines', 'doctor', '--offline', '--config', args.config,
             '--temp-dir', output / 'doctor-tmp'], 'doctor')
        run([py, '-B', PROJECT / 'tools/smoke_runtime.py', '--config', args.config,
             '--output-root', output / 'synthetic'], 'synthetic')
        run([py, '-B', PROJECT / 'tools/package_evidence.py', 'verify', '--archive', args.archive,
             '--output-root', output / 'evidence-verify'], 'evidence-verify')
        # The accepted verifier checks every member before any selected extraction.
        predictions = output / 'historical-predictions'
        predictions.mkdir()
        with zipfile.ZipFile(args.archive) as archive:
            expected_score = json.loads(archive.read('evaluation/public60.json'))
            for number in range(1, 411):
                target = predictions / f'task_{number}/prediction.csv'
                target.parent.mkdir()
                target.write_bytes(archive.read(f'predictions/task_{number}/prediction.csv'))
        # score() uses this build Python (3.12.3), not the workbench's Python.
        result = score(args.evaluation_root, predictions, output / 'historical-score')
        actual = json.loads(Path(result['score_file']).read_text())
        if actual != expected_score or (actual['task_count'], actual['passed_task_count']) != (60, 45):
            raise ValueError('independent historical scoring differs from accepted per-task evidence')
        report['historical_score'] = result
        # Run freshly exported synthetic predictions through the frozen evaluator too.
        reference = output / 'synthetic-reference'
        (reference / 'configs').mkdir(parents=True)
        for number in (1, 2):
            task = f'task_{number}'
            gold = reference / 'gold' / task
            gold.mkdir(parents=True)
            shutil.copyfile(PROJECT / f'examples/synthetic/expected/{task}.csv', gold / 'gold.csv')
            import csv
            with (gold / 'gold.csv').open() as handle:
                names = next(csv.reader(handle))
            write_json(reference / f'configs/{task}.json', {'schema_version': '1.0', 'task_id': task,
                'order_sensitive': True, 'columns': [{'gold_index': i, 'gold_name': name, 'type': 'text'} for i, name in enumerate(names)]})
        run([sys.executable, '-B', args.evaluation_root / 'evaluate.py', '--prediction-root',
             output / 'synthetic/batch/predictions', '--gold-root', reference / 'gold',
             '--config-root', reference / 'configs', '--output', output / 'synthetic-score.json'], 'synthetic-score')
        synthetic = json.loads((output / 'synthetic-score.json').read_text())
        if (synthetic['task_count'], synthetic['passed_task_count']) != (2, 2):
            raise ValueError('fresh synthetic export failed frozen scoring')
        report['synthetic_score'] = {'passed': 2, 'total': 2}
        run([py, '-B', PROJECT / 'tools/verify_release.py', '--git-scope', 'worktree',
             '--archive-root', output / 'ci/archives', '--output', output / 'release.json'], 'release')
        report['status'] = 'PASS'
    finally:
        write_json(output / 'acceptance.json', report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block-path', type=Path, required=True)
    parser.add_argument('--evaluation-root', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True, help='offline placeholder config')
    parser.add_argument('--settings', type=Path, help='optional current external data settings; must fail when hidden')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.inside:
            args.block_path = args.block_path.absolute()
            output = plain(args.output_root)
            child(args, output)
            return 0
        output = fresh(args.output_root)
        blocked = plain(args.block_path)
        if blocked == Path('/') or blocked.is_relative_to(PROJECT) or PROJECT.is_relative_to(blocked):
            raise ValueError('blocked source must be outside and disjoint from project')
        if not blocked.is_dir():
            raise ValueError('blocked directory does not exist')
        for name in ('evaluation_root', 'archive', 'config', 'settings'):
            value = getattr(args, name)
            if value is not None:
                value = plain(value)
                if not value.is_relative_to(PROJECT):
                    raise ValueError('acceptance resources must be independent project-local files')
                setattr(args, name, value)
        output.mkdir(parents=True, mode=0o700)
        command = ['bwrap', '--die-with-parent', '--ro-bind', '/', '/', '--bind', str(PROJECT), str(PROJECT),
                   '--tmpfs', str(blocked), '--chmod', '000', str(blocked), '--unshare-pid', '--proc', '/proc',
                   '--dev', '/dev', '--chdir', str(PROJECT), sys.executable, '-B', str(Path(__file__).resolve()),
                   '--inside', '--block-path', str(blocked), '--evaluation-root', str(args.evaluation_root),
                   '--archive', str(args.archive), '--config', str(args.config), '--output-root', str(output)]
        if args.settings:
            command += ['--settings', str(args.settings)]
        write_json(output / 'namespace.json', {'command': command, 'source_mutation': False,
            'parent_pid_namespace': os.readlink('/proc/self/ns/pid'), 'parent_mount_namespace': os.readlink('/proc/self/ns/mnt')})
        env = clean_env(output)
        with (output / 'namespace.log').open('w') as log:
            result = subprocess.run(command, cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode:
            raise ValueError('isolation acceptance failed; see ' + str(output / 'namespace.log'))
        print(json.dumps({'status': 'PASS', 'report': str(output / 'acceptance.json'), 'model_calls': 0}))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print('isolation: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
