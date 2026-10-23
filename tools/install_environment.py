"""Install only the locked Python toolchain/package, never runtime or data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from environment_lock import PROFILE_ROOT, check_artifact
from experiment_store import atomic_bytes, read
from standalone import PROJECT, plain


def install(python):
    # venv Python itself is commonly a symlink; validate its enclosing target instead.
    python = Path(os.path.abspath(python))
    venv = plain(python.parent.parent)
    if not venv.is_relative_to(PROJECT) or venv == PROJECT or not venv.name.startswith('.venv'):
        raise ValueError('installation target must be a project-local .venv* directory')
    if venv.exists():
        if not (venv / 'pyvenv.cfg').is_file() or not python.exists():
            raise ValueError('existing environment is incomplete; refusing to replace it')
    else:
        subprocess.run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(venv)], check=True)
    version = subprocess.check_output([str(python), '-B', '-c', 'import platform; print(platform.python_version())'], text=True).strip()
    suffix = {'3.12.3': '312', '3.13.16': '313'}.get(version)
    if suffix is None:
        raise ValueError('Python profile mismatch; select existing CPython 3.12.3 or 3.13.16, no automatic replacement')
    pid = f'cpython{suffix}-linux-x86_64'
    doc = read(PROFILE_ROOT / (pid + '.json'))
    # Reject drift in an existing environment before pip can silently upgrade it.
    script = 'import sys; sys.path.insert(0, sys.argv[1]); import environment_lock as e; d=e.profile(); e.check_python(d); import importlib.metadata as m; names={x.metadata["Name"] for x in m.distributions()}; e.check_packages(d, names)'
    subprocess.run([str(python), '-B', '-c', script, str(PROJECT / 'tools')], check=True)
    cache = PROJECT / '.local/packages' / pid
    cache.mkdir(parents=True, exist_ok=True)
    for item in doc['packages'].values():
        target = cache / item['filename']
        if not target.exists():
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                atomic_bytes(target, response.read())
        check_artifact(target, item)
    pip_wheel = cache / doc['packages']['pip']['filename']
    subprocess.run([str(python), '-B', str(pip_wheel) + '/pip', 'install', '--no-index', '--find-links', str(cache),
                    '--require-hashes', '-r', str(PROFILE_ROOT / (pid + '.txt'))], check=True)
    subprocess.run([str(python), '-B', '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', str(PROJECT)], check=True)
    subprocess.run([str(python), '-B', '-c', 'import sys; sys.path.insert(0,sys.argv[1]); import environment_lock as e; e.verify(runtime=False)', str(PROJECT / 'tools')], check=True)
    config = PROJECT / '.local/config-v6/codex.yaml'
    if not config.exists():
        import yaml
        data = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
        data['experiment']['workbench']['rootfs_path'] = str(PROJECT / '.runtimes/data-workbench-1.0/rootfs')
        data['harnesses']['codex']['runtime_path'] = str(PROJECT / '.runtimes/codex-{version}/platform-package/vendor/x86_64-unknown-linux-musl')
        atomic_bytes(config, yaml.safe_dump(data, allow_unicode=True).encode())
    print('Python package installed with locked dependencies. Edit .local/env.sh, then run scripts/experiment.sh check. Runtime/data/service authentication were not installed or tested.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--python', type=Path, required=True)
    args = p.parse_args()
    try:
        install(args.python)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('install: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
