"""Complete a missing extraction note in a copy of the original candidate session."""

from dataclasses import replace
import csv
import hashlib


LIMIT_SECONDS = 60
MAX_NOTE_BYTES = 32 * 1024
ORIGINAL_OUTPUT = '/mnt/runtime/original-candidate'
PROMPT = f"""The prior turn finished computing its candidate but omitted extraction.md.
Use the previous turn's actual executed queries and files at {ORIGINAL_OUTPUT}
to document the existing candidate. Include source paths and locators, the
executed SQL or equivalent steps, joins and filters, grouping, sorting, and
column mapping as applicable. Refer to any scripts by their relative paths
in the original output. Mention uncertainty explicitly. If the prior work
is insufficient to describe the extraction, finish without writing a note.
Do not solve the question again, modify or export a CSV, or run a semantic
review. Write only /mnt/output/extraction.md as UTF-8. Save it promptly and
end this turn within {LIMIT_SECONDS} seconds.
"""


class CandidatePending(ValueError):
    """A single candidate cannot be handed off to review."""


def invocation_runner(config, original, output, session):
    runner = original.shared.run_v8.build_v8_harness(replace(config, wall_time_seconds=LIMIT_SECONDS))
    runner.termination_grace_seconds = 0
    runner.recover_nested_prediction = False
    runner.cleanup = None
    build = runner.invocation_factory

    def invocation(task, directory, writable):
        base = build(task, directory, writable)
        command = list(base.command)
        boundary = command.index('--')
        mounts = command[:boundary]
        workspace = next(i for i in range(2, len(mounts))
                         if mounts[i - 2] == '--ro-bind' and mounts[i] == '/mnt/workspace')
        mounts[workspace - 1] = str(output)
        mounts.extend(('--ro-bind', str(output), ORIGINAL_OUTPUT))
        inner = (command[boundary + 1], 'exec', 'resume', '--json', '--strict-config',
                 '--skip-git-repo-check', '--ignore-rules', session, PROMPT)
        return replace(base, command=(*mounts, '--', *inner),
                       metadata={**base.metadata, 'stage': 'candidate_extraction_repair', 'continued_session': session})

    runner.invocation_factory = invocation
    return runner


def note_bytes(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_NOTE_BYTES:
        raise CandidatePending('补件未生成有效的 extraction.md')
    data = path.read_bytes()
    try:
        if data.decode('utf-8').strip():
            return data
    except UnicodeError:
        pass
    raise CandidatePending('补件说明为空或 UTF-8 无效')


def complete(path, task, config, initial, original, global_failure, *, retry_service_failure=False):
    shared = original.shared
    output = path / 'output'
    candidate = output / 'prediction.csv'
    if candidate.is_symlink() or not candidate.is_file():
        raise CandidatePending('补件前缺少规范候选 CSV')
    try:
        prediction = shared.validate_prediction(candidate)
        session = original.session_id(path / 'stdout.log')
        session_file = original.session_file(path, session)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, csv.Error) as exc:
        raise CandidatePending('候选 CSV 无效或原会话无法续接') from exc
    source_session_sha = shared.digest(session_file)
    repair = path / 'repair'
    summary = repair / 'run.json'
    state = repair / 'attempt.json'
    identity = {'session_id': session, 'source_session_sha256': source_session_sha,
                'candidate_sha256': prediction['sha256'], 'limit_seconds': LIMIT_SECONDS}
    if repair.exists() and (not state.is_file() or shared.read_json(state) != identity):
        raise ValueError('补件输入或尝试记录摘要不一致')
    if retry_service_failure and summary.is_file() and global_failure(repair):
        original.archive(repair)
    if not repair.exists():
        repair.mkdir()
        shared.write_json(state, identity)
        original.copy_session(path, repair, session)
        invocation_runner(config, original, output, session).run(task, repair)
    if not summary.is_file():
        raise CandidatePending('补件会话中断，未生成运行记录')
    if failure := global_failure(repair):
        raise ValueError(f'{task.task_id}: {failure}')
    result = shared.read_json(summary)
    if result['status'] == 'stopped':
        raise KeyboardInterrupt
    if shared.digest(candidate) != prediction['sha256'] or shared.digest(session_file) != source_session_sha:
        raise ValueError('原候选 CSV 或原会话在补件过程中发生变化')
    if result['status'] != 'no_prediction':
        raise CandidatePending(f'补件未完成（{result["status"]}）')
    try:
        resumed = original.session_id(repair / 'stdout.log')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CandidatePending('补件缺少结束记录') from exc
    if resumed != session:
        raise CandidatePending('补件未续接原会话')
    repaired_output = repair / 'output'
    if (repaired_output / 'prediction.csv').exists() or (repaired_output / 'prediction.csv').is_symlink():
        raise CandidatePending('补件产生了额外的 CSV')
    note = repaired_output / 'extraction.md'
    data = note_bytes(note)
    usage = {key: val - initial['usage'].get(key, 0) for key, val in result['usage'].items()}
    if not all(key in initial['usage'] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')) or any(
        val < 0 for val in usage.values()
    ):
        usage = {}
    receipt = {**identity, 'extraction_sha256': hashlib.sha256(data).hexdigest(),
               'wall_time_seconds': result['wall_time_seconds'], 'usage': usage}
    shared.write_json(repair / 'receipt.json', receipt)
    temporary = path / 'extraction-repair.tmp'
    temporary.write_bytes(data)
    temporary.replace(output / 'extraction.md')
    saved = {**shared.checkpoint(path), 'usage': initial['usage'],
             'wall_time_seconds': initial['wall_time_seconds'], 'extraction_repair': receipt}
    if saved['extraction_sha256'] != receipt['extraction_sha256']:
        raise ValueError('补件发布后说明摘要不一致')
    return saved
