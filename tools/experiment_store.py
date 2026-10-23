"""Durable metadata and exclusive access for unified experiments (standard library)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from standalone import PROJECT, plain

KIND = 'eviweave-unified-experiment'


def uid():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(plain(path).read_text(encoding='utf-8'))


def atomic_bytes(path, content):
    path = plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode())


def internal(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe experiment member')
    path = plain(root / relative)
    if not path.is_relative_to(root):
        raise ValueError('experiment member escaped root')
    return path


def output_path(value, protected=()):
    path = plain(Path(value).expanduser())
    if any(c in str(path) for c in ('\n', '\r', '\0')):
        raise ValueError('output path contains control characters')
    if path == PROJECT or not path.is_relative_to(PROJECT):
        raise ValueError('output must be a child of this project')
    fixed = ['src', 'tools', 'scripts', 'configs', 'provenance', 'tests', 'docker',
             'examples', 'data', '.local', '.runtimes', '.git', '.venv', 'docs', '.github']
    for p in [*(PROJECT / n for n in fixed), *PROJECT.glob('.venv*'), Path(sys.prefix).resolve(), *(Path(p).resolve() for p in protected)]:
        if path == p or path.is_relative_to(p) or p.is_relative_to(path):
            raise ValueError('output overlaps protected source/configuration/data/runtime: ' + str(p))
    if path.exists() and not path.is_dir():
        raise ValueError('output is not a directory')
    return path


def exclude_output(path):
    # Only the project's own Git administrative directory may be changed.
    git = PROJECT / '.git'
    if not git.is_dir() or git.is_symlink():
        raise ValueError('custom output requires a project-local .git directory')
    target = plain(git / 'info/exclude')
    guard = plain(git / 'info/eviweave-exclude.lock')
    guard.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(guard, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        old = target.read_bytes() if target.exists() else b''
        rel = path.relative_to(PROJECT).as_posix()
        escaped = ''.join('\\' + c if c in '\\*?[] !#' else c for c in rel)
        line = ('/' + escaped + '/').encode()
        if line not in old.splitlines():
            atomic_bytes(target, old + (b'\n' if old and not old.endswith(b'\n') else b'') + line + b'\n')
    finally:
        os.close(fd)


@contextmanager
def locked(root):
    path = plain(root / '.experiment.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('experiment is in use; retry after run/verify finishes') from exc
        yield
    finally:
        os.close(fd)


def load(root):
    root = plain(root)
    doc = read(root / 'experiment.json')
    if not isinstance(doc, dict):
        raise ValueError('damaged experiment root manifest')
    if doc.get('kind') != KIND or doc.get('format_version') != 1:
        raise ValueError('unsupported experiment format; old batches are not migrated')
    if not isinstance(doc.get('identity'), dict) or not isinstance(doc.get('plans'), dict):
        raise ValueError('damaged identity or plan registry')
    if fingerprint(doc['identity']) != doc['identity_sha256']:
        raise ValueError('damaged experiment identity')
    if sha(root / 'config.resolved.yaml') != doc['identity']['config_sha256']:
        raise ValueError('resolved configuration changed')
    if fingerprint(read(root / 'resources/environment-lock.json')) != doc['identity']['environment_sha256']:
        raise ValueError('environment lock snapshot changed')
    inputs = read(internal(root, doc['inputs_file']))
    if fingerprint(inputs) != doc['inputs_sha256']:
        raise ValueError('registered inputs changed')
    if not doc['plans'] or doc['active_plan'] not in doc['plans']:
        raise ValueError('missing active plan')
    for pid, expected in doc['plans'].items():
        if not re.fullmatch(r'[A-Za-z0-9-]+', pid):
            raise ValueError('invalid plan ID')
        path = internal(root, f'plans/{pid}/plan.json')
        plan = read(path)
        if not isinstance(plan, dict):
            raise ValueError('damaged plan')
        if sha(path) != expected or plan['plan_id'] != pid:
            raise ValueError('plan checksum/association mismatch')
        tasks = plan['tasks']
        if (tasks != sorted(set(tasks)) or not tasks or any(type(t) is not int or t < 1 for t in tasks)
                or type(plan['repeat']) is not int or plan['repeat'] < 1
                or plan['slots'] != [f'{t}:{r}' for r in range(1, plan['repeat'] + 1) for t in tasks]):
            raise ValueError('invalid plan slots')
        if any(str(t) not in inputs for t in tasks):
            raise ValueError('plan input not registered')
    for directory in ('resources', 'plans', 'launches', 'predictions'):
        for member in (root / directory).rglob('*'):
            if member.is_symlink():
                raise ValueError('experiment member symlink: ' + str(member))
    for taskdir in (root / 'tasks').glob('*'):
        plain(taskdir)
        for attempt in taskdir.glob('*'):
            plain(attempt)
            for name in ('attempt.json', 'run.json', 'events.jsonl', 'stdout.log', 'stderr.log', 'output/prediction.csv'):
                plain(attempt / name)
    allowed = {'experiment.json', 'config.resolved.yaml', 'resources', 'plans', 'launches', 'tasks',
               'selection.json', 'predictions', 'reports', 'report-set.json', 'summary.json', 'score.json', 'summary.csv', '.experiment.lock'}
    for p in root.iterdir():
        if p.name not in allowed and not (p.name.startswith('.') and p.name.endswith('.tmp')):
            raise ValueError('unknown experiment conflict: ' + p.name)
        if p.is_symlink():
            raise ValueError('experiment symlink')
    return doc


def registered_inputs(root, doc):
    return read(internal(root, doc['inputs_file']))


def plan_for(root, doc, pid=None):
    return read(root / 'plans' / (pid or doc['active_plan']) / 'plan.json')


def attempts(root, doc):
    records, sequences = [], set()
    for taskdir in sorted((root / 'tasks').glob('*')):
        if not re.fullmatch(r'task_[1-9][0-9]*', taskdir.name):
            raise ValueError('invalid task directory')
        for directory in sorted(taskdir.glob('*')):
            if not re.fullmatch(r'run_[1-9][0-9]*', directory.name):
                raise ValueError('invalid attempt directory')
            internal(root, str(directory.relative_to(root)))
            if not (directory / 'attempt.json').exists():
                # Reserved before metadata reached disk: keep its number, never accept output.
                continue
            record = read(directory / 'attempt.json')
            if not isinstance(record, dict):
                raise ValueError('damaged attempt metadata')
            expected_id = directory.relative_to(root).as_posix()
            if record['attempt_id'] != expected_id or taskdir.name != f'task_{record["task"]}':
                raise ValueError('attempt association conflict')
            plan = plan_for(root, doc, record['plan_id']) if record['plan_id'] in doc['plans'] else None
            if plan is None or record['slot'] not in plan['slots'] or record['slot'] != f'{record["task"]}:{record["repeat"]}':
                raise ValueError('attempt references unknown plan slot')
            launch = read(internal(root, f'launches/{record["launch_id"]}.json'))
            if launch['plan_id'] != record['plan_id'] or record['slot'] not in launch['pending']:
                raise ValueError('attempt launch association conflict')
            seq = record['sequence']
            if type(seq) is not int or seq < 1 or seq in sequences:
                raise ValueError('attempt start sequence conflict')
            sequences.add(seq)
            records.append(record)
    records.sort(key=lambda a: a['sequence'])
    known = {a['attempt_id']: a for a in records}
    for a in records:
        prev = a.get('previous_attempt')
        if prev and (prev not in known or known[prev]['slot'] != a['slot']
                     or known[prev]['plan_id'] != a['plan_id'] or known[prev]['sequence'] >= a['sequence']):
            raise ValueError('invalid retry association')
    return records


def original(root, record):
    return internal(root, record['attempt_id']) / 'output/prediction.csv'


def execution_evidence(root, record):
    """Inspect lifecycle evidence without repairing or changing any original file."""
    directory = internal(root, record['attempt_id'])
    terminal = {'submitted', 'runtime_error', 'wall_time_exhausted', 'stopped',
                'model_output_exhausted', 'no_prediction', 'invalid_prediction'}
    unfinished = {'prepared', 'running', 'interrupted', 'execution_error'}
    outer = record['status']
    errors, missing, events, run = [], [], [], {}
    if outer not in terminal | unfinished:
        errors.append('unknown outer execution status')
    run_path = directory / 'run.json'
    if run_path.exists():
        try:
            run = read(run_path)
            if not isinstance(run, dict):
                raise ValueError('result object required')
        except (OSError, ValueError, UnicodeError):
            errors.append('invalid run.json')
            run = {}
    else:
        missing.append('runner result missing')
    path = directory / 'events.jsonl'
    if path.exists():
        try:
            lines = plain(path).read_text(encoding='utf-8').splitlines(keepends=True)
        except (OSError, UnicodeError):
            errors.append('unreadable runner events')
            lines = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                if outer in unfinished and i == len(lines) - 1 and not line.endswith('\n'):
                    missing.append('unfinished final event retained')
                else:
                    errors.append('invalid runner event JSON')
                continue
            if not isinstance(event, dict) or not isinstance(event.get('event'), str):
                errors.append('invalid runner event object')
                continue
            events.append(event)
    else:
        missing.append('runner events missing')
    starts = [e for e in events if e['event'] == 'run_started']
    ends = [e for e in events if e['event'] == 'run_finished']
    expected_task = f'task_{record["task"]}'
    if len(starts) > 1 or len(ends) > 1:
        errors.append('duplicate runner start/finish events')
    if not starts:
        missing.append('run_started missing')
    elif starts[0].get('task_id') != expected_task or events[0] is not starts[0]:
        errors.append('runner start task/order mismatch')
    if not ends:
        missing.append('run_finished missing')
    elif events[-1] is not ends[0]:
        errors.append('run_finished is not the final event')
    if any(e.get('task_id', expected_task) != expected_task for e in events):
        errors.append('runner event task mismatch')
    for item, label in [(run, 'runner result'), *[(e, 'runner finish') for e in ends]]:
        if label == 'runner result' and not run_path.exists():
            continue
        if not isinstance(item.get('status'), str) or item['status'] not in terminal:
            errors.append(label + ' has invalid status')
        if 'return_code' not in item or (item['return_code'] is not None and type(item['return_code']) is not int):
            errors.append(label + ' has invalid return code')
        if item.get('status') == 'submitted' and item.get('return_code') != 0:
            errors.append(label + ' submitted with nonzero/unknown return code')
        if outer in terminal and item.get('status') != outer:
            errors.append(label + ' conflicts with outer status')
    if run_path.exists():
        if run.get('task_id') != expected_task:
            errors.append('runner result task mismatch')
        if ends and any(run.get(k) != ends[0].get(k) for k in ('status', 'return_code')):
            errors.append('runner result/finish status or return code mismatch')
        usage = run.get('usage')
        if usage is not None and (not isinstance(usage, dict) or any(
                not isinstance(k, str) or type(v) is not int or v < 0 for k, v in usage.items())):
            errors.append('invalid runner usage')
    for name in ('stdout.log', 'stderr.log'):
        if not plain(directory / name).is_file():
            missing.append(name + ' missing')
    if outer in terminal:
        errors.extend(missing)
    elif not missing:
        missing.append('outer lifecycle unfinished; resume required')
    status = 'conflict' if errors else ('incomplete' if missing else 'consistent')
    return run, {'status': status, 'reasons': list(dict.fromkeys(errors or missing))}


def evidence(root, record):
    """Require frozen runner result, final event and CSV digest to agree."""
    from dataspace_baselines.core.prediction import validate_prediction
    directory = internal(root, record['attempt_id'])
    run, inspection = execution_evidence(root, record)
    if inspection['status'] == 'conflict':
        raise ValueError('; '.join(inspection['reasons']))
    events = [json.loads(line) for line in plain(directory / 'events.jsonl').read_text().splitlines() if line.strip()]
    if not isinstance(run, dict) or any(not isinstance(e, dict) for e in events):
        raise ValueError('invalid runner result/event schema')
    if (run.get('task_id') != f'task_{record["task"]}' or run.get('status') != 'submitted'
            or run.get('return_code') != 0 or not events or events[-1].get('event') != 'run_finished'
            or events[-1].get('status') != 'submitted' or events[-1].get('return_code') != 0):
        raise ValueError('incomplete submitted result/events')
    starts = [e for e in events if e.get('event') == 'run_started']
    validated = [e for e in events if e.get('event') == 'prediction_validated']
    if len(starts) != 1 or starts[0].get('task_id') != run['task_id'] or len(validated) != 1:
        raise ValueError('missing or conflicting runner evidence')
    for name in ('stdout.log', 'stderr.log'):
        if not plain(directory / name).is_file():
            raise ValueError('missing runner log')
    prediction = original(root, record)
    plain(prediction)
    metadata = validate_prediction(prediction)
    if not isinstance(validated[0].get('prediction'), dict) or validated[0]['prediction'].get('sha256') != metadata['sha256']:
        raise ValueError('prediction differs from validated runner digest')
    if record.get('prediction_sha256') and record['prediction_sha256'] != metadata['sha256']:
        raise ValueError('prediction differs from attempt digest')
    return run, metadata


def completed(root, record):
    if record['status'] != 'submitted' or not re.fullmatch(r'[0-9a-f]{64}', record.get('prediction_sha256') or ''):
        return False
    try:
        evidence(root, record)
        return True
    except (OSError, ValueError, KeyError, UnicodeError):
        return False


def select(records):
    return {str(a['task']): a['attempt_id'] for a in records}


def repair(root, doc):
    records = attempts(root, doc)
    write(root / 'resources/inputs.json', registered_inputs(root, doc))
    for a in records:
        if a['status'] in ('running', 'prepared', 'execution_error'):
            try:
                run, prediction = evidence(root, a)
            except (OSError, ValueError, KeyError, UnicodeError):
                if a['status'] != 'execution_error':
                    a.update(status='interrupted', recovery='incomplete evidence after interrupted launch')
            else:
                a.update(status='submitted', prediction_sha256=prediction['sha256'],
                         recovery='completed frozen result/events/digest recovered')
            write(internal(root, a['attempt_id']) / 'attempt.json', a)
    selection = select(records)
    write(root / 'selection.json', selection)
    by_id = {a['attempt_id']: a for a in records}
    for task, aid in selection.items():
        target = root / f'predictions/task_{task}/prediction.csv'
        if completed(root, by_id[aid]):
            source = original(root, by_id[aid])
            if not target.exists() or sha(plain(target)) != sha(source):
                atomic_bytes(target, source.read_bytes())
        else:
            plain(target).unlink(missing_ok=True)
    return records
