"""Opt-in V8 Codex 0.170.0 identity; retained historical code stays unchanged."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import stat
import sys
from dataclasses import replace

PROJECT = Path(__file__).resolve().parents[1]
VERSION = '0.170.0'
RELEASE_LOCK = PROJECT / 'provenance/codex-0.170.0-lock.json'
VENDOR = Path('vendor/x86_64-unknown-linux-musl')
FEATURE_OVERRIDES = {'goals': False}  # 0.145.0 did not expose automatic goal continuation.


def sha(path):
    with Path(path).open('rb') as handle:
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()


def contents(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Codex package must be a real directory')
    result = {}
    for p in sorted(root.rglob('*')):
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('unsupported Codex package member')
        result[p.relative_to(root).as_posix()] = {'mode': stat.S_IMODE(mode),
            **({'sha256': sha(p)} if p.is_file() else {'directory': True})}
    return result


def verify_runtime(package):
    lock = json.loads(RELEASE_LOCK.read_text())
    if lock['version'] != VERSION or contents(package) != lock['contents']:
        raise ValueError('Codex 0.170.0 package differs from its independent release lock')
    return {'version': VERSION, 'release_lock_sha256': sha(RELEASE_LOCK),
            'binary_sha256': lock['binary_sha256']}


def attach(runner, config):
    """Validate only the opt-in runtime, without changing prompt or budget."""
    harness = config.harnesses['codex']
    if str(harness.options.get('version')) != VERSION:
        return runner
    if harness.options.get('wire_api') != 'responses':
        raise ValueError('experimental Codex requires Responses')
    runtime = Path(str(harness.options['runtime_path']).replace('{version}', VERSION)).resolve()
    if not runtime.is_relative_to(PROJECT) or runtime.parts[-2:] != tuple(VENDOR.parts):
        raise ValueError('experimental Codex must use a project-local locked package')
    identity = verify_runtime(runtime.parent.parent)
    runner.resume_identity['codex_upgrade'] = {**identity, 'adapter_sha256': sha(__file__),
                                              'feature_overrides': FEATURE_OVERRIDES.copy()}
    original = runner.invocation_factory
    def invocation(task, run_root, output_root):
        # Verify again at dispatch: a saved manifest must not conceal changed code.
        if sha(runtime / 'bin/codex') != identity['binary_sha256'] or sha(RELEASE_LOCK) != identity['release_lock_sha256']:
            raise ValueError('experimental Codex binary changed after runner creation')
        inv = original(task, run_root, output_root)
        path = run_root / 'runtime/home/.codex/config.toml'
        content = path.read_text()
        if content.count('[features]\n') != 1:
            raise ValueError('unexpected frozen Codex configuration shape')
        path.write_text(content.replace('[features]\n', '[features]\ngoals = false\n', 1))
        return replace(inv, metadata={**inv.metadata, 'codex_upgrade': runner.resume_identity['codex_upgrade']})
    runner.invocation_factory = invocation
    return runner


def configure(base, runtime, output):
    """Copy a deliberately selected config; change only Codex version/path."""
    import yaml
    sys.path.insert(0, str(PROJECT / 'src'))
    from dataspace_baselines.config import load_harness_experiment_config
    from standalone import fresh
    base, runtime = Path(base).resolve(), Path(runtime).resolve()
    if not runtime.is_relative_to(PROJECT):
        raise ValueError('upgraded runtime must be project-local')
    output = fresh(output)
    if not output.is_relative_to(PROJECT / '.local'):
        raise ValueError('upgraded configuration must be under .local')
    verify_runtime(runtime)
    config = load_harness_experiment_config(base)
    if config.model.api_key_env != 'EVIWEAVE_API_KEY' or config.harnesses['codex'].options.get('wire_api') != 'responses':
        raise ValueError('expected dedicated credential variable and Responses')
    if str(config.harnesses['codex'].options.get('version')) != '0.145.0':
        raise ValueError('configuration must start from retained Codex 0.145.0')
    raw = yaml.safe_load(base.read_text())
    upgraded = copy.deepcopy(raw)
    upgraded['experiment']['workbench']['rootfs_path'] = str(config.workbench.rootfs_path)
    upgraded['harnesses']['codex'].update(version=VERSION, runtime_path=str(runtime / VENDOR))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        json.dump(upgraded, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    actual = load_harness_experiment_config(output)
    if (actual.model, actual.workbench, actual.wall_time_seconds, actual.concurrency) != (
            config.model, config.workbench, config.wall_time_seconds, config.concurrency):
        raise ValueError('upgrade changed model/workbench/budget')
    return {'config': str(output), 'version': VERSION, 'model_calls': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-config', type=Path, required=True)
    p.add_argument('--runtime', type=Path, default=PROJECT / '.runtimes/codex-0.170.0/platform-package')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(configure(a.base_config, a.runtime, a.output)))


if __name__ == '__main__':
    main()
