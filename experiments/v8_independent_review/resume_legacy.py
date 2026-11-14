"""Import a stopped public60 batch into a new recovery run."""

import fcntl
import shutil
from pathlib import Path
import tempfile

import light_review as light
import review_common as original


shared = original.shared


def verify_candidate(directory, task, config, fingerprint):
    if (directory / 'checkpoint.json').is_file():
        return original.candidate_check(directory, task, config)['candidate']['sha256'], None
    event = original.first_event(directory / 'events.jsonl')
    if (event['experiment_fingerprint'] != fingerprint or event['task_id'] != task.task_id
            or event['question'] != task.question or Path(event['workspace']) != task.context_dir):
        raise ValueError(f'{task.task_id} 旧候选会话身份不一致')
    result = shared.read_json(directory / 'run.json')
    if result['status'] == 'submitted':
        return shared.checkpoint(directory)['candidate']['sha256'], None
    candidate = directory / 'output/prediction.csv'
    if result.get('return_code') != 0 or not candidate.is_file() or candidate.is_symlink():
        return None, None
    prediction = shared.validate_prediction(candidate)
    session = original.session_id(directory / 'stdout.log')
    trace = original.session_file(directory, session)
    return prediction['sha256'], {'path': str(trace.relative_to(directory)), 'sha256': shared.digest(trace)}


def inventory(source, config, tasks, identity, manifest):
    legacy_identity = manifest['identity']
    keys = ('baseline', 'config_path', 'model', 'limits', 'candidate_limit_seconds',
            'task_ids', 'pdf_tasks', 'inputs', 'reused_checkpoints', 'historical_v8_score_sha256')
    if any(legacy_identity[k] != identity[k] for k in keys):
        raise ValueError('旧批次输入、配置或复审预算与当前实验不一致')
    fingerprint = shared.candidate_runner(config).resume_fingerprint
    reused = identity['reused_checkpoints']
    candidates, completed = {}, {}
    for task in tasks.values():
        name = task.task_id
        folder = source / name
        if light.signature(folder / 'evidence') != manifest['evidence'][name]:
            raise ValueError(f'{name} 旧批次证据包摘要不一致')
        candidate_sha = reused[name]['candidate']['sha256'] if name in reused else None
        if (folder / 'candidate/run.json').is_file():
            candidate_sha, trace = verify_candidate(folder / 'candidate', task, config, fingerprint)
            candidates[name] = {'fingerprint': fingerprint, 'prediction_sha256': candidate_sha, 'trace': trace}
        result = folder / 'result.json'
        if result.is_file():
            receipt = shared.read_json(result)
            prediction_sha = shared.validate_prediction(folder / 'prediction.csv')['sha256']
            if (receipt['prediction_sha256'] != prediction_sha or candidate_sha is None
                    or receipt['candidate_sha256'] != candidate_sha):
                raise ValueError(f'{name} 旧批次结果或候选摘要不一致')
            completed[name] = prediction_sha
    return {'candidates': candidates, 'completed': completed}


def migrate(source, target, config, tasks, identity):
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('续跑输出须独立于旧目录')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f'续跑目录已存在 {target}')
    with (source / '.run.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = shared.read_json(source / 'experiment.json')
        inventory_before = inventory(source, config, tasks, identity, manifest)
        receipt = {'source': str(source), 'source_manifest_sha256': shared.digest(source / 'experiment.json'),
                   **inventory_before}
        staging = Path(tempfile.mkdtemp(prefix='.legacy-import-', dir=target.parent))
        try:
            for task in tasks.values():
                shutil.copytree(source / task.task_id, staging / task.task_id, symlinks=True)
            if inventory(staging, config, tasks, identity, manifest) != inventory_before:
                raise ValueError('导入后的结果或候选与旧批次不一致')
            if (source / 'score.json').is_file():
                shutil.copyfile(source / 'score.json', staging / 'score.json')
            shared.write_json(staging / 'legacy-import.json', receipt)
            shared.write_json(staging / 'experiment.json', {**manifest, 'identity': identity,
                                'legacy_import_sha256': shared.digest(staging / 'legacy-import.json')})
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    print(f'已导入 {len(receipt["completed"])} 题结果和 {len(receipt["candidates"])} 份本地候选记录', flush=True)
