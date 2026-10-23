"""Explicit configuration, task selection and immutable experiment identities."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from environment_lock import verify as verify_environment
from experiment_store import fingerprint, read, sha
from standalone import PROJECT, plain, read_lock, verify_evaluation


def tasks_for(scope=None, tasks=None):
    if tasks is not None:
        if scope is not None or not tasks or any(type(t) is not int or t < 1 or t > 410 for t in tasks):
            raise ValueError('use --tasks IDs 1..410 or --scope, not both')
        return sorted(set(tasks))
    if scope == 'all':
        return list(range(1, 411))
    return sorted(int(Path(n).stem.removeprefix('task_')) for n in read_lock()['files'] if n.startswith('configs/'))


def resolved(path):
    import yaml
    from dataspace_baselines.config import load_harness_experiment_config
    path = plain(path)
    config = load_harness_experiment_config(path)
    if config.model.api_key_env != 'EVIWEAVE_API_KEY':
        raise ValueError('only dedicated EVIWEAVE_API_KEY is accepted')
    url = urlsplit(config.model.openai_base_url)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.hostname == 'example.invalid' or url.hostname.endswith('.invalid')):
        raise ValueError('configure an HTTPS Responses endpoint without embedded credentials before check/run')
    raw = yaml.safe_load(path.read_text())
    raw['experiment']['workbench']['rootfs_path'] = str(config.workbench.rootfs_path)
    raw['harnesses']['codex']['runtime_path'] = config.harnesses['codex'].options['runtime_path']
    # Never serialize arbitrary unrecognized configuration fields containing key values.
    allowed_experiment = {'model', 'workbench', 'wall_time_seconds', 'concurrency', 'sandbox_image'}
    if set(raw) != {'experiment', 'harnesses'} or set(raw['experiment']) - allowed_experiment:
        raise ValueError('unsupported configuration fields in unified format')
    if set(raw['experiment']['model']) - {'key', 'model', 'api_key_env', 'openai_base_url', 'anthropic_base_url', 'max_output_tokens', 'context_window'}:
        raise ValueError('unsupported model fields; credential values must remain in environment')
    if config.harnesses['codex'].options.get('wire_api') != 'responses':
        raise ValueError('unified experiments require the frozen Responses policy')
    encoded = (json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    return config, encoded


def source_identity():
    import dataspace_baselines
    installed = Path(dataspace_baselines.__file__).resolve().parent
    lock = read(PROJECT / 'provenance/extraction-manifest.json')
    files = {}
    for entry in lock['files']:
        digest = sha(PROJECT / entry['destination'])
        if digest != entry['release_sha256']:
            raise ValueError('frozen business source changed: ' + entry['destination'])
        relative = Path(entry['destination']).relative_to('src/dataspace_baselines')
        if sha(installed / relative) != digest:
            raise ValueError('installed business module differs from frozen source: ' + str(relative))
        files[entry['destination']] = digest
    for filename in ('experiment.py', 'experiment_config.py', 'experiment_store.py', 'experiment_scheduler.py',
                     'experiment_verify.py', 'environment_lock.py', 'standalone.py'):
        files['tools/' + filename] = sha(PROJECT / 'tools' / filename)
    return files


def task_snapshot(dataset, tasks):
    from dataspace_baselines.core.task import TaskSpec
    dataset = plain(dataset)
    result = {}
    for number in tasks:
        directory = plain(dataset / f'input/task_{number}')
        plain(directory / 'task.json')
        plain(directory / 'context')
        task = TaskSpec.load(directory)
        if task.task_id != f'task_{number}':
            raise ValueError('task ID does not match input directory')
        files = {}
        for path in sorted(directory.rglob('*')):
            plain(path)
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = sha(path)
            elif not path.is_dir():
                raise ValueError('task contains non-regular file')
        result[str(number)] = files
    return result


def prepare(config_path, dataset, evaluation, tasks, environment_verifier=verify_environment):
    config_path, dataset, evaluation = map(plain, (config_path, dataset, evaluation))
    if not dataset.is_dir() or not (dataset / 'input').is_dir():
        raise ValueError('dataset must contain input/; no discovery or fallback')
    config, encoded = resolved(config_path)
    evaluated = verify_evaluation(evaluation)
    environment = environment_verifier(config)
    identity = {'config_sha256': __import__('hashlib').sha256(encoded).hexdigest(),
                'source': source_identity(), 'environment_sha256': fingerprint(environment),
                'evaluation': evaluated, 'dataset': str(dataset)}
    # environment-lock.json uses the same canonical representation as fingerprint.
    return {'config': config, 'config_bytes': encoded, 'environment': environment, 'identity': identity,
            'inputs': task_snapshot(dataset, tasks),
            'paths': {'config': str(config_path), 'dataset': str(dataset), 'evaluation': str(evaluation)}}


def defaults(name, fallback):
    return Path(os.environ.get(name) or str(PROJECT / fallback)).expanduser()
