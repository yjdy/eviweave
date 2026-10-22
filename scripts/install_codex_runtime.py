#!/usr/bin/env python3
"""Prepare only Codex CLI 0.145.0 (Linux x86_64); execute --version only."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import urllib.request

from runtime_support import PROJECT, copy_verified, differences, extract_checked, inventory, local_path, sha256, write_json

VERSION = '0.145.0'
PLATFORM_PACKAGE = '0.145.0-linux-x64'
METADATA_URL = 'https://registry.npmjs.org/@openai/codex/' + PLATFORM_PACKAGE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=PROJECT/'.runtimes')
    parser.add_argument('--cache-dir', type=Path, default=PROJECT/'.audit/p2/cache')
    parser.add_argument('--temp-dir', type=Path, default=PROJECT/'.audit/p2/tmp')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--from-dir', type=Path, help='verified platform-package directory')
    source.add_argument('--from-archive', type=Path, help='official npm platform package tarball')
    parser.add_argument('--sha256', help='required digest for a supplied archive')
    parser.add_argument('--manifest', type=Path, help='expected directory inventory; required for directory import')
    args = parser.parse_args(argv)
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('only Linux x86_64 is supported')
    runtime_root = local_path(args.runtime_root); cache = local_path(args.cache_dir); temp = local_path(args.temp_dir)
    for path in (runtime_root, cache, temp): path.mkdir(parents=True, exist_ok=True)
    target = runtime_root/f'codex-{VERSION}'/'platform-package'
    provenance = {'version': VERSION, 'platform_package': PLATFORM_PACKAGE, 'kind': 'historical-directory' if args.from_dir else 'official-npm'}
    with tempfile.TemporaryDirectory(dir=temp, prefix='codex-') as temporary:
        stage = Path(temporary)
        if args.from_dir:
            if not args.manifest:
                raise ValueError('--manifest is required for a directory import')
            expected = json.loads(args.manifest.read_text())
            if differences(expected, inventory(args.from_dir)):
                raise ValueError('source directory does not match expected manifest')
            source_path = args.from_dir
        else:
            archive = args.from_archive
            if archive:
                if not args.sha256 or sha256(archive) != args.sha256:
                    raise ValueError('archive SHA-256 is required and must match')
                provenance['archive_sha256'] = args.sha256
            else:
                metadata_file = cache/f'codex-{PLATFORM_PACKAGE}.json'
                if not metadata_file.exists():
                    with urllib.request.urlopen(METADATA_URL, timeout=30) as response:
                        write_json(metadata_file, json.load(response))
                metadata = json.loads(metadata_file.read_text())
                if metadata['version'] != PLATFORM_PACKAGE or metadata['name'] != '@openai/codex':
                    raise ValueError('unexpected npm package identity')
                dist = metadata['dist']; url = dist['tarball']
                if not url.startswith('https://registry.npmjs.org/@openai/codex/-/'):
                    raise ValueError('unexpected package download origin')
                archive = cache/f'codex-{PLATFORM_PACKAGE}.tgz'
                if not archive.exists():
                    partial = archive.with_suffix('.partial')
                    with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as output:
                        while block := response.read(1024*1024): output.write(block)
                    partial.rename(archive)
                algorithm, encoded = dist['integrity'].split('-', 1)
                if algorithm != 'sha512': raise ValueError('expected npm SHA-512 integrity')
                digest = hashlib.sha512(archive.read_bytes()).digest()
                if digest != base64.b64decode(encoded): raise ValueError('npm integrity mismatch')
                provenance.update(url=url, integrity=dist['integrity'], archive_sha256=sha256(archive))
            unpack = stage/'unpack'; extract_checked(archive, unpack)
            source_path = unpack/'package'
            expected = inventory(source_path)
        check_source = stage/'version-check'
        subprocess.run(['cp', '-a', '--', str(source_path), str(check_source)], check=True)
        binary_rel = 'vendor/x86_64-unknown-linux-musl/bin/codex'
        package = json.loads((source_path/'package.json').read_text())
        if package.get('version') != PLATFORM_PACKAGE:
            raise ValueError('platform package version mismatch')
        # Use a minimal environment and project-local state; no login or exec.
        for directory in ('home', 'tmp'): (stage/directory).mkdir()
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(stage/'home'), 'CODEX_HOME': str(stage/'home/.codex'),
                       'TMPDIR': str(stage/'tmp'), 'LANG': 'C.UTF-8'}
        result = subprocess.run([str((check_source/binary_rel).resolve()), '--version'], env=environment,
                                cwd=stage, capture_output=True, text=True, timeout=30, check=True)
        if result.stdout.strip() != 'codex-cli '+VERSION: raise ValueError('binary version mismatch')
        action = copy_verified(source_path, target, temp, expected)
        record = target.parent/'runtime-files.json'
        if record.exists() and json.loads(record.read_text()) != expected:
            raise ValueError('stored Codex inventory differs')
        write_json(record, expected)
        historical_binary = 'a2a05dafaa1acb002a45eaec0a462de5b13694fcfcd7bc43305f14781ce7be14'
        provenance.update(binary_sha256=sha256(target/binary_rel),
                          historical_binary_match=sha256(target/binary_rel) == historical_binary,
                          historical_tree_comparison='verified import inventory' if args.from_dir else 'not authenticated by historical full-tree inventory',
                          version_output=result.stdout.strip(),
                          inventory_sha256=sha256(record))
        receipt = target.parent/'installation.json'
        if not receipt.exists(): write_json(receipt, provenance)
        print(json.dumps({'status': 'PASS', 'action': action, **provenance}))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        raise SystemExit(f'Codex preparation failed: {type(exc).__name__}: {exc}')
