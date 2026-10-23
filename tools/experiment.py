#!/usr/bin/env python3
"""Unified experiment check, run/resume and offline verify. Default run invokes public60."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from experiment_config import defaults, prepare, tasks_for
from experiment_scheduler import add_plan, doctor, initialize, plan_for, run_environment, schedule
from experiment_store import (attempts, exclude_output, fingerprint, load, locked, output_path,
                              uid, read, registered_inputs)
from experiment_verify import report_target, verify
from standalone import PROJECT, plain


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('check', 'run'):
        q = sub.add_parser(name)
        q.add_argument('--config', type=Path)
        q.add_argument('--dataset', type=Path)
        q.add_argument('--evaluation-root', type=Path)
        q.add_argument('--environment-profile', type=Path, help='explicit environment profile for a new experiment; resume restores its frozen snapshot')
        group = q.add_mutually_exclusive_group()
        group.add_argument('--tasks', nargs='+', type=int)
        group.add_argument('--scope', choices=('public60', 'all'))
        q.add_argument('--repeat', type=int)
        q.add_argument('--observability-profile', type=Path)
        q.add_argument('--cost-pricing', type=Path)
        if name == 'run':
            q.add_argument('--output', type=Path)
            q.add_argument('--resume', action='store_true')
            q.add_argument('--yes', action='store_true')
    v = sub.add_parser('verify')
    v.add_argument('--experiment', type=Path, required=True)
    v.add_argument('--output', type=Path)
    from cost_cli import add_parser
    add_parser(sub)
    return p


def parameters(args):
    config = args.config or defaults('EVIWEAVE_CONFIG', '.local/config-v6/codex.yaml')
    dataset = args.dataset or defaults('EVIWEAVE_DATASET', 'data/DataSpace-Benchmark')
    evaluation = args.evaluation_root or defaults('EVIWEAVE_EVALUATION_ROOT', '.local/evaluation-v6-public60')
    repeat = args.repeat if args.repeat is not None else 1
    if repeat < 1:
        raise ValueError('--repeat must be positive')
    return config, dataset, evaluation, tasks_for(args.scope, args.tasks), repeat


def confirm(args, root, plan):
    print(f'Existing experiment: {root}\nTasks: {plan["tasks"]}; repeat: {plan["repeat"]}. '
          'Previous attempts remain in history; each newly started attempt replaces that task’s current answer.', flush=True)
    if args.yes:
        return
    if not sys.stdin.isatty() or input('Continue? [y/N] ').strip().lower() not in ('y', 'yes'):
        raise ValueError('reuse not confirmed; target directory was not changed; use --yes for explicit noninteractive reuse')


def execute(args, prepare_fn=prepare, schedule_fn=schedule, verify_fn=verify):
    if args.command in ('cost', 'observe-check'):
        from cost_cli import execute as cost_execute
        return cost_execute(args)
    if getattr(args, 'observability_profile', None):
        from observability_session import require_online_profile
        require_online_profile(args.observability_profile, args.cost_pricing)
    if getattr(args, 'cost_pricing', None):
        raise ValueError('--cost-pricing requires a certified observability profile')
    if args.command == 'verify':
        root = output_path(args.experiment)
        if (root / 'observation.json').is_file():
            from cost_cli import execute as cost_execute
            args.command, args.cost_command = 'cost', 'verify'
            return cost_execute(args)
        if not root.is_dir():
            raise ValueError('experiment directory missing')
        load(root)  # Validate before creating a lock file in an unrelated directory.
        target = report_target(args.output, root) if args.output and plain(args.output) != root else None
        with locked(root):
            if target:
                target.mkdir(parents=True, exist_ok=True)
                with locked(target):
                    if not target.is_relative_to(PROJECT / '.audit'):
                        exclude_output(target)
                    report = verify_fn(root, target)
            else:
                report = verify_fn(root)
        print(json.dumps({'integrity': report['integrity'], 'report_id': report['report_id'], 'output': str(target or root)}))
        return 0 if report['integrity'] == 'PASS' else 1
    if args.command == 'check':
        config, dataset, evaluation, tasks, repeat = parameters(args)
        if prepare_fn is prepare and args.environment_profile:
            from environment_lock import profile, verify as verify_environment
            selected = profile(args.environment_profile)
            prepared = prepare_fn(config, dataset, evaluation, tasks,
                                  environment_verifier=lambda c: verify_environment(c, doc=selected))
        else:
            prepared = prepare_fn(config, dataset, evaluation, tasks)
        from public_resources import audit_root
        diagnostic = audit_root() / 'experiments' / ('check-' + uid())
        diagnostic.mkdir(parents=True)
        with run_environment(diagnostic, live=False):
            # doctor offline checks all local runtime contracts; no service authentication.
            from dataspace_baselines.doctor import run_doctor
            from experiment_store import write
            tmp = diagnostic / 'doctor-tmp'
            tmp.mkdir()
            checks = run_doctor(prepared['config'], offline=True, temp_dir=tmp)
            write(diagnostic / 'doctor.json', [dict(name=c.name, ok=c.ok, detail=c.detail, skipped=c.skipped) for c in checks])
            if not all(c.ok for c in checks):
                raise RuntimeError('local environment check failed: ' + str(diagnostic))
        present = bool(os.environ.get('EVIWEAVE_API_KEY', '').strip())
        print(json.dumps({'status': 'PASS' if present else 'MISSING_KEY', 'tasks': tasks, 'repeat': repeat,
                          'api_key_present': present, 'service_auth': 'NOT_TESTED', 'model_calls': 0,
                          'profile': prepared['environment']['profile_id'], 'diagnostics': str(diagnostic)}))
        return 0 if present else 2
    if args.resume:
        if not args.output or any(getattr(args, n) is not None for n in ('scope', 'tasks', 'repeat', 'config', 'dataset', 'evaluation_root', 'environment_profile')):
            raise ValueError('--resume requires --output and restores original tasks/repeat/configuration/data')
        root = output_path(args.output)
        before = load(root)
        plan = plan_for(root, before)
        config, dataset, evaluation = (Path(before['paths'][n]) for n in ('config', 'dataset', 'evaluation'))
        tasks, repeat = plan['tasks'], plan['repeat']
    else:
        config, dataset, evaluation, tasks, repeat = parameters(args)
        root = output_path(args.output or PROJECT / '.audit/experiments' / uid(), [config, dataset, evaluation])
        before = load(root) if root.exists() and any(root.iterdir()) else None
        plan = {'tasks': tasks, 'repeat': repeat}
    registered = registered_inputs(root, before) if before else {}
    registered_tasks = sorted(set(tasks) | {int(t) for t in registered})
    if before and prepare_fn is prepare:
        # Resume/reuse checks the experiment's frozen profile, not a mutable external lock.
        from environment_lock import verify as verify_environment
        frozen_environment = read(root / 'resources/environment-lock.json')
        if args.environment_profile:
            from environment_lock import profile
            if profile(args.environment_profile) != frozen_environment['profile']:
                raise ValueError('existing experiment environment profile differs')
        prepared = prepare_fn(config, dataset, evaluation, registered_tasks,
                              environment_verifier=lambda c: verify_environment(c, doc=frozen_environment['profile']))
    else:
        if prepare_fn is prepare and args.environment_profile:
            from environment_lock import profile, verify as verify_environment
            selected = profile(args.environment_profile)
            prepared = prepare_fn(config, dataset, evaluation, registered_tasks,
                                  environment_verifier=lambda c: verify_environment(c, doc=selected))
        else:
            prepared = prepare_fn(config, dataset, evaluation, registered_tasks)
    state_before = fingerprint({'doc': before, 'attempts': attempts(root, before)}) if before else None
    if before:
        if before['identity'] != prepared['identity']:
            raise ValueError('experiment configuration/source/environment/evaluation identity differs; use a new output directory')
        if any(prepared['inputs'].get(t) != files for t, files in registered.items()):
            raise ValueError('registered input bytes changed; use a new output directory')
        confirm(args, root, plan)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(root):
        if before:
            doc = load(root)
            if fingerprint({'doc': doc, 'attempts': attempts(root, doc)}) != state_before:
                raise ValueError('experiment changed while waiting for confirmation; retry')
        else:
            if any(p.name != '.experiment.lock' for p in root.iterdir()):
                raise ValueError('output acquired conflicting contents before lock')
            doc = initialize(root, prepared)
        if not root.is_relative_to(PROJECT / '.audit'):
            exclude_output(root)
        if not args.resume:
            plan = add_plan(root, doc, tasks, repeat, prepared['inputs'])
        code = 1
        try:
            code = schedule_fn(root, doc, plan, prepared['config'], Path(dataset), resume=args.resume)
        finally:
            # Work threads are stopped by schedule before report generation starts.
            try:
                report = verify_fn(root)
                if report['integrity'] != 'PASS':
                    code = 1 if code != 130 else code
            except Exception as exc:
                print('report generation failed: ' + str(exc), file=sys.stderr)
                code = 1 if code != 130 else code
        print('Experiment and reports: ' + str(root), flush=True)
        return code


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except (ValueError, FileNotFoundError, KeyError, ImportError) as exc:
        print('experiment: ' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('experiment failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
