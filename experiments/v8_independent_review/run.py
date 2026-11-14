"""Run the 60 public tasks through bounded, lightweight independent review."""

import argparse
from collections import Counter
from dataclasses import replace
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools')]

import light_review as light
import packets
import pilot_evidence
import repair
import review_common as original
import resume_legacy
from experiment_scheduler import global_failure as scheduler_failure

shared = original.shared
LIMITS = {'B1': 180, 'B2': 240}
DEFAULT_OUTPUT = original.AUDIT / 'v8-independent-review-public60-recovery' / 'public60'
CANDIDATE_NOTE = ('\nAfter computing and before exporting the candidate CSV, write extraction.md. '
                  'Check that both /mnt/output/extraction.md and /mnt/output/prediction.csv exist '
                  'and are nonempty before ending the turn.\n')


def setup(evaluation):
    config, config_path, fingerprint, tasks, pdf = original.settings(evaluation)
    historical = shared.read_json(original.DEFAULT_RUN / 'score.json')['tasks']
    reuse = {}
    inputs = {}
    for number, task in tasks.items():
        hashes = original.input_hashes(task)
        inputs[task.task_id] = hashes
        folder = original.DEFAULT_RUN / task.task_id
        if (folder / 'candidate/checkpoint.json').is_file():
            saved = original.candidate_check(folder / 'candidate', task, config)
            if (hashes != shared.read_json(folder / 'inputs.json')
                    or saved != historical[str(number)]['receipts']['candidate']):
                raise ValueError(f'{task.task_id} 与原候选检查点不一致')
            reuse[task.task_id] = saved
    identity = {'baseline': fingerprint, 'config_path': str(config_path), 'model': config.model.model,
                'evaluation': str(evaluation),
                'limits': LIMITS, 'candidate_limit_seconds': config.wall_time_seconds,
                'extraction_repair_seconds': repair.LIMIT_SECONDS,
                'task_ids': [task.task_id for task in tasks.values()], 'pdf_tasks': sorted(pdf),
                'inputs': inputs, 'reused_checkpoints': reuse,
                'historical_v8_score_sha256': shared.digest(original.HISTORICAL),
                'code': {p.name: shared.digest(p) for p in (Path(__file__), Path(packets.__file__), Path(repair.__file__),
                         Path(light.__file__), Path(pilot_evidence.__file__), Path(original.__file__),
                         Path(shared.__file__), Path(resume_legacy.__file__))}}
    return config, tasks, pdf, identity


def prepare(root, config, tasks, identity):
    if any(p.name != '.run.lock' for p in root.iterdir()):
        raise ValueError('准备目录已有文件，请使用独立输出目录')
    start = time.monotonic()
    digests = {}
    for number, task in tasks.items():
        directory = root / task.task_id
        directory.mkdir()
        packet = directory / 'evidence'
        if task.task_id in identity['reused_checkpoints']:
            pilot_evidence.build(task, number, config, packet)
        else:
            packets.build(task, config, packet)
        digests[task.task_id] = light.signature(packet)
    shared.write_json(root / 'experiment.json', {'identity': identity, 'evidence': digests,
                                                 'preparation_seconds': time.monotonic() - start})


def check(root, tasks, identity):
    manifest = shared.read_json(root / 'experiment.json')
    if manifest['identity'] != identity:
        raise ValueError('实验身份变化，请使用新的输出目录')
    for task in tasks.values():
        packet = root / task.task_id / 'evidence'
        if light.signature(packet) != manifest['evidence'][task.task_id]:
            raise ValueError(f'{task.task_id} 证据包摘要发生变化')
    import_receipt = root / 'legacy-import.json'
    expected = manifest.get('legacy_import_sha256')
    if expected != (shared.digest(import_receipt) if import_receipt.is_file() else None):
        raise ValueError('旧批次导入记录摘要不一致')


def global_failure(directory):
    failure = scheduler_failure(directory)
    if failure:
        return failure
    log = directory / 'stdout.log'
    if not log.is_file():
        return None
    for line in reversed(log.read_text(encoding='utf-8', errors='replace').splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'turn.completed':
            break
        if event.get('type') == 'turn.failed':
            error = event.get('error', event)
            message = error.get('message', '') if isinstance(error, dict) else ''
            if isinstance(message, str) and ('status 429' in message or 'status: 429' in message) and any(
                code in message.lower() for code in ('insufficient_quota', 'quota_exceeded', 'billing_hard_limit_reached')
            ):
                return 'service authentication/quota failure: explicit quota exhaustion (HTTP 429)'
            break
    return None


def checked_result(run, task, directory):
    result = run(task, directory)
    failure = global_failure(directory)
    if failure:
        raise ValueError(f'{task.task_id}: {failure}')
    return result


def review_runner(config, phase, task, packet, **kwargs):
    runner = light.create_runner(config, phase, task, packet, **kwargs)
    run = runner.run
    build = runner.invocation_factory

    def invocation(task, directory, output):
        base = build(task, directory, output)
        if phase == 'B1':
            guidance = ('\nEvidence previews are incomplete, including file excerpts and sample rows. '
                        'A sample is not the complete cohort. Leave the full population uncertain when needed. '
                        'The index may contain nested previews and timestamped frame paths.\n')
        else:
            guidance = ('\nOnce a focused check supports the decision, save the review JSON and final CSV promptly. '
                        'End after checking both files. If keeping the candidate, copy its bytes as-is. '
                        'If a material question remains open, record it explicitly and keep the supported candidate.\n')
        return replace(base, command=(*base.command[:-1], base.command[-1] + guidance))

    runner.invocation_factory = invocation
    def run_checked(task, directory):
        return checked_result(run, task, directory)

    runner.run = run_checked
    return runner


def candidate_runner(config):
    runner = shared.candidate_runner(config)
    build = runner.invocation_factory

    def invocation(task, directory, output):
        inv = build(task, directory, output)
        return replace(inv, command=(*inv.command[:-1], inv.command[-1] + CANDIDATE_NOTE))

    runner.invocation_factory = invocation
    runner.resume_identity['candidate_note'] = hashlib.sha256(CANDIDATE_NOTE.encode()).hexdigest()
    return runner


def candidate_run_check(path, task, runner, legacy_fingerprint=None):
    start = original.first_event(path / 'events.jsonl')
    fingerprints = {runner.resume_fingerprint}
    if legacy_fingerprint is not None:
        fingerprints.add(legacy_fingerprint)
    if (start['experiment_fingerprint'] not in fingerprints or start['task_id'] != task.task_id
            or start['question'] != task.question or Path(start['workspace']) != task.context_dir):
        raise ValueError(f'{task.task_id} 候选运行身份不一致')


def finalize_candidate(path, task, config, initial, *, retry_service_failure=False):
    output = path / 'output'
    note = output / 'extraction.md'
    missing_note = not note.is_file() or not note.read_text(encoding='utf-8', errors='replace').strip()
    if (path / 'repair').exists() or (initial['status'] == 'runtime_error'
                                    and initial.get('return_code') == 0 and missing_note):
        if note.is_symlink() or (output / light.review.REVIEW_FILE).exists():
            raise repair.CandidatePending('候选说明是链接或候选已写复审记录')
        return repair.complete(path, task, config, initial, original, global_failure,
                               retry_service_failure=retry_service_failure)
    if initial['status'] != 'submitted':
        raise repair.CandidatePending(f'候选未提交（{initial["status"]}）')
    return {**shared.checkpoint(path), 'usage': initial['usage'], 'wall_time_seconds': initial['wall_time_seconds']}


def candidate_for(directory, task, config, reused, *, allow_new=True, legacy_fingerprint=None):
    if reused is not None:
        return original.DEFAULT_RUN / task.task_id / 'candidate', reused
    path = directory / 'candidate'
    runner = candidate_runner(config)
    if (path / 'checkpoint.json').is_file():
        candidate_run_check(path, task, runner, legacy_fingerprint)
        saved = shared.read_json(path / 'checkpoint.json')
        actual = shared.checkpoint(path)
        if any(saved.get(key) != val for key, val in actual.items()):
            raise ValueError(f'{task.task_id} 候选检查点摘要不一致')
        return path, saved
    if not allow_new:
        raise ValueError(f'{task.task_id} 已完成的任务缺少候选检查点')
    summary = path / 'run.json'
    prior_attempt = summary.is_file()
    if summary.is_file() and global_failure(path):
        candidate_run_check(path, task, runner, legacy_fingerprint)
        original.archive(path)
    if summary.is_file() and shared.read_json(summary)['status'] == 'stopped' and not (path / 'repair').exists():
        candidate_run_check(path, task, runner, legacy_fingerprint)
        original.archive(path)
    if not summary.is_file():
        if path.exists():
            original.archive(path)
        print(f'{task.task_id} 生成共享候选', flush=True)
        checked_result(runner.run, task, path)
    candidate_run_check(path, task, runner, legacy_fingerprint)
    if failure := global_failure(path):
        raise ValueError(f'{task.task_id}: {failure}')
    initial = shared.read_json(summary)
    if initial['status'] == 'stopped':
        raise KeyboardInterrupt
    saved = finalize_candidate(path, task, config, initial, retry_service_failure=prior_attempt)
    shared.write_json(path / 'checkpoint.json', saved)
    return path, saved


def phase_metrics(receipts):
    tokens = Counter({key: 0 for key in original.RATES})
    missing, snapshots, cache_write = 0, 0, 0
    for receipt in receipts:
        usage = receipt['usage']
        if not usage or not all(key in usage for key in light.FIELDS):
            missing += 1
            continue
        inputs, cached, output = (usage[key] for key in light.FIELDS)
        if not (inputs >= cached >= 0 and output >= 0):
            missing += 1
            continue
        tokens.update(uncached_input_tokens=inputs - cached, cached_input_tokens=cached, output_tokens=output)
        cache_write += usage.get('cache_write_input_tokens', 0)
        snapshots += receipt.get('usage_source') == 'last_persisted_snapshot'
    return {'seconds': sum(row['wall_time_seconds'] for row in receipts), 'recorded_tokens': dict(tokens),
            'recorded_estimated_cny': str(sum(tokens[k] * v for k, v in original.RATES.items()) / Decimal(1_000_000)),
            'usage_missing': missing, 'usage_snapshots': snapshots, 'cache_write_input_tokens_unpriced': cache_write}


def summarize(root, tasks, pdf, candidates, evaluation):
    scorer = original.load_scorer(evaluation)
    history = {row['task_id']: row['passed'] for row in shared.read_json(original.HISTORICAL)['tasks']}
    rows = {}
    suspended = {}
    repair_costs = {}
    for number, task in tasks.items():
        folder = root / task.task_id
        pending = folder / 'pending.json'
        if pending.is_file():
            suspended[number] = shared.read_json(pending)
        repair_path = folder / 'candidate/repair'
        if (repair_path / 'receipt.json').is_file():
            repair_costs[number] = shared.read_json(repair_path / 'receipt.json')
        elif (repair_path / 'run.json').is_file():
            rec = shared.read_json(repair_path / 'run.json')
            repair_costs[number] = {**rec, 'usage': {}}
    for number, (source, saved, reused) in candidates.items():
        name = tasks[number].task_id
        folder = root / name
        receipt = shared.read_json(folder / 'result.json')
        candidate = source / saved['candidate_csv']
        prediction = folder / 'prediction.csv'
        if (shared.digest(prediction) != receipt['prediction_sha256'] or
                shared.digest(candidate) != receipt['candidate_sha256']):
            raise ValueError(f'{name} 评分时摘要不一致')
        config = scorer.load_task_config(evaluation / 'configs' / f'{name}.json')
        gold = evaluation / 'output' / name / 'gold.csv'
        rows[number] = {**receipt, 'historical_v8': history[name], 'reused_candidate': reused,
                        'candidate': scorer.evaluate_task(config=config, gold_path=gold, prediction_path=candidate).passed,
                        'light_B': scorer.evaluate_task(config=config, gold_path=gold, prediction_path=prediction).passed}
    groups = {'public60': set(tasks), 'pdf54': pdf, 'non_pdf6': set(tasks) - pdf,
              'excluding_pilot55': set(tasks) - set(shared.TASKS)}
    summaries = {}
    for group, numbers in groups.items():
        subset = {n: rows[n] for n in sorted(numbers & rows.keys())}
        phases = {phase: [r[phase] for r in subset.values() if r[phase]] for phase in LIMITS}
        generated = [candidates[n][1] for n, row in subset.items() if not row['reused_candidate']]
        summaries[group] = {'planned': len(numbers), 'completed': len(subset), 'pending': sorted(numbers - subset.keys()),
            'suspended': {n: suspended[n] for n in sorted(numbers & suspended.keys())},
            'passes': {k: sum(row[k] for row in subset.values()) for k in ('historical_v8', 'candidate', 'light_B')},
            'corrected': [n for n, r in subset.items() if r['light_B'] and not r['candidate']],
            'regressed': [n for n, r in subset.items() if r['candidate'] and not r['light_B']],
            'fallbacks': [n for n, r in subset.items() if r['decision'] == 'fallback'],
            'new_candidate_generation': phase_metrics(generated),
            'extraction_repair': phase_metrics([repair_costs[n] for n in sorted(numbers & repair_costs.keys())]),
            'review': {phase: phase_metrics(data) for phase, data in phases.items()},
            'review_total': phase_metrics(phases['B1'] + phases['B2'])}
    shared.write_json(root / 'score.json', {'groups': summaries, 'tasks': rows,
                                          'cny_rates_per_million': {k: str(v) for k, v in original.RATES.items()}})


def run_all(root, config, tasks, pdf, identity):
    completed = {}
    reuse = identity['reused_checkpoints']
    import_receipt = root / 'legacy-import.json'
    imported = shared.read_json(import_receipt)['candidates'] if import_receipt.is_file() else {}
    try:
        for number, task in tasks.items():
            directory = root / task.task_id
            pending = directory / 'pending.json'
            if original.input_hashes(task) != identity['inputs'][task.task_id]:
                raise ValueError(f'{task.task_id} 原始输入摘要发生变化')
            if not (directory / 'evidence/evidence.json').is_file():
                raise ValueError(f'{task.task_id} 缺少第一阶段证据包')
            if pending.is_file():
                print(f'{task.task_id} 已挂起', flush=True)
                continue
            previous = (directory / 'result.json').is_file()
            legacy = imported.get(task.task_id, {})
            fingerprint = legacy.get('fingerprint')
            expected_csv = legacy.get('prediction_sha256')
            if expected_csv is not None and not (directory / 'candidate/checkpoint.json').is_file():
                if shared.digest(directory / 'candidate/output/prediction.csv') != expected_csv:
                    raise ValueError(f'{task.task_id} 导入的候选摘要发生变化')
                trace = legacy['trace']
                if trace and shared.digest(directory / 'candidate' / trace['path']) != trace['sha256']:
                    raise ValueError(f'{task.task_id} 导入的候选会话摘要发生变化')
            try:
                source, saved = candidate_for(directory, task, config, reuse.get(task.task_id),
                                              allow_new=not previous, legacy_fingerprint=fingerprint)
            except repair.CandidatePending as exc:
                shared.write_json(pending, {'task_id': task.task_id, 'stage': 'candidate', 'reason': str(exc),
                                            'repair_attempted': (directory / 'candidate/repair').is_dir()})
                print(f'{task.task_id} 挂起：{exc}', flush=True)
                continue
            if not previous:
                for phase in LIMITS:
                    path = directory / phase
                    if path.exists():
                        original.archive(path)
                (directory / 'scope.sealed.json').unlink(missing_ok=True)
                print(f'{task.task_id} 独立复审', flush=True)
                light.review_task(root, task, source, saved, config, runner_factory=review_runner, limits=LIMITS)
            completed[number] = source, saved, task.task_id in reuse
    finally:
        summarize(root, tasks, pdf, completed, Path(identity['evaluation']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'import-legacy', 'run'))
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--evaluation', type=Path, required=True, help='local frozen evaluation bundle')
    parser.add_argument('--legacy-output', type=Path, help='stopped original public60 output directory')
    args = parser.parse_args()
    if (args.action == 'import-legacy') != (args.legacy_output is not None):
        parser.error('--legacy-output is required only with import-legacy')
    try:
        root = args.output.resolve()
        if root.parent != DEFAULT_OUTPUT.parent:
            raise ValueError(f'输出须使用 {DEFAULT_OUTPUT.parent} 的独立子目录')
        if args.action == 'run' and not os.environ.get(shared.KEY, '').strip():
            raise ValueError(f'请先在终端导出 {shared.KEY}')
        config, tasks, pdf, identity = setup(args.evaluation.resolve())
        if args.action == 'import-legacy':
            resume_legacy.migrate(args.legacy_output.resolve(), root, config, tasks, identity)
        if args.action == 'prepare':
            root.mkdir(mode=0o700, parents=True)
        with (root / '.run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'prepare':
                prepare(root, config, tasks, identity)
            check(root, tasks, identity)
            print(f'{len(tasks)} 题 | {len(pdf)} 道含 PDF | {len(identity["reused_checkpoints"])} 份可复用候选 '
                  f'| {config.model.model} | {LIMITS["B1"]}/{LIMITS["B2"]} 秒 | {root}', flush=True)
            if args.action == 'run':
                run_all(root, config, tasks, pdf, identity)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'public60: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
