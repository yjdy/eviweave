"""Verify versioned host profiles and the independently imported historical runtime."""
from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import zipfile

from experiment_store import fingerprint, read, sha
from standalone import PROJECT, plain

PROFILE_ROOT = PROJECT / 'locks'


def profile_id():
    versions = {'3.12.3': 'cpython312-linux-x86_64', '3.13.16': 'cpython313-linux-x86_64'}
    if platform.python_version() not in versions or platform.machine() != 'x86_64' or sys.platform != 'linux':
        raise ValueError('no locked profile for this Python/platform; use CPython 3.12.3 or 3.13.16 on Linux x86_64')
    return versions[platform.python_version()]


def profile():
    doc = read(PROFILE_ROOT / (profile_id() + '.json'))
    if doc.get('format_version') != 1 or doc.get('profile_id') != profile_id():
        raise ValueError('unsupported environment profile')
    return doc


def check_python(doc):
    expected = doc['python']
    current = {'version': platform.python_version(), 'full_version': sys.version,
               'implementation': platform.python_implementation(), 'machine': platform.machine(),
               'soabi': sysconfig.get_config_var('SOABI'), 'sha256': sha(Path(sys.executable).resolve())}
    if current != expected:
        raise ValueError('Python version/ABI/binary differs from the locked profile')
    return current


def check_packages(doc, names=None):
    result = {}
    for name, item in doc['packages'].items():
        if names is not None and name not in names:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError as exc:
            raise ValueError('locked package missing: ' + name + '; run scripts/install.sh') from exc
        if dist.version != item['version']:
            raise ValueError('locked package version differs: ' + name)
        for relative, expected in item['files'].items():
            path = Path(dist.locate_file(relative))
            if not path.is_file() or sha(path) != expected:
                raise ValueError('installed package content differs: ' + name + '/' + relative)
        # File lists come from the authenticated wheel, not the installed RECORD.
        result[name] = {'version': dist.version, 'artifact_sha256': item['sha256'],
                        'content_sha256': fingerprint(item['files'])}
    return result


def check_host(doc):
    current = {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
               'os_release': sha('/etc/os-release')}
    if current != doc['host']:
        raise ValueError('host OS/kernel/architecture differs from locked profile')
    for tool, expected in doc['tools'].items():
        executable = shutil.which(tool)
        if not executable or sha(executable) != expected['sha256']:
            raise ValueError(tool + ' missing or differs from locked binary; prepare existing runtime manually')
        output = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=20, check=True)
        if output.stdout.strip() != expected['version']:
            raise ValueError(tool + ' version differs')
    return current


def check_runtime(config, doc):
    sys.path.insert(0, str(PROJECT / 'scripts'))
    from runtime_support import inventory
    baseline = read(PROJECT / 'provenance/runtime-baseline.json')
    locked = doc['runtime']
    if sha(PROJECT / 'provenance/runtime-baseline.json') != locked['baseline_sha256']:
        raise ValueError('runtime baseline changed')
    options = config.harnesses['codex'].options
    if str(options.get('version')) != '0.145.0':
        raise ValueError('Codex version must match historical 0.145.0')
    runtime = plain(str(options['runtime_path']).replace('{version}', '0.145.0'))
    # The imported inventory includes the platform package and workbench manifest.
    codex_root = runtime.parent.parent
    workbench = plain(config.workbench.rootfs_path).parent
    for name, root, inv_path, expected in (
        ('codex', codex_root, PROJECT / '.runtimes/codex-0.145.0/runtime-files.json', locked['codex_inventory_sha256']),
        ('workbench', workbench, PROJECT / '.runtimes/data-workbench-1.0-files.json', locked['workbench_inventory_sha256'])):
        if not inv_path.is_file() or sha(inv_path) != expected:
            raise ValueError(name + ' inventory missing/changed; prepare verified historical runtime manually')
        if inventory(root) != read(inv_path):
            raise ValueError(name + ' full runtime contents differ from locked historical copy')
    rootfs = config.workbench.rootfs_path
    for filename, expected in locked['package_manifests'].items():
        if sha(rootfs / 'opt/dataspace' / filename) != expected:
            raise ValueError('workbench package inventory differs')
    if sha(runtime / 'bin/codex') != baseline['historical_identity']['codex_binary_sha256']:
        raise ValueError('Codex binary differs')
    return locked


def verify(config=None, runtime=True, doc=None):
    doc = doc or profile()
    check_python(doc)
    packages = check_packages(doc)
    if runtime:
        check_host(doc)
        check_runtime(config, doc)
    return {'format_version': 1, 'profile_id': doc['profile_id'], 'profile_sha256': fingerprint(doc),
            'profile': doc, 'installed_packages': packages}


def wheel_files(path):
    with zipfile.ZipFile(path) as archive:
        return {n: __import__('hashlib').sha256(archive.read(n)).hexdigest()
                for n in archive.namelist() if not n.endswith('/') and not n.endswith('.dist-info/RECORD')}


def check_artifact(path, entry):
    if Path(path).name != entry['filename'] or sha(path) != entry['sha256']:
        raise ValueError('dependency artifact SHA-256 mismatch')
    if wheel_files(path) != entry['files']:
        raise ValueError('dependency artifact contents mismatch')
