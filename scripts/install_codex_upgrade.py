#!/usr/bin/env python3
"""Install the separately locked V8 experimental Codex; never invoke inference."""
import argparse
import base64
import hashlib
import json
import platform
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from runtime_support import PROJECT, copy_verified, extract_checked, inventory, local_path, sha256, write_json

LOCK = PROJECT / 'provenance/codex-0.170.0-lock.json'


def install(runtime_root, cache, temporary):
    lock = json.loads(LOCK.read_text())
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('only Linux x86_64 supported')
    runtime_root, cache, temporary = map(local_path, (runtime_root, cache, temporary))
    for p in (runtime_root, cache, temporary):
        p.mkdir(parents=True, exist_ok=True)
    archive = cache / 'codex-0.170.0-linux-x64.tgz'
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        with urllib.request.urlopen(lock['url'], timeout=60) as response, partial.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        partial.rename(archive)
    if sha256(archive) != lock['sha256']:
        raise ValueError('upgrade archive SHA-256 mismatch')
    with archive.open('rb') as handle:
        digest = hashlib.sha512()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        integrity = 'sha512-' + base64.b64encode(digest.digest()).decode()
    if integrity != lock['integrity']:
        raise ValueError('upgrade archive npm integrity mismatch')
    target = runtime_root / 'codex-0.170.0/platform-package'
    with tempfile.TemporaryDirectory(dir=temporary, prefix='install-') as temp:
        stage = Path(temp)
        extract_checked(archive, stage / 'unpack')
        package = stage / 'unpack/package'
        metadata = json.loads((package / 'package.json').read_text())
        if metadata['name'] != '@openai/codex' or metadata['version'] != '0.170.0-linux-x64':
            raise ValueError('unexpected upgrade platform package')
        binary = package / lock['binary']
        if sha256(binary) != lock['binary_sha256']:
            raise ValueError('upgrade binary SHA-256 mismatch')
        (stage / 'home/.codex').mkdir(parents=True); (stage / 'tmp').mkdir()
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(stage / 'home'), 'CODEX_HOME': str(stage / 'home/.codex'),
               'TMPDIR': str(stage / 'tmp'), 'LANG': 'C.UTF-8'}
        version = subprocess.check_output([str(binary), '--version'], env=env, cwd=stage, timeout=20, text=True).strip()
        if version != 'codex-cli 0.170.0':
            raise ValueError('upgrade version mismatch')
        expected = inventory(package)
        action = copy_verified(package, target, temporary, expected)
        write_json(target.parent / 'runtime-files.json', expected)
        receipt = {'version': lock['version'], 'release_lock_sha256': sha256(LOCK),
                   'binary_sha256': lock['binary_sha256'], 'archive_sha256': lock['sha256'],
                   'inventory_sha256': sha256(target.parent / 'runtime-files.json'), 'model_calls': 0}
        write_json(target.parent / 'installation.json', receipt)
    return {'status': 'PASS', 'action': action, 'runtime': str(target), **receipt}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-root', type=Path, default=PROJECT / '.runtimes')
    p.add_argument('--cache', type=Path, default=PROJECT / '.audit/codex-upgrade-0170/cache')
    p.add_argument('--temporary', type=Path, default=PROJECT / '.audit/codex-upgrade-0170/tmp')
    a = p.parse_args()
    print(json.dumps(install(a.runtime_root, a.cache, a.temporary)))


if __name__ == '__main__':
    main()
