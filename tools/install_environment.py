"""Install only the locked Python toolchain/package, never runtime or data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from environment_lock import PROFILE_ROOT, check_artifact, profile
from experiment_store import atomic_bytes, fingerprint, read, write
from standalone import PROJECT, clean_env, plain


def install(python, environment_profile=None, cache_dir=None):
    # All profile parsing and dependency selection use only the standard library.
    python = Path(os.path.abspath(python))
    venv = plain(python.parent.parent)
    if not venv.is_relative_to(PROJECT) or venv == PROJECT or not venv.name.startswith('.venv'):
        raise ValueError('installation target must be a project-local .venv* directory')
    if venv.exists() and (not (venv / 'pyvenv.cfg').is_file() or not python.exists()):
        raise ValueError('existing environment is incomplete; refusing to replace it')
    selected = environment_profile or os.environ.get('EVIWEAVE_ENVIRONMENT_PROFILE')
    if python.exists() and not selected:
        version = subprocess.check_output([str(python), '-B', '-c', 'import platform; print(platform.python_version())'],
                                          env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONNOUSERSITE': '1'}, text=True).strip()
        suffix = {'3.12.3': '312', '3.13.16': '313'}.get(version)
        if not suffix:
            raise ValueError('no original exact profile for target Python; select an independent profile')
        selected = PROFILE_ROOT / f'cpython{suffix}-linux-x86_64.json'
    doc = profile(selected)
    from public_resources import private_path
    pid = doc.get('dependency_profile', doc['profile_id'])
    cache = private_path(cache_dir) if cache_dir else PROJECT / '.local/packages' / pid
    state = cache / 'install-state'
    state.mkdir(parents=True, exist_ok=True)
    environment = clean_env(state)
    environment.update(PIP_CONFIG_FILE='/dev/null', PIP_DISABLE_PIP_VERSION_CHECK='1',
                       PIP_CACHE_DIR=str(state / 'pip-cache'))
    def run(command):
        subprocess.run(command, env=environment, check=True)
    # venv Python itself is commonly a symlink; validate its enclosing target instead.
    if not venv.exists():
        run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(venv)])
    marker = venv / 'eviweave-profile.json'
    if marker.exists() and read(marker) != {'profile_sha256': fingerprint(doc)}:
        raise ValueError('existing environment belongs to another profile; choose a fresh .venv*')
    # Reject drift in an existing environment before pip can silently upgrade it.
    snapshot = PROJECT / '.local/install-profiles' / (fingerprint(doc) + '.json')
    if snapshot.exists() and read(snapshot) != doc:
        raise ValueError('installation profile snapshot conflict')
    write(snapshot, doc)
    script = 'import sys; sys.path.insert(0, sys.argv[1]); import environment_lock as e; d=e.profile(sys.argv[2]); e.check_python(d); import importlib.metadata as m; names={x.metadata["Name"] for x in m.distributions()}; e.check_packages(d, names)'
    run([str(python), '-B', '-c', script, str(PROJECT / 'tools'), str(snapshot)])
    cache.mkdir(parents=True, exist_ok=True)
    for item in doc['packages'].values():
        target = cache / item['filename']
        if not target.exists():
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                atomic_bytes(target, response.read())
        check_artifact(target, item)
    pip_wheel = cache / doc['packages']['pip']['filename']
    run([str(python), '-B', str(pip_wheel) + '/pip', 'install', '--no-index', '--find-links', str(cache),
         '--require-hashes', '-r', str(PROFILE_ROOT / (pid + '.txt'))])
    run([str(python), '-B', '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-build-isolation', str(PROJECT)])
    run([str(python), '-B', '-c', 'import sys; sys.path.insert(0,sys.argv[1]); import environment_lock as e; e.verify(runtime=False,doc=e.profile(sys.argv[2]))', str(PROJECT / 'tools'), str(snapshot)])
    write(marker, {'profile_sha256': fingerprint(doc)})
    config = PROJECT / '.local/config-v6/codex.yaml'
    if not config.exists():
        # PyYAML is available in the target, not necessarily in bootstrap Python.
        script = ('import sys,json; from pathlib import Path; import yaml; '
                  'p=Path(sys.argv[1]); d=json.loads(Path(sys.argv[2]).read_text()); '
                  'r=yaml.safe_load((p/"configs/codex.example.yaml").read_text()); '
                  'r["experiment"]["workbench"]["rootfs_path"]=sys.argv[4]; '
                  'r["experiment"]["workbench"]["image"]=sys.argv[6]; '
                  'r["harnesses"]["codex"]["runtime_path"]=sys.argv[5]; '
                  'target=Path(sys.argv[3]); target.parent.mkdir(parents=True,exist_ok=True); '
                  'target.open("x").write(yaml.safe_dump(r,allow_unicode=True))')
        runtime = doc.get('runtime', {})
        workbench = (runtime['workbench_root'] + '/rootfs' if doc['format_version'] == 2 else str(PROJECT / '.runtimes/data-workbench-1.0/rootfs'))
        codex = (runtime['codex_root'] + '/vendor/x86_64-unknown-linux-musl' if doc['format_version'] == 2 else str(PROJECT / '.runtimes/codex-{version}/platform-package/vendor/x86_64-unknown-linux-musl'))
        image = runtime.get('image', 'dataspace-workbench:1.0')
        run([str(python), '-B', '-c', script, str(PROJECT), str(snapshot), str(config), workbench, codex, image])
    print('Python package installed with locked dependencies. Edit .local/env.sh, then run scripts/experiment.sh check. Runtime/data/service authentication were not installed or tested.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--python', type=Path, required=True)
    p.add_argument('--environment-profile', type=Path, help='explicit reviewed project-local host profile; otherwise private/inherited setting or original exact default')
    p.add_argument('--cache-dir', type=Path, help='project-private wheel cache; existing artifacts are always checked')
    args = p.parse_args()
    try:
        install(args.python, args.environment_profile, args.cache_dir)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('install: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
