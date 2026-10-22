#!/usr/bin/env python3
"""Run exactly three independent task-33 trials, unless infrastructure fails.

Uses current frozen source and project-local inputs. No previous predictions,
scores or gold results are passed to the model. --check-only never calls it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

from standalone import PROJECT, clean_env, digest, fresh, plain, score, task_paths, verify_evaluation, write_json


@contextmanager
def private_environment(root):
    original = dict(os.environ)
    env = clean_env(root, live=True)
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def snapshot(task, config, evaluation):
    modules = json.loads((PROJECT / 'provenance/extraction-manifest.json').read_text())['files']
    sources = {}
    for entry in modules:
        sha = digest(PROJECT / entry['destination'])
        if sha != entry['release_sha256']:
            raise ValueError('冻结业务模块摘要变化：' + entry['destination'])
        sources[entry['destination']] = sha
    inputs = {}
    for path in sorted(task.rglob('*')):
        if path.is_symlink():
            raise ValueError('题目数据不允许符号链接')
        if path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    h.update(block)
            inputs[path.relative_to(task).as_posix()] = h.hexdigest()
    return {'source': sources, 'task_files': inputs, 'config_sha256': digest(config),
            'evaluation': verify_evaluation(evaluation)}


def doctor(config, output):
    from dataspace_baselines.config import load_harness_experiment_config
    from dataspace_baselines.doctor import run_doctor
    checks = run_doctor(load_harness_experiment_config(config), offline=False, temp_dir=output / 'doctor-tmp')
    write_json(output / 'doctor.json', [{'name': c.name, 'ok': c.ok, 'detail': c.detail} for c in checks])
    if not all(c.ok for c in checks):
        raise ValueError('运行环境检查未通过，请查看输出目录中的doctor.json；未调用模型')


def run_trial(task, config, root):
    from dataspace_baselines.config import load_harness_experiment_config
    from dataspace_baselines.core.task import TaskSpec
    from dataspace_baselines.harnesses import build_harness
    runner = build_harness('codex', load_harness_experiment_config(config))
    old = {}

    def stop(signum, frame):
        runner.cancel()
        raise KeyboardInterrupt

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, stop)
        runner.run(TaskSpec.load(task), root / 'attempt')
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return json.loads((root / 'attempt/run.json').read_text())


def conclusion(records):
    if len(records) != 3 or not all('passed' in r for r in records):
        return '未完成三次有效评分，不能判断运行间波动。'
    passed = sum(r['passed'] for r in records)
    if len({r['prediction_sha256'] for r in records}) == 1:
        return f'通过{passed}/3，三次预测字节相同；本次未观察到预测输出波动。'
    return f'通过{passed}/3，观测到预测输出差异；仅凭三次不能证明是纯随机波动，真实模型版本和服务端采样未锁定。'


def execute(settings, evaluation, output, check_only=False):
    if not os.environ.get('EVIWEAVE_API_KEY', '').strip():
        raise ValueError('未读取到EVIWEAVE_API_KEY，请先在当前终端export该变量；不会读取其他key或环境文件')
    task, config = task_paths(settings, 33)
    if not task.is_relative_to(PROJECT) or not config.is_relative_to(PROJECT):
        raise ValueError('此实验只接受项目内的独立数据和配置')
    evaluation = plain(evaluation)
    if not evaluation.is_relative_to(PROJECT):
        raise ValueError('评分资源必须位于项目内')
    output = fresh(output)
    identity = snapshot(task, config, evaluation)
    output.mkdir(parents=True, mode=0o700)
    records = []
    report = {'status': 'FAIL', 'task': 33, 'planned_trials': 3, 'check_only': check_only,
              'api_key_env_readable': True, 'service_auth': 'NOT_TESTED', 'python': sys.version,
              'execution': 'verified project source', 'records': records,
              'limits': 'serial, fresh HOME/session, unchanged V6 budgets; no automatic retry'}
    write_json(output / 'identity.json', identity)
    shutil.copyfile(config, output / 'config.private.yaml')
    try:
        with private_environment(output):
            doctor(config, output)
        print('EVIWEAVE_API_KEY：可读取（不显示值）；运行环境检查通过。', flush=True)
        if check_only:
            report['status'] = 'CHECK_ONLY'
            print('仅检查模式，未请求模型；服务端鉴权尚未验证。', flush=True)
            return 0
        for trial in range(1, 4):
            # Stop on changed inputs rather than comparing different experiments.
            if snapshot(task, config, evaluation) != identity:
                raise ValueError('输入、配置、评分资源或冻结源码发生变化，停止后续调用')
            root = output / f'trial_{trial}'
            root.mkdir(mode=0o700)
            print(f'[{trial}/3] 第33题开始，新会话；日志：{root}/attempt', flush=True)
            with private_environment(root):
                run = run_trial(task, config, root)
            record = {k: run.get(k) for k in ('status', 'return_code', 'model_turns', 'tool_actions', 'wall_time_seconds', 'usage')}
            record['trial'] = trial
            records.append(record)
            if (run.get('model_turns') or 0) > 0:
                report['service_auth'] = 'MODEL_TURN_RECORDED'
            prediction = root / 'attempt/output/prediction.csv'
            if run['status'] != 'submitted' or not prediction.is_file():
                raise ValueError(f'第{trial}次运行未正常提交，停止；查看该次run.json、stdout.log和stderr.log')
            record['prediction_sha256'] = digest(prediction)
            with prediction.open(newline='', encoding='utf-8-sig') as handle:
                rows = csv.reader(handle)
                next(rows)
                record['prediction_rows'] = sum(1 for _ in rows)
            predictions = root / 'predictions/task_33'
            predictions.mkdir(parents=True)
            shutil.copyfile(prediction, predictions / 'prediction.csv')
            result = score(evaluation, root / 'predictions', root / 'scoring', [33])
            scored = json.loads(Path(result['score_file']).read_text())
            if scored['task_count'] != 1 or scored['tasks'][0]['task_id'] != 'task_33':
                raise ValueError('评分范围不符')
            record['passed'] = scored['tasks'][0]['passed']
            record['scoring_error_code'] = scored['tasks'][0].get('error_code')
            if snapshot(task, config, evaluation) != identity:
                raise ValueError('执行后发现来源变化，停止后续调用')
            print(f"[{trial}/3] {'通过' if record['passed'] else '未通过'}，{record['prediction_rows']}行，SHA256={record['prediction_sha256']}", flush=True)
            write_json(output / 'summary.json', dict(report, status='RUNNING'))
        report['status'] = 'COMPLETE'
        return 0
    except KeyboardInterrupt:
        report['status'] = 'INTERRUPTED'
        print('已中断并取消当前任务，保留已有证据。', file=sys.stderr)
        return 130
    finally:
        report['conclusion'] = conclusion(records)
        report['completed_scored_trials'] = sum('passed' in r for r in records)
        report['passed_trials'] = sum(r.get('passed', False) for r in records)
        write_json(output / 'summary.json', report)
        with (output / 'summary.csv').open('w', newline='') as handle:
            fields = ['trial', 'status', 'passed', 'prediction_rows', 'prediction_sha256', 'model_turns', 'wall_time_seconds']
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(records)
        print(report['conclusion'], flush=True)
        print('完整记录：' + str(output / 'summary.json'), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=PROJECT / '.local/config-v6/settings.json')
    parser.add_argument('--evaluation-root', type=Path, default=PROJECT / '.local/evaluation-v6-public60')
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--check-only', action='store_true', help='只检查key可读性/输入/本地环境，不调用模型')
    args = parser.parse_args(argv)
    # The terminal entry uses the verified source without needing a wheel install.
    sys.path.insert(0, str(PROJECT / 'src'))
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    output = args.output_root or PROJECT / '.audit' / ('task33-three-runs-' + name)
    try:
        return execute(args.settings, args.evaluation_root, output, args.check_only)
    except (ValueError, OSError, KeyError, ImportError, csv.Error, subprocess.TimeoutExpired) as exc:
        print('检查停止：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
