#!/usr/bin/env python3
"""Read-only, standard-library audit of the historical EviWeave V6 baseline."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path

MANIFEST_SHA256 = 'bd031c52e3f7322d551d105dfe03f78e847ad8e2e5984e5b16c51632af907236'
FINGERPRINT = '367c7815e55b02c3b33ecd4422faddfdc24cc042767a87726b3547052584d396'
RETRIES = {22, 23, 207, 273}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify(experiment, benchmark):
    errors, files, attempts, predictions = [], [], [], []
    def require(condition, code, item):
        if not condition:
            errors.append({'status': code, 'item': item})
    def load(path):
        try:
            return read_json(path)
        except (OSError, ValueError) as exc:
            errors.append({'status': 'missing' if isinstance(exc, FileNotFoundError) else 'unparseable', 'item': str(path), 'detail': str(exc)})
            return {}
    manifest = load(experiment / 'frozen_manifest.json')
    try:
        manifest_digest = sha256(experiment / 'frozen_manifest.json')
    except OSError:
        manifest_digest = None
    require(manifest_digest == MANIFEST_SHA256, 'mismatch', 'manifest_sha256')
    require(len(manifest) == 8016, 'mismatch', 'manifest_count')
    require(sum(k.startswith('input/') for k in manifest) == 7849, 'mismatch', 'input_count')
    def check(entry):
        key, expected = entry
        base = benchmark if key.startswith('input/') else experiment
        path = base / key
        if not path.resolve().is_relative_to(base.resolve()):
            return {'path': key, 'status': 'unreadable', 'detail': 'path escapes source'}
        try:
            actual = sha256(path)
            return {'path': key, 'expected': expected, 'actual': actual, 'status': 'pass' if actual == expected else 'mismatch'}
        except OSError as exc:
            return {'path': key, 'status': 'missing' if isinstance(exc, FileNotFoundError) else 'unreadable', 'detail': str(exc)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(check, sorted(manifest.items())))
    errors.extend(f for f in files if f['status'] != 'pass')
    experiment_record = load(experiment / 'run/experiment.json')
    identity = experiment_record.get('identity', {})
    calculated = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(calculated == FINGERPRINT == experiment_record.get('fingerprint'), 'identity_mismatch', 'experiment.json')
    require(identity.get('prompt_contract') == 'relation-v6', 'identity_mismatch', 'prompt_contract')
    safeguards = identity.get('execution_policy', {}).get('simple_fixes', {})
    require(safeguards.get('version') == 'simple-fixes-v1', 'identity_mismatch', 'output_safeguards')
    protocol = load(experiment / 'protocol.json')
    retry = load(experiment / 'retry_four_protocol.json')
    task_ids = {f'task_{i}' for i in range(1, 411)}
    require(set(protocol.get('task_ids', [])) == task_ids and len(protocol.get('task_ids', [])) == 410, 'mismatch', 'protocol_tasks')
    require(set(retry.get('task_ids', [])) == {f'task_{i}' for i in RETRIES}, 'mismatch', 'retry_tasks')
    require(retry.get('experiment_fingerprint') == FINGERPRINT and retry.get('manifest_sha256') == MANIFEST_SHA256, 'identity_mismatch', 'retry_protocol')
    require({p.name for p in (experiment / 'run/tasks').iterdir()} == task_ids, 'mismatch', 'task_directories')
    require({p.name for p in (experiment / 'run/predictions').iterdir()} == task_ids, 'mismatch', 'prediction_directories')
    results = load(experiment / 'results.json')
    def index_records(records, name):
        index = {(x.get('task_id'), x.get('attempt')): x for x in records}
        require(len(index) == len(records), 'mismatch', name + '_duplicates')
        return index
    result_attempts = index_records(results.get('all_attempts', []), 'result_attempts')
    selected_results = index_records(results.get('tasks', []), 'selected_results')
    summary = load(experiment / 'run/batch_summary.json')
    summary_results = index_records(summary.get('tasks', []), 'batch_summary')
    record_keys = ['task_id', 'harness', 'model', 'status', 'return_code', 'usage', 'wall_time_seconds', 'model_turns', 'tool_actions', 'error', 'prediction_path']
    missing_usage = []
    expected_attempts = set()
    expected_selected = set()
    for number in range(1, 411):
        task_id = f'task_{number}'
        root = experiment / 'run/tasks' / task_id
        expected_names = {'run_1', 'run_2'} if number in RETRIES else {'run_1'}
        require({p.name for p in root.glob('run_*')} == expected_names, 'mismatch', task_id + '/attempt_set')
        for attempt in sorted(expected_names):
            expected_attempts.add((task_id, attempt))
            run_root = root / attempt
            record = load(run_root / 'run.json')
            try:
                events = [json.loads(line) for line in (run_root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            except (OSError, ValueError) as exc:
                errors.append({'status': 'unparseable', 'item': f'{task_id}/{attempt}/events', 'detail': str(exc)})
                events = []
            starts = [e for e in events if e.get('event') == 'run_started']
            ends = [e for e in events if e.get('event') == 'run_finished']
            require(len(starts) == len(ends) == 1, 'mismatch', f'{task_id}/{attempt}/event_count')
            if len(starts) == len(ends) == 1:
                start, end = starts[0], ends[0]
                require(start.get('experiment_fingerprint') == FINGERPRINT and start.get('task_id') == task_id and start.get('harness') == 'codex' and start.get('model') == 'qwen3.8-flash', 'identity_mismatch', f'{task_id}/{attempt}')
                meta = start.get('invocation', {}).get('metadata', {})
                require(meta.get('simple_fixes') == safeguards and meta.get('runtime_version') == '0.145.0', 'identity_mismatch', f'{task_id}/{attempt}/safeguards')
                for key in ['status', 'return_code', 'prediction_path', 'error', 'wall_time_seconds']:
                    require(end.get(key) == record.get(key), 'mismatch', f'{task_id}/{attempt}/finish/{key}')
            require(record.get('task_id') == task_id and record.get('harness') == 'codex' and record.get('model') == 'qwen3.8-flash', 'identity_mismatch', f'{task_id}/{attempt}/record')
            aggregate = result_attempts.get((task_id, attempt), {})
            require(all(aggregate.get(k) == record.get(k) for k in record_keys), 'mismatch', f'{task_id}/{attempt}/aggregate')
            usage = record.get('usage') or None
            if usage is None:
                missing_usage.append([task_id, attempt])
            attempts.append({'task_id': task_id, 'attempt': attempt, 'status': record.get('status'), 'usage': usage, 'run_sha256': sha256(run_root / 'run.json') if (run_root / 'run.json').exists() else None, 'events_sha256': sha256(run_root / 'events.jsonl') if (run_root / 'events.jsonl').exists() else None})
        chosen = 'run_2' if number in RETRIES else 'run_1'
        expected_selected.add((task_id, chosen))
        record = load(root / chosen / 'run.json')
        require(record.get('status') == 'submitted', 'mismatch', task_id + '/selected_status')
        for name, table in [('results', selected_results), ('summary', summary_results)]:
            require(all(table.get((task_id, chosen), {}).get(k) == record.get(k) for k in record_keys), 'mismatch', task_id + '/selected_' + name)
        output = root / chosen / 'output/prediction.csv'
        final = experiment / 'run/predictions' / task_id / 'prediction.csv'
        try:
            digest = sha256(output)
            require(digest == sha256(final), 'prediction_mismatch', task_id)
            with final.open(encoding='utf-8', newline='') as handle:
                rows = list(csv.reader(handle, strict=True))
            require(bool(rows) and bool(rows[0]) and all(len(row) == len(rows[0]) for row in rows), 'invalid_prediction', task_id)
            predictions.append({'task_id': task_id, 'attempt': chosen, 'sha256': digest, 'bytes': final.stat().st_size, 'data_rows': len(rows)-1})
        except (OSError, ValueError, csv.Error, UnicodeError) as exc:
            errors.append({'status': 'invalid_prediction', 'item': task_id, 'detail': str(exc)})
    require(set(result_attempts) == expected_attempts, 'mismatch', 'all_attempts_set')
    require(set(selected_results) == expected_selected == set(summary_results), 'mismatch', 'selected_attempts_set')
    require(sorted(missing_usage) == sorted([[f'task_{i}', 'run_1'] for i in [22,23,207]]), 'mismatch', 'missing_usage')
    counts = dict(Counter(a['status'] for a in attempts))
    require(counts == {'submitted':410, 'runtime_error':3, 'no_prediction':1}, 'mismatch', 'attempt_status_counts')
    return {'status': 'PASS' if not errors else 'FAIL', 'manifest_sha256': manifest_digest, 'experiment_fingerprint': experiment_record.get('fingerprint'), 'prompt_contract': identity.get('prompt_contract'), 'output_safeguards': safeguards, 'file_counts': dict(Counter(f['status'] for f in files)), 'input_count': sum(f['path'].startswith('input/') for f in files), 'non_input_count': sum(not f['path'].startswith('input/') for f in files), 'attempt_count': len(attempts), 'prediction_count': len(predictions), 'attempt_status_counts': counts, 'missing_usage': missing_usage, 'files': files, 'attempts': attempts, 'predictions': predictions, 'differences': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment', type=Path)
    parser.add_argument('benchmark', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    # Never write inside either read-only source, even when invoked incorrectly.
    output = args.output.resolve()
    for source in [args.experiment.resolve(), args.benchmark.resolve()]:
        if output == source or output.is_relative_to(source):
            parser.error('output must be outside the read-only sources')
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = verify(args.experiment.resolve(), args.benchmark.resolve())
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result = {'status': 'FAIL', 'differences': [{'status': 'unparseable', 'detail': f'{type(exc).__name__}: {exc}'}]}
    for name, value in [('result', result), ('differences', result['differences']), ('attempts', result.get('attempts', [])), ('predictions', result.get('predictions', []))]:
        (output / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {k:v for k,v in result.items() if k not in ['files','attempts','predictions','differences']}
    (output / 'summary.md').write_text('# P0 baseline: ' + result['status'] + '\n\n```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n\nDifferences: ' + str(len(result['differences'])) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
