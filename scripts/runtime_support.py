"""Standard-library runtime inventories and project-confined preparation helpers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor

PROJECT = Path(__file__).resolve().parents[1]


def local_path(value):
    path = Path(value).expanduser().resolve()
    if path == PROJECT or not path.is_relative_to(PROJECT):
        raise ValueError(f'output must be a child of project: {path}')
    return path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload):
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def inventory(root):
    """Never descend symlinks, including absolute container links."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'inventory root must be a real directory: {root}')
    paths = [root]
    def walk_error(error):
        raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort(); files.sort()
        paths.extend(Path(parent) / name for name in directories + files)
    paths.sort(key=lambda p: p.relative_to(root).as_posix())
    hardlinks = {}
    def entry(path):
        info = path.lstat()
        item = {'path': path.relative_to(root).as_posix(), 'mode': stat.S_IMODE(info.st_mode),
                'uid': info.st_uid, 'gid': info.st_gid}
        attrs = {name: os.getxattr(path, name, follow_symlinks=False).hex()
                 for name in sorted(os.listxattr(path, follow_symlinks=False))}
        item['xattrs'] = attrs
        if stat.S_ISLNK(info.st_mode):
            item.update(type='symlink', target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            item['type'] = 'directory'
        elif stat.S_ISREG(info.st_mode):
            item.update(type='file', size=info.st_size, sha256=sha256(path))
            if info.st_nlink > 1:
                item['_inode'] = (info.st_dev, info.st_ino)
        else:
            raise ValueError(f'unsupported special file: {path}')
        return item
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(entry, paths))
    for item in entries:
        inode = item.pop('_inode', None)
        if inode:
            item['hardlink_to'] = hardlinks.setdefault(inode, item['path'])
    return {'schema': 1, 'entries': entries}


def differences(expected, actual):
    a = {e['path']: e for e in expected['entries']}
    b = {e['path']: e for e in actual['entries']}
    return [{'path': name, 'expected': a.get(name), 'actual': b.get(name)}
            for name in sorted(a.keys() | b.keys()) if a.get(name) != b.get(name)]


def copy_verified(source, target, temp_dir, expected):
    source = Path(source).resolve(); target = local_path(target)
    if source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('source and destination must be disjoint')
    if target.exists():
        diff = differences(expected, inventory(target))
        if diff:
            raise ValueError(f'existing runtime differs ({len(diff)} entries); refusing replacement')
        return 'reused'
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = local_path(temp_dir); temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_dir, prefix='import-') as temporary:
        stage = Path(temporary) / 'tree'
        # GNU cp preserves internal hardlinks and xattrs without linking to source.
        subprocess.run(['cp', '-a', '--', str(source), str(stage)], check=True)
        diff = differences(expected, inventory(stage))
        if diff:
            raise ValueError(f'copied runtime differs: {diff[:3]}')
        for item in expected['entries']:
            if item['type'] == 'file':
                left = (source / item['path']).stat(); right = (stage / item['path']).stat()
                if (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino):
                    raise ValueError('copy shares inode with source')
        stage.rename(target)
    return 'imported'


def extract_checked(archive, destination):
    """Validate the whole tar graph before extracting any member.

    Container absolute symlinks are retained, but no member may traverse one.
    Hardlinks must point to an ordinary member inside the archive.
    """
    destination = local_path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('archive destination must be empty')
    with tarfile.open(archive) as handle:
        members = handle.getmembers(); names = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or (not name.parts and not member.isdir()):
                raise ValueError(f'unsafe archive path: {member.name}')
            if name.as_posix() in names:
                raise ValueError(f'duplicate archive path: {member.name}')
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f'unsupported archive member: {member.name}')
            names[name.as_posix()] = member
        for member in members:
            path = PurePosixPath(member.name)
            for parent in path.parents:
                ancestor = names.get(parent.as_posix())
                if ancestor and not ancestor.isdir():
                    raise ValueError(f'archive traverses non-directory: {member.name}')
            if member.islnk():
                link = PurePosixPath(member.linkname)
                linked = names.get(link.as_posix())
                if link.is_absolute() or '..' in link.parts or not linked or not linked.isfile():
                    raise ValueError(f'unsafe archive hardlink: {member.name}')
        destination.mkdir(parents=True, exist_ok=True)
        # Above validation prevents write-through links; trust is not inferred
        # from tarfile's data filter, which rejects legitimate rootfs symlinks.
        handle.extractall(destination, members=members, filter='fully_trusted')
        for member in members:
            path = destination / member.name
            for key, value in member.pax_headers.items():
                if key.startswith('SCHILY.xattr.'):
                    os.setxattr(path, key.removeprefix('SCHILY.xattr.'), value.encode('utf-8', errors='surrogateescape'), follow_symlinks=False)


def archive_tree(root, destination):
    root = Path(root); destination = local_path(destination)
    if destination.exists():
        raise ValueError(f'archive already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.partial')
    subprocess.run(['tar', '--xattrs', '--acls', '-cf', str(temporary), '-C', str(root), '.'], check=True)
    temporary.rename(destination)
    return sha256(destination)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Snapshot a runtime without following symlinks; new snapshots are not historical authentication.')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root)
    write_json(args.output, result)
    print(json.dumps({'entries': len(result['entries']), 'inventory_sha256': sha256(args.output)}))
