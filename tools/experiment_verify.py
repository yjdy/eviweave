"""Offline integrity, frozen scoring and versioned reports for unified experiments."""
from __future__ import annotations

from collections import Counter
import csv
import io
import json
from pathlib import Path
import platform
import shutil

from experiment_store import (atomic_bytes, attempts, completed, evidence, execution_evidence, fingerprint, internal,
                              load, original, plan_for, read, select, sha, uid, write)
from standalone import PROJECT, score as frozen_score, verify_evaluation


def usage_summary(rows):
    fields = sorted({key for r in rows for key in (r.get('usage') or {})})
    fields = sorted(set(fields) | {'input_tokens', 'output_tokens', 'cached_input_tokens',
                                 'cache_write_input_tokens', 'reasoning_output_tokens'})
    return {'attempts': len(rows), 'with_usage': sum(bool(r.get('usage')) for r in rows),
            'excluded_attempts': sum(bool(r.get('usage_exclusion_reason')) for r in rows),
            'fields': {key: {'recorded_sum': sum(r['usage'][key] for r in rows if key in (r.get('usage') or {}))
                            if any(key in (r.get('usage') or {}) for r in rows) else None,
                            'coverage': sum(key in (r.get('usage') or {}) for r in rows)} for key in fields},
            'amount': None, 'amount_reason': 'token usage only; monetary amount not calculated',
            'policy': 'Raw fields summed separately; cached/reasoning subfields are not added to parent totals.'}


def score_attempt(evaluation, prediction, task, output):
    predictions = output / 'predictions'
    target = predictions / f'task_{task}/prediction.csv'
    target.parent.mkdir(parents=True)
    shutil.copyfile(prediction, target)
    result = frozen_score(evaluation, predictions, output / 'evaluation', [task])
    doc = read(Path(result['score_file']))
    if doc['task_count'] != 1 or len(doc['tasks']) != 1 or doc['tasks'][0]['task_id'] != f'task_{task}':
        raise ValueError('frozen evaluator scope mismatch')
    return doc['tasks'][0]


def report_target(value, experiment):
    from experiment_store import output_path
    target = output_path(value, [experiment])
    if target.exists():
        allowed = {'summary.json', 'score.json', 'summary.csv', 'report-set.json', 'reports', '.experiment.lock'}
        if any(p.name not in allowed for p in target.iterdir()):
            raise ValueError('custom report directory contains unknown files')
        if any((target / name).exists() for name in ('summary.json', 'score.json', 'summary.csv')):
            owner = read(target / 'report-set.json') if (target / 'report-set.json').exists() else {}
            if owner.get('kind') != 'eviweave-report-set' or owner.get('experiment_identity') != load(experiment)['identity_sha256']:
                raise ValueError('refusing to overwrite unknown report files')
            known = {name: set() for name in ('summary.json', 'score.json', 'summary.csv')}
            for committed in (target / 'reports').glob('*/report-set.json'):
                version = read(committed)
                if version.get('experiment_identity') != owner['experiment_identity']:
                    continue
                if all((committed.parent / name).is_file() and sha(committed.parent / name) == value
                       for name, value in version.get('files', {}).items()) and set(version.get('files', {})) == set(known):
                    for name, value in version['files'].items():
                        known[name].add(value)
            for name, hashes in known.items():
                if (target / name).exists() and sha(target / name) not in hashes:
                    raise ValueError('refusing to overwrite modified/unknown report file: ' + name)
    return target


def verify(root, output=None, scorer=score_attempt):
    """Caller holds the same experiment lock used by scheduling."""
    doc = load(root)
    environment = read(root / 'resources/environment-lock.json')
    if fingerprint(environment['profile']) != environment['profile_sha256']:
        raise ValueError('environment profile snapshot damaged')
    evaluation = root / 'resources/evaluation'
    if verify_evaluation(evaluation) != doc['identity']['evaluation']:
        raise ValueError('frozen evaluation identity changed')
    target = output or root
    rid = uid()
    report = target / f'reports/{rid}'
    report.mkdir(parents=True)
    plan = plan_for(root, doc)
    records = attempts(root, doc)
    rows, anomalies = [], []
    available = {int(p.stem.removeprefix('task_')) for p in (evaluation / 'configs').glob('task_*.json')}
    scores = {'report_id': rid, 'evaluation': doc['identity']['evaluation'], 'attempts': {}, 'current': {}}
    selection = read(root / 'selection.json')
    expected_selection = select(records)
    if selection != expected_selection:
        anomalies.append('current selection differs from latest allocated attempts; use resume to recover')
    for a in records:
        directory = internal(root, a['attempt_id'])
        run, inspection = execution_evidence(root, a)
        conflict = inspection['status'] == 'conflict'
        if conflict:
            anomalies.extend(a['attempt_id'] + ': ' + reason for reason in inspection['reasons'])
        row = dict(a, usage=None if conflict else (run.get('usage') or None), return_code=run.get('return_code'),
                   wall_time_seconds=run.get('wall_time_seconds'), error=a.get('error') or run.get('error'),
                   prediction_valid=False, passed=None, evidence_status=inspection['status'],
                   evidence_error='; '.join(inspection['reasons']) or None,
                   usage_exclusion_reason='conflicting execution evidence' if conflict else None)
        if a['status'] == 'submitted' and not conflict:
            try:
                _, prediction = evidence(root, a)
                if not a.get('prediction_sha256'):
                    raise ValueError('submitted outer attempt missing prediction digest')
                row['prediction_valid'] = True
                if a['task'] in available:
                    result = scorer(evaluation, original(root, a), a['task'], report / f'scoring/history/{a["sequence"]}')
                    scores['attempts'][a['attempt_id']] = result
                    row['passed'] = result['passed']
                    row['scoring_error_code'] = result.get('error_code')
            except (OSError, ValueError, KeyError, UnicodeError) as exc:
                row['error'] = 'integrity/scoring: ' + str(exc)
                row.update(evidence_status='conflict', evidence_error=str(exc), usage=None,
                           usage_exclusion_reason='prediction/scoring evidence failed')
                anomalies.append(a['attempt_id'] + ': ' + str(exc))
        if run.get('task_id') and run['task_id'] != f'task_{a["task"]}':
            anomalies.append(a['attempt_id'] + ': conflicting runner task')
        rows.append(row)
    by_id = {a['attempt_id']: a for a in rows}
    for task, aid in selection.items():
        a = by_id.get(aid)
        if a is None or str(a['task']) != task:
            anomalies.append('invalid selection: ' + task)
            continue
        prediction = root / f'predictions/task_{task}/prediction.csv'
        if a['status'] == 'submitted' and a['prediction_valid']:
            if not prediction.is_file() or sha(prediction) != a['prediction_sha256']:
                anomalies.append('current collected prediction missing/changed: ' + task)
                scores['current'][task] = {'passed': False, 'status': 'missing_or_damaged_current'}
            elif int(task) in available:
                scores['current'][task] = scorer(evaluation, prediction, int(task), report / f'scoring/current/{task}')
            else:
                scores['current'][task] = {'passed': None, 'status': 'unscored_no_public_gold'}
        else:
            if prediction.exists():
                anomalies.append('failed latest attempt retains stale current prediction: ' + task)
            scores['current'][task] = {'passed': False if int(task) in available else None, 'status': 'execution_incomplete'}
    # Foreign collected predictions must never become invisible current answers.
    for prediction in (root / 'predictions').glob('*/prediction.csv'):
        task = prediction.parent.name.removeprefix('task_')
        if task not in selection:
            anomalies.append('unselected collected prediction: ' + task)
    latest_slots = {a['slot']: a for a in rows if a['plan_id'] == plan['plan_id']}
    progress = Counter()
    for slot in plan['slots']:
        a = latest_slots.get(slot)
        status = a['status'] if a else 'not_started'
        if a and status == 'submitted' and not a['prediction_valid']:
            status = 'damaged_prediction'
        progress[status] += 1
    attempted = {(a['plan_id'], a['slot']) for a in rows}
    missing_rows = []
    for pid in doc['plans']:
        historical_plan = plan_for(root, doc, pid)
        missing_rows.extend(dict(plan_id=pid, slot=slot, task=int(slot.split(':')[0]),
                                 repeat=int(slot.split(':')[1]), status='not_started', usage=None)
                            for slot in historical_plan['slots'] if (pid, slot) not in attempted)

    def accuracy(tasks):
        public = sorted(set(tasks) & available)
        passed = sum(scores['current'].get(str(t), {}).get('passed') is True for t in public)
        return {'public_tasks': len(public), 'passed': passed, 'accuracy': passed / len(public) if public else None,
                'unscored_tasks': len(set(tasks) - available)}

    current_tasks = {}
    for task in sorted({int(t) for t in selection} | set(plan['tasks'])):
        aid = selection.get(str(task))
        attempt = by_id.get(aid)
        current_score = scores['current'].get(str(task), {})
        state = attempt['status'] if attempt else 'not_started'
        if attempt and state == 'submitted' and not attempt['prediction_valid']:
            state = 'damaged_prediction'
        if current_score.get('status') == 'missing_or_damaged_current':
            state = 'missing_or_damaged_current'
        current_tasks[str(task)] = {'attempt_id': aid, 'status': state,
                                    'prediction_sha256': attempt.get('prediction_sha256') if attempt else None,
                                    'passed': current_score.get('passed'), 'public_gold': task in available}
    success = [r for r in rows if r['status'] == 'submitted' and r['prediction_valid']]
    summary = {'kind': 'eviweave-unified-report', 'report_id': rid, 'format_version': 1,
               'experiment_identity': doc['identity_sha256'], 'active_plan': plan,
               'integrity': 'FAIL' if anomalies else 'PASS', 'anomalies': anomalies,
               'plan_progress': dict(progress), 'historical_status_counts': dict(Counter(a['status'] for a in rows)),
               'history_attempts': len(rows), 'current_selection': selection,
               'attempt_integrity': {r['attempt_id']: {'status': r['evidence_status'],
                   'reason': r['evidence_error'], 'usage_exclusion_reason': r['usage_exclusion_reason']} for r in rows},
               'current_tasks': current_tasks,
               'current_status_counts': dict(Counter(r['status'] for r in current_tasks.values())),
               'current_plan_accuracy': accuracy(plan['tasks']),
               'all_current_accuracy': accuracy([int(t) for t in selection]),
               'usage': {'all': usage_summary(rows), 'submitted': usage_summary(success),
                         'failed': usage_summary([r for r in rows if r not in success])},
               'environment_profile': environment['profile_id'], 'environment_sha256': doc['identity']['environment_sha256'],
               'verification_python': platform.python_version(), 'runtime_match_check': 'NOT_REQUIRED_FOR_OFFLINE_VERIFY',
               'verification_model_calls': 0}
    scores['current_plan_accuracy'] = summary['current_plan_accuracy']
    fields = ['report_id', 'plan_id', 'launch_id', 'task', 'repeat', 'slot', 'attempt_id', 'previous_attempt',
              'status', 'return_code', 'error', 'prediction_sha256', 'prediction_valid', 'passed',
              'scoring_error_code', 'wall_time_seconds', 'usage']
    fields.extend(['evidence_status', 'evidence_error', 'usage_exclusion_reason'])
    handle = io.StringIO(newline='')
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in [*rows, *missing_rows]:
        writer.writerow(dict(row, report_id=rid, usage=json.dumps(row.get('usage'), sort_keys=True)))
    write(report / 'summary.json', summary)
    write(report / 'score.json', scores)
    atomic_bytes(report / 'summary.csv', handle.getvalue().encode())
    manifest = {'kind': 'eviweave-report-set', 'report_id': rid, 'experiment_identity': doc['identity_sha256'],
                'files': {name: sha(report / name) for name in ('summary.json', 'score.json', 'summary.csv')}}
    write(report / 'report-set.json', manifest)
    # The version's manifest commits all three files. A pointer allows the next verify
    # to reconstruct interrupted root copies from a complete version.
    write(target / 'report-set.json', manifest)
    for name in manifest['files']:
        atomic_bytes(target / name, (report / name).read_bytes())
    return summary
