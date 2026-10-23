#!/usr/bin/env python3
"""Prepare pinned public resources in private project storage; never run inference."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import zlib

from experiment_store import atomic_bytes, read, sha, uid, write
from standalone import PROJECT, check_files, clean_env, plain, read_lock

LOCK = PROJECT / 'provenance/public-resources-lock.json'


def audit_root():
    root = plain(os.environ.get('EVIWEAVE_AUDIT_ROOT') or PROJECT / '.audit')
    if not root.is_relative_to(PROJECT / '.audit'):
        raise ValueError('audit root must stay under project .audit')
    return root


def private_path(value):
    path = plain(value)
    if not any(path.is_relative_to(PROJECT / p) and path != PROJECT / p
               for p in ('.audit', '.local', '.runtimes', 'data')):
        raise ValueError('preparation path must be a child of project private storage')
    if any(c in str(path) for c in ('\n', '\r', '\0')):
        raise ValueError('control character in preparation path')
    return path


def resource_lock():
    doc = read(LOCK)
    if doc['kind'] != 'eviweave-public-resources' or doc['format_version'] != 1:
        raise ValueError('unsupported public resource lock')
    if {e['path']: e['sha256'] for e in doc['scoring']} != read_lock()['files']:
        raise ValueError('public scoring mapping differs from frozen 121-file lock')
    return doc


def fetch(entry, destination):
    """No trust in existing cache; failed or corrupt downloads are never promoted."""
    destination = private_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    guard = private_path(destination.with_name(destination.name + '.lock'))
    with guard.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            if not destination.is_file() or destination.stat().st_size != entry['size'] or sha(destination) != entry['sha256']:
                raise ValueError('cached resource size/digest mismatch; refusing replacement: ' + destination.name)
            return destination
        if not entry['url'].startswith('https://'):
            raise ValueError('public resource requires HTTPS')
        fd, name = tempfile.mkstemp(dir=destination.parent, prefix='.download-')
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(entry['url'], timeout=60) as response:
                if response.status != 200:
                    raise ValueError('public download requires HTTP 200')
                count = 0
                while block := response.read(1024 * 1024):
                    count += len(block)
                    if count > entry['size']:
                        raise ValueError('download exceeds locked size')
                    out.write(block)
                out.flush(); os.fsync(out.fileno())
            if temporary.stat().st_size != entry['size'] or sha(temporary) != entry['sha256']:
                raise ValueError('download size/digest mismatch')
            temporary.rename(destination)
            write(destination.with_name(destination.name + '.receipt.json'),
                  {'url': entry['url'], 'size': entry['size'], 'sha256': entry['sha256'], 'status': 'verified'})
        except Exception as exc:
            write(destination.with_name(destination.name + '.failure.json'),
                  {'url': entry['url'], 'status': 'failed', 'error_type': type(exc).__name__})
            raise
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def range_bytes(url, start, end, total):
    if not (type(start) is int and type(end) is int and 0 <= start <= end < total):
        raise ValueError('invalid Range bounds')
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
            raise ValueError('Range response identity mismatch')
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise ValueError('Range response length mismatch')
    return data


def fetch_archive(entry, destination, chunk_size=16 * 1024 * 1024):
    """Bounded parallel Range download; only the complete locked SHA authenticates it."""
    destination = private_path(destination)
    if destination.exists():
        return fetch(entry, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    guard = private_path(destination.with_name(destination.name + '.archive-lock'))
    with guard.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists(): return fetch(entry, destination)
        parts = private_path(destination.with_name(destination.name + '.parts'))
        parts.mkdir(exist_ok=True)
        ranges = list(range(0, entry['size'], chunk_size))
        def one(start):
            end = min(start + chunk_size, entry['size']) - 1
            path = private_path(parts / str(start))
            receipt = private_path(parts / (str(start) + '.json'))
            if path.exists():
                previous = read(receipt)
                if (previous['url'] != entry['url'] or previous['start'] != start or previous['end'] != end
                        or path.stat().st_size != end - start + 1 or sha(path) != previous['sha256']):
                    raise ValueError('cached Range part size/digest mismatch')
            else:
                try:
                    data = range_bytes(entry['url'], start, end, entry['size'])
                except Exception as exc:
                    write(parts / (str(start) + '.failure-' + uid() + '.json'),
                          {'url': entry['url'], 'start': start, 'end': end, 'error_type': type(exc).__name__})
                    raise
                atomic_bytes(path, data)
                write(receipt, {'url': entry['url'], 'start': start, 'end': end, 'sha256': sha(path),
                                'authenticated_only_after_full_archive_hash': True})
            return path
        with ThreadPoolExecutor(max_workers=6) as pool:
            paths = list(pool.map(one, ranges))
        fd, name = tempfile.mkstemp(dir=destination.parent, prefix='.assemble-')
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as output:
                for path in paths:
                    with path.open('rb') as source: shutil.copyfileobj(source, output, 1024 * 1024)
                output.flush(); os.fsync(output.fileno())
            if temporary.stat().st_size != entry['size'] or sha(temporary) != entry['sha256']:
                raise ValueError('assembled archive size/digest mismatch')
            temporary.rename(destination)
            write(destination.with_name(destination.name + '.receipt.json'),
                  {'url': entry['url'], 'size': entry['size'], 'sha256': entry['sha256'], 'status': 'verified', 'range_parts': len(paths)})
            # The authenticated complete archive replaces temporary payload parts;
            # retain their receipts and hashes, without doubling data disk usage.
            for path in paths: path.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def scoring_member(entry, total):
    e = entry['zip']; off = e['local_header_offset']
    h = struct.unpack('<4s5H3L2H', range_bytes(entry['url'], off, off + 29, total))
    if h[0] != b'PK\x03\x04' or h[2] & 1 or h[3] != e['method'] or h[3] not in (0, 8):
        raise ValueError('invalid ZIP member header')
    ln, le = h[-2:]
    payload = range_bytes(entry['url'], off + 30, off + 29 + ln + le + e['compressed_size'], total)
    if payload[:ln].decode('utf-8') != entry['member']:
        raise ValueError('ZIP member path differs')
    body = payload[ln + le:]
    if e['method'] == 8:
        decoder = zlib.decompressobj(-15)
        body = decoder.decompress(body, entry['size'] + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('invalid compressed member')
    if (len(body) != entry['size'] or zlib.crc32(body) & 0xffffffff != e['crc32']
            or hashlib.sha256(body).hexdigest() != entry['sha256']):
        raise ValueError('scoring member size/CRC/digest mismatch')
    return body


def evaluation(target, cache):
    target, cache = map(private_path, (target, cache))
    expected = read_lock()['files']
    if target.exists():
        check_files(target, expected)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = resource_lock()
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.evaluation-') as tmp:
        stage = Path(tmp) / 'tree'; stage.mkdir()
        def one(entry):
            destination = stage / entry['path']
            if entry['path'] == 'evaluate.py':
                content = fetch(entry, cache / 'evaluate.py').read_bytes()
            else:
                cached = private_path(cache / 'scoring' / entry['path'])
                if cached.exists():
                    if sha(cached) != entry['sha256'] or cached.stat().st_size != entry['size']:
                        raise ValueError('cached scoring digest mismatch')
                    content = cached.read_bytes()
                else:
                    content = scoring_member(entry, doc['dataset']['size'])
                    atomic_bytes(cached, content)
            atomic_bytes(destination, content)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(one, doc['scoring']))
        check_files(stage, expected)
        stage.rename(target)
    write(cache / 'evaluation-receipt.json', {'lock_sha256': sha(LOCK), 'evaluation_lock_sha256': sha(PROJECT / 'provenance/evaluation-lock.json'),
                                            'files': expected, 'model_calls': 0})
    return target


def extract_dataset(archive, target):
    target = private_path(target)
    if target.exists():
        raise ValueError('dataset destination already exists; refusing conflict')
    with zipfile.ZipFile(archive) as z:
        names = set(); directories = set()
        for item in z.infolist():
            p = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (p.is_absolute() or '..' in p.parts or '\\' in item.filename or not p.parts
                    or p.parts[0] != 'DataSpace-Benchmark' or p.as_posix() in names
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('unsafe, duplicate or linked dataset ZIP member')
            names.add(p.as_posix())
            if item.is_dir(): directories.add(p.as_posix())
        for n in names:
            for parent in PurePosixPath(n).parents:
                if parent.as_posix() in names and parent.as_posix() not in directories:
                    raise ValueError('dataset traverses non-directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(target.parent).free < sum(item.file_size for item in z.infolist()) + 256 * 1024 * 1024:
            raise ValueError('insufficient disk space for complete dataset extraction')
        with tempfile.TemporaryDirectory(dir=target.parent, prefix='.dataset-') as tmp:
            z.extractall(tmp)
            (Path(tmp) / 'DataSpace-Benchmark').rename(target)


def codex(target, cache, temporary):
    doc = resource_lock()['codex']
    archive = fetch(doc, cache / 'codex-0.145.0-linux-x64.tgz')
    subprocess.run([sys.executable, '-B', str(PROJECT / 'scripts/install_codex_runtime.py'),
                    '--runtime-root', str(target), '--cache-dir', str(cache), '--temp-dir', str(temporary),
                    '--from-archive', str(archive), '--sha256', doc['sha256']], check=True)
    package = target / 'codex-0.145.0/platform-package'
    if sha(package / 'vendor/x86_64-unknown-linux-musl/bin/codex') != doc['binary_sha256']:
        raise ValueError('fixed Codex binary mismatch')
    return package


def artifacts(cache):
    doc = resource_lock()
    jobs = [(e, cache / 'debs' / e['url'].rsplit('/', 1)[1]) for e in doc['system']]
    jobs += [(e, cache / 'wheels' / e['filename']) for e in doc['wheels']]
    def one(job):
        e, dest = job
        path = fetch(e, dest)
        if path.suffix == '.deb':
            fields = subprocess.check_output(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], text=True)
            values = [line.split(': ', 1)[1] for line in fields.splitlines()]
            if values != [e['package'], e['version'], e['architecture']]:
                raise ValueError('Debian artifact metadata differs')
        return {'filename': path.name, 'sha256': e['sha256'], 'size': e['size']}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, jobs))
    write(cache / 'artifacts-receipt.json', {'resource_lock_sha256': sha(LOCK), 'verified': results})
    return results


def prepare_storage(storage):
    storage = private_path(storage)
    marker = private_path(storage / 'public-storage.json')
    expected = {'kind': 'eviweave-public-builder', 'resource_lock_sha256': sha(LOCK)}
    if storage.exists():
        if not marker.is_file() or read(marker) != expected:
            raise ValueError('project daemon storage conflict; choose a fresh directory')
    else:
        storage.mkdir(parents=True)
        write(marker, expected)
    # Image filesystems contain legitimate links. Daemon control paths must not.
    for name in ('data', 'exec', 'tmp', 'home', 'client', 'containerd-data', 'containerd-state', 'opt',
                 'verifiers', 'nri.sock', 'nri-plugins', 'nri-config', 'daemon.json', 'containerd.toml',
                 'docker.sock', 'docker.pid', 'containerd.pid', 'containerd.sock', 'daemon.log', 'containerd.log'):
        plain(storage / name)
    return storage


def build_workbench(target, cache, storage):
    """Independent Docker/containerd and an offline image build from reviewed artifacts."""
    target, cache, storage = map(private_path, (target, cache, storage))
    doc = resource_lock()
    if any(storage == p or storage.is_relative_to(p) or p.is_relative_to(storage) for p in (cache, target)):
        raise ValueError('daemon storage, cache and runtime must be disjoint')
    if target.exists():
        verify_workbench(target)
        return target
    prepare_storage(storage)
    artifacts(cache)
    context = private_path(cache / ('build-context-' + sha(PROJECT / 'docker/Dockerfile.public-workbench')[:12]))
    if context.exists():
        raise ValueError('build context exists; use a fresh cache directory')
    context.mkdir()
    shutil.copytree(PROJECT / 'docker/workbench', context / 'workbench')
    for kind in ('debs', 'wheels'):
        (context / kind).mkdir()
    for e in doc['system']:
        shutil.copyfile(cache / 'debs' / e['url'].rsplit('/', 1)[1], context / 'debs' / e['url'].rsplit('/', 1)[1])
    for e in doc['wheels']:
        shutil.copyfile(cache / 'wheels' / e['filename'], context / 'wheels' / e['filename'])
    shutil.copyfile(PROJECT / 'docker/Dockerfile.public-workbench', context / 'Dockerfile')
    (cache / 'build-state').mkdir(exist_ok=True)
    env = clean_env(cache / 'build-state')
    # All daemon/client homes and caches are project-local; never use a system socket.
    subprocess.run([sys.executable, '-B', str(PROJECT / 'scripts/project_docker.py'), 'start', '--storage-root', str(storage)], env=env, check=True)
    socket = 'unix://' + str(storage / 'docker.sock')
    command = ['docker', '--config', str(storage / 'client'), '--host', socket]
    image = 'eviweave-workbench:p5-' + sha(LOCK)[:12]
    subprocess.run([*command, 'pull', '--platform', 'linux/amd64', doc['base']['reference']], env=env, check=True)
    subprocess.run([*command, 'build', '--network=none', '--pull=false', '--tag', image, str(context)], env=env, check=True)
    subprocess.run([sys.executable, '-B', str(PROJECT / 'scripts/prepare_workbench.py'), 'export',
                    '--runtime-root', str(target), '--cache-dir', str(cache / 'export'), '--temp-dir', str(cache / 'tmp'),
                    '--docker-host', socket, '--image', image], env=env, check=True)
    receipt = {'kind': 'independent-public-rebuild', 'resource_lock_sha256': sha(LOCK), 'image': image,
               'docker_host': socket, 'inventory_sha256': sha(target.parent / (target.name + '-files.json')),
               'recipe_sha256': sha(PROJECT / 'docker/Dockerfile.public-workbench'),
               'historical_tree_identity': False}
    write(target.parent / (target.name + '-source.json'), receipt)
    verify_workbench(target)
    return target


def verify_workbench(target):
    target = private_path(target)
    sys.path.insert(0, str(PROJECT / 'scripts'))
    from runtime_support import inventory
    from prepare_workbench import validate_tree
    receipt = read(target.parent / (target.name + '-source.json'))
    inv = target.parent / (target.name + '-files.json')
    if (receipt['kind'] != 'independent-public-rebuild' or receipt['resource_lock_sha256'] != sha(LOCK)
            or receipt['recipe_sha256'] != sha(PROJECT / 'docker/Dockerfile.public-workbench')
            or sha(inv) != receipt['inventory_sha256'] or inventory(target) != read(inv)):
        raise ValueError('independent workbench receipt/inventory differs')
    validate_tree(target)
    for filename in ('python-packages.txt', 'system-packages.txt', 'TOOLS.md', 'capabilities.json'):
        if sha(target / 'rootfs/opt/dataspace' / filename) != sha(PROJECT / 'docker/workbench' / filename):
            raise ValueError('independent workbench frozen package/tools inventory mismatch')
    return receipt


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('evaluation', 'codex', 'artifacts', 'workbench', 'dataset', 'verify-workbench'))
    p.add_argument('--target', type=Path)
    p.add_argument('--cache', type=Path, default=PROJECT / '.local/public-cache')
    p.add_argument('--storage', type=Path, default=PROJECT / '.runtimes/public-build')
    args = p.parse_args(argv)
    cache = private_path(args.cache); cache.mkdir(parents=True, exist_ok=True)
    target = private_path(args.target) if args.target else None
    if target is not None and (target == cache or target.is_relative_to(cache) or cache.is_relative_to(target)):
        p.error('resource destination and download cache must be disjoint')
    if args.action != 'artifacts' and target is None:
        p.error('--target is required')
    try:
        if args.action == 'evaluation': evaluation(target, cache)
        elif args.action == 'codex': codex(target, cache, cache / 'tmp')
        elif args.action == 'artifacts': artifacts(cache)
        elif args.action == 'workbench': build_workbench(target, cache, args.storage)
        elif args.action == 'verify-workbench': verify_workbench(target)
        else:
            archive = fetch_archive(resource_lock()['dataset'], cache / 'DataSpace-Benchmark.zip')
            extract_dataset(archive, target)
            write(cache / 'dataset-receipt.json', {'target': str(target), **resource_lock()['dataset']})
        print(json.dumps({'status': 'PASS', 'action': args.action, 'resource_lock_sha256': sha(LOCK), 'model_calls': 0}))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        write(cache / ('failure-' + args.action + '-' + uid() + '.json'), {'status': 'FAIL', 'error_type': type(exc).__name__, 'detail': str(exc)})
        print('public preparation failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
