#!/usr/bin/env python3
"""Import, verify, build or export Data Workbench using project-local storage."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from runtime_support import PROJECT, archive_tree, copy_verified, differences, extract_checked, inventory, local_path, sha256, write_json

HISTORICAL_IMAGE = 'sha256:6d982582df67775ad78193d54a0e54e2809373bacf73551b9baaad0ef3f893b5'
TOOLS_SHA256 = '71faab0935ae2f9740c2a797816bae4d31c94065349519f5b80a9f7a20141c17'


def docker_command(args):
    """Mutation requires an explicitly selected, local daemon and local data root."""
    if not args.docker_host or not args.docker_host.startswith('unix://'):
        raise ValueError('--docker-host unix://PROJECT/... is required for build/export')
    socket = local_path(args.docker_host.removeprefix('unix://'))
    command = ['docker', '--config', str(local_path(args.cache_dir)/'docker-client'), '--host', 'unix://'+str(socket)]
    root = subprocess.check_output([*command, 'info', '--format', '{{.DockerRootDir}}'], text=True, timeout=30).strip()
    local_path(root)
    companion = subprocess.check_output([*command, 'info', '--format', '{{.Containerd.Address}}'], text=True, timeout=30).strip()
    local_path(companion)
    return command


def validate_tree(runtime):
    rootfs = runtime/'rootfs'
    manifest = json.loads((runtime/'workbench-manifest.json').read_text())
    if manifest.get('runtime_version') != '1.0': raise ValueError('runtime version mismatch')
    actual = sha256(rootfs/'opt/dataspace/TOOLS.md')
    if actual != TOOLS_SHA256 or manifest.get('tools_sha256') != actual:
        raise ValueError('TOOLS.md digest mismatch')
    for name in ('capabilities.json', 'python-packages.txt', 'system-packages.txt'):
        if not (rootfs/'opt/dataspace'/name).is_file(): raise ValueError('missing '+name)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['import', 'verify', 'build', 'export'])
    parser.add_argument('--runtime-root', type=Path, default=PROJECT/'.runtimes/data-workbench-1.0')
    parser.add_argument('--cache-dir', type=Path, default=PROJECT/'.audit/p2/cache')
    parser.add_argument('--temp-dir', type=Path, default=PROJECT/'.audit/p2/tmp')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--from-dir', type=Path)
    group.add_argument('--from-archive', type=Path)
    parser.add_argument('--sha256', help='required SHA-256 for archive input')
    parser.add_argument('--manifest', type=Path, help='expected inventory of the complete runtime directory')
    parser.add_argument('--archive-output', type=Path, help='optional portable archive, only written once')
    parser.add_argument('--docker-host', help='explicit project-local daemon unix socket for build/export')
    parser.add_argument('--image', default='eviweave-workbench:p2-rebuilt')
    args = parser.parse_args(argv)
    target = local_path(args.runtime_root); temp = local_path(args.temp_dir); cache = local_path(args.cache_dir)
    temp.mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)
    if args.image == 'dataspace-workbench:1.0' and args.action == 'build':
        raise ValueError('refusing to replace historical image tag')
    if args.action == 'build':
        command = docker_command(args)
        subprocess.run([*command, 'build', '--network', 'host', '--tag', args.image,
                        '--file', str(PROJECT/'docker/Dockerfile.workbench'), str(PROJECT/'docker')], check=True)
        image = json.loads(subprocess.check_output([*command, 'image', 'inspect', args.image], text=True))[0]
        write_json(cache/'build-image.json', {'image': args.image, 'id': image['Id'], 'platform': image['Os']+'/'+image['Architecture'],
                   'recipe_sha256': sha256(PROJECT/'docker/Dockerfile.workbench')})
        print(json.dumps({'status': 'PASS', 'action': 'build', 'image_id': image['Id']})); return
    if args.action == 'verify':
        manifest = validate_tree(target)
        expected = json.loads((args.manifest or target.parent/(target.name+'-files.json')).read_text())
        diff = differences(expected, inventory(target))
        if diff: raise ValueError(f'runtime content mismatch: {diff[:3]}')
        print(json.dumps({'status': 'PASS', 'action': 'verify', 'entries': len(expected['entries']), 'image_id': manifest['image_id']})); return
    with tempfile.TemporaryDirectory(dir=temp, prefix='workbench-') as temporary:
        stage = Path(temporary)
        if args.action == 'export':
            command = docker_command(args)
            image_id = subprocess.check_output([*command, 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True, timeout=30).strip()
            container = subprocess.check_output([*command, 'create', args.image], text=True, timeout=30).strip()
            try:
                archive = stage/'rootfs.tar'
                subprocess.run([*command, 'export', '--output', str(archive), container], check=True, timeout=120)
            finally:
                subprocess.run([*command, 'rm', container], check=True, timeout=30)
            source = stage/'runtime'; source.mkdir()
            extract_checked(archive, source/'rootfs')
            from datetime import datetime, timezone
            write_json(source/'workbench-manifest.json', {'runtime_version': '1.0', 'image': args.image, 'image_id': image_id,
                       'tools_sha256': sha256(source/'rootfs/opt/dataspace/TOOLS.md'),
                       'exported_at': datetime.now(timezone.utc).isoformat(), 'origin': 'independent-export'})
            expected = inventory(source)
        else:
            if not args.manifest: raise ValueError('--manifest is required for import')
            expected = json.loads(args.manifest.read_text())
            if args.from_archive:
                if not args.sha256 or sha256(args.from_archive) != args.sha256:
                    raise ValueError('archive SHA-256 mismatch or missing')
                source = stage/'runtime'; extract_checked(args.from_archive, source)
            elif args.from_dir:
                source = args.from_dir.resolve()
            else: raise ValueError('--from-dir or --from-archive is required')
            if differences(expected, inventory(source)): raise ValueError('source inventory mismatch')
        manifest = validate_tree(source)
        if args.action == 'import' and manifest['image_id'] != HISTORICAL_IMAGE:
            raise ValueError('historical image identity mismatch')
        action = copy_verified(source, target, temp, expected)
        record = target.parent/(target.name+'-files.json')
        if record.exists() and json.loads(record.read_text()) != expected: raise ValueError('stored inventory differs')
        write_json(record, expected)
        archive_sha = None
        if args.archive_output:
            if args.archive_output.exists():
                raise ValueError('archive output exists; verify or choose a new name')
            archive_sha = archive_tree(target, args.archive_output)
        print(json.dumps({'status': 'PASS', 'action': action, 'origin': args.action, 'image_id': manifest['image_id'],
                          'entries': len(expected['entries']), 'inventory_sha256': sha256(record), 'archive_sha256': archive_sha}))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        raise SystemExit(f'Workbench preparation failed: {type(exc).__name__}: {exc}')
