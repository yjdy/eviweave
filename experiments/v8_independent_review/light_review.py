"""Run the two bounded phases of independent review."""

from dataclasses import replace
import json
import shutil
from uuid import UUID

import review_common as original

shared = original.shared
review = original.REVIEW
LIMITS = {'B1': 180, 'B2': 180}
EVIDENCE_MOUNT = '/mnt/runtime/selected-evidence'
CANDIDATE_MOUNT = '/mnt/runtime/shared-candidate'
SCOPE_MOUNT = '/mnt/runtime/independent-scope.json'
SCOPE_NAME = 'scope.json'
DIMENSIONS = review.DIMENSIONS
FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens')

B1_PROMPT = f"""Establish the answer requirements before seeing a candidate. Read
{EVIDENCE_MOUNT}/evidence.json and view only relevant frames listed there. This is
the entire evidence available in stage one. Save uncertain judgments promptly
when you need the full originals, which will be mounted in stage two.
For ordering, distinguish batch membership from ordering within the
batch. For table selection, distinguish row/event meaning from similar field names.
Review source_table, scope, fields, order and output_columns. Do not generate
answer rows, compute full aggregates, build joins for the whole cohort, or scan
entire attachments. Save /mnt/output/{SCOPE_NAME} early, with one top-level key per
review dimension. Each value has status (clear or uncertain), decision (short
text), and evidence. Use a nonempty locator string, or a nonempty list of
locators such as [{{"source":"doc/example.pdf","page":4}}]. Each structured
locator needs a nonempty source and may include a page, time, field, clause,
or note. Save the first complete record before doing optional checks.
Use uncertain entries for open questions, then refine in place if evidence
supports it. Use /mnt/output as your working directory. Write updates atomically
using a temporary file and rename. Reserve
15 seconds to save and exit. End this turn after saving the scope record.
"""
B2_PROMPT = f"""Compare the sealed independent scope at {SCOPE_MOUNT} with
{CANDIDATE_MOUNT}/prediction.csv and extraction.md. Read only the relevant parts
of scripts or intermediates to confirm what was actually done. Use the originals
for any discrepancy that could change the answer. A documented finding of clear
evidence is needed to change the candidate. For a sort-only discrepancy reuse
the candidate rows and get ordering keys from the source. For a field or table
discrepancy, reuse the candidate joins after a small spot check and run only the
affected query if warranted. Keep the candidate for unresolved issues. Avoid
rebuilding the whole solution. Record the resolution and exact evidence for
each material discrepancy, including unresolved ones. Write the V8 review JSON
and check it locally. Write a valid CSV to a temporary file and rename it to
/mnt/output/prediction.csv. Reserve 15 seconds to finish writing and exit.
"""


def signature(directory):
    return {p.relative_to(directory).as_posix(): shared.digest(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def scope_record(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8000:
        raise ValueError('缺少有效的独立口径记录')
    data = shared.read_json(path)
    if not isinstance(data, dict) or set(data) != set(DIMENSIONS):
        raise ValueError('独立口径记录须覆盖五项')
    for item in data.values():
        if (not isinstance(item, dict) or set(item) != {'status', 'decision', 'evidence'}
                or item['status'] not in ('clear', 'uncertain')
                or not nonempty_text(item['decision']) or not valid_evidence(item['evidence'])):
            raise ValueError('独立口径记录的状态、判断或依据无效')
    return data


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def valid_evidence(value):
    if isinstance(value, str):
        return nonempty_text(value)
    if not isinstance(value, list) or not value:
        return False
    return all(
        nonempty_text(locator) or (isinstance(locator, dict)
                                   and nonempty_text(locator.get('source'))
                                   and all(nonempty_text(part) or type(part) in (int, float)
                                           for part in locator.values()))
        for locator in value
    )


def trace_session(directory, *, completed):
    if completed:
        return original.session_id(directory / 'stdout.log')
    sessions = set()
    with (directory / 'stdout.log').open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'thread.started':
                sessions.add(str(UUID(event['thread_id'])))
    if len(sessions) != 1:
        raise ValueError('超时阶段未留下唯一会话')
    return sessions.pop()


def trace_usage(result, directory, session, previous):
    cumulative = result.usage
    source = 'turn_completed'
    if not all(k in cumulative for k in FIELDS) and session:
        try:
            with original.session_file(directory, session).open(encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    payload = event.get('payload', {})
                    if payload.get('type') == 'token_count' and isinstance(payload.get('info'), dict):
                        candidate = payload['info'].get('total_token_usage')
                        if isinstance(candidate, dict) and all(type(candidate.get(k)) is int for k in FIELDS):
                            cumulative = {k: v for k, v in candidate.items() if k != 'total_tokens'}
                            source = 'last_persisted_snapshot'
        except (OSError, ValueError):
            pass
    if all(type(cumulative.get(k)) is int for k in FIELDS):
        usage = {k: v - previous.get(k, 0) for k, v in cumulative.items()}
        if all(v >= 0 for v in usage.values()) and usage['input_tokens'] >= usage['cached_input_tokens']:
            return usage, cumulative, source
    return None, {}, 'unavailable'


def create_runner(config, phase, task, packet, *, session=None, package=None, scope=None, limit=None):
    limit = limit or LIMITS[phase]
    runner = shared.run_v8.build_v8_harness(replace(config, wall_time_seconds=limit))
    runner.termination_grace_seconds = 0
    runner.recover_nested_prediction = False
    build = runner.invocation_factory

    def invocation(task, run_root, output_root):
        base = build(task, run_root, output_root)
        command = list(base.command)
        boundary = command.index('--')
        mounts = command[:boundary] + ['--ro-bind', str(packet), EVIDENCE_MOUNT]
        inner = command[boundary + 1:]
        if phase == 'B1':
            # Bind the selected originals in place of the full task workspace.
            for i, entry in enumerate(mounts):
                if entry == '/mnt/workspace' and i >= 2 and mounts[i - 2] == '--ro-bind':
                    mounts[i - 1] = str(packet)
                    break
            else:
                raise ValueError('找不到原件挂载位置')
            inner.remove('--ephemeral')
            inner[-1] = f'Question:\n{task.question}\n\n' + B1_PROMPT
        else:
            mounts += ['--ro-bind', str(package), CANDIDATE_MOUNT, '--ro-bind', str(scope), SCOPE_MOUNT]
            inner = [inner[0], 'exec', 'resume', '--json', '--strict-config', '--skip-git-repo-check',
                     '--ignore-rules', session, B2_PROMPT + review.INSTRUCTIONS]
        pdf = shared.run_v8.PDF_INSTRUCTIONS if phase == 'B2' and shared.run_v8.has_pdf(task) else ''
        inner[-1] += pdf + f'\nWall-clock allowance for this stage: {limit} seconds.\n'
        metadata = {**base.metadata, 'light_review_phase': phase, 'continued_session': session}
        return replace(base, command=tuple([*mounts, '--', *inner]), metadata=metadata)

    runner.invocation_factory = invocation
    if phase == 'B1':
        runner.cleanup = None
    return runner


def phase_stats(result, directory, session, previous, limit):
    usage, cumulative, source = trace_usage(result, directory, session, previous)
    return {'status': result.status, 'limit_seconds': limit, 'wall_time_seconds': result.wall_time_seconds,
            'tool_actions': result.tool_actions, 'usage': usage, 'cumulative_usage': cumulative,
            'usage_source': source, 'session_id': session}


def review_task(root, task, source, saved, config, *, runner_factory=create_runner, limits=LIMITS):
    directory = root / task.task_id
    packet = directory / 'evidence'
    candidate = source / saved['candidate_csv']
    candidate_hash = saved['candidate']['sha256']
    if shared.digest(candidate) != candidate_hash:
        raise ValueError('候选摘要不一致')
    scope_stage = directory / 'B1'
    result = runner_factory(config, 'B1', task, packet, limit=limits['B1']).run(task, scope_stage)
    if result.status == 'stopped':
        raise KeyboardInterrupt
    session = None
    sealed = directory / 'scope.sealed.json'
    reason = 'independent_scope_' + result.status
    if result.status in ('no_prediction', 'wall_time_exhausted'):
        try:
            scope = scope_stage / 'output' / SCOPE_NAME
            scope_record(scope)
            session = trace_session(scope_stage, completed=result.status == 'no_prediction')
            if session == saved['session_id']:
                raise ValueError('独立会话与候选相同')
            original.session_file(scope_stage, session)
            shutil.copyfile(scope, sealed)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reason = f'{reason}: {exc}'
    if session is None and result.status == 'wall_time_exhausted':
        try:
            session = trace_session(scope_stage, completed=False)
        except (OSError, ValueError, KeyError):
            pass
    b1 = phase_stats(result, scope_stage, session, {}, limits['B1'])
    receipt = {'candidate_sha256': candidate_hash, 'B1': b1, 'B2': None, 'decision': 'fallback'}
    final = candidate
    if sealed.is_file():
        receipt['sealed_scope_sha256'] = shared.digest(sealed)
        comparison = directory / 'B2'
        original.copy_session(scope_stage, comparison, session)
        package = comparison / 'shared-candidate'
        original.package_candidate(source, saved, package)
        # Only ship the compact candidate intermediate referenced for this pilot.
        for name in ('sample.json',):
            path = source / 'output' / name
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8000:
                shutil.copyfile(path, package / name)
        receipt['handoff_sha256'] = signature(package)
        result = runner_factory(config, 'B2', task, packet, session=session, package=package,
                                scope=sealed, limit=limits['B2']).run(task, comparison)
        if result.status == 'stopped':
            raise KeyboardInterrupt
        if signature(package) != receipt['handoff_sha256'] or shared.digest(sealed) != receipt['sealed_scope_sha256']:
            raise ValueError('复审交接资料发生变化')
        b2 = phase_stats(result, comparison, session, b1['cumulative_usage'], limits['B2'])
        if not b1['cumulative_usage']:
            b2.update(usage=None, usage_source='unknown_stage_boundary')
        receipt['B2'] = b2
        reason = 'comparison_' + result.status
        if result.status == 'submitted':
            try:
                if trace_session(comparison, completed=True) != session:
                    raise ValueError('B2 未续接独立会话')
                revised = comparison / 'output/prediction.csv'
                if revised.is_symlink():
                    raise ValueError('修订 CSV 是符号链接')
                shared.validate_prediction(revised)
                diagnostics = shared.read_json(comparison / review.REPORT_FILE)
                if diagnostics['status'] not in ('aligned', 'conflict', 'needs_review') or diagnostics['csv_header']['status'] != 'aligned':
                    raise ValueError('复审记录无效或表头不一致')
                final = revised
                receipt['decision'] = 'kept' if shared.digest(final) == candidate_hash else 'revised'
                reason = None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reason = f'{reason}: {exc}'
    if shared.digest(candidate) != candidate_hash:
        raise ValueError('共享候选发生变化')
    published = directory / 'prediction.csv'
    temporary = published.with_suffix('.tmp')
    shutil.copyfile(final, temporary)
    temporary.replace(published)
    receipt.update(reason=reason, prediction_sha256=shared.digest(published))
    shared.write_json(directory / 'result.json', receipt)
    print(f"{task.task_id} {receipt['decision']} {reason or ''}", flush=True)
    return receipt
