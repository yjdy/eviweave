#!/usr/bin/env python3
"""Collect a candidate, check differences/capabilities, then select it explicitly."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile

from experiment_store import fingerprint, read, sha, write
from standalone import PROJECT, clean_env, plain


def dependency_profile():
    minor = sys.version_info[:2]
    if (minor not in ((3, 12), (3, 13)) or platform.python_implementation() != 'CPython'
            or sys.platform != 'linux' or platform.machine() != 'x86_64'):
        raise ValueError('independent profiles support only CPython 3.12/3.13, Linux x86_64')
    return f'cpython3{minor[1]}-linux-x86_64'


def measured_python():
    return {'version': platform.python_version(), 'full_version': sys.version,
            'implementation': platform.python_implementation(), 'machine': platform.machine(),
            'soabi': sysconfig.get_config_var('SOABI'), 'sha256': sha(Path(sys.executable).resolve())}


def measured_host():
    return {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
            'os_release': sha('/etc/os-release')}


def skeleton(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name):
        raise ValueError('profile name must use 1..64 lowercase letters/digits/hyphens')
    dep = dependency_profile()
    locked = read(PROJECT / 'locks' / (dep + '.json'))
    return {'format_version': 2, 'kind': 'independent-host', 'profile_id': name,
            'scope': 'local-offline-capability; model-service-not-tested', 'online_collection': 'UNSUPPORTED_G1',
            'dependency_profile': dep, 'dependency_lock_sha256': sha(PROJECT / 'locks' / (dep + '.json')),
            'python': measured_python(), 'host': measured_host(), 'packages': copy.deepcopy(locked['packages']),
            'tools': {}, 'runtime': {}}


def mapping(value, fields, where):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError('unsupported fields/types at profile ' + where)


def digest(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('invalid profile digest')


def validate_inventory(doc):
    mapping(doc, ('schema', 'entries'), 'inventory')
    if type(doc['schema']) is not int or doc['schema'] != 1 or type(doc['entries']) is not list or not doc['entries']:
        raise ValueError('invalid profile inventory')
    seen = set()
    for e in doc['entries']:
        if type(e) is not dict or e.get('type') not in ('file', 'directory', 'symlink'):
            raise ValueError('invalid inventory entry')
        extra = {'file': {'size', 'sha256'}, 'directory': set(), 'symlink': {'target'}}[e['type']]
        allowed = {'path', 'mode', 'uid', 'gid', 'xattrs', 'type'} | extra
        if e['type'] == 'file' and 'hardlink_to' in e:
            allowed.add('hardlink_to')
        mapping(e, allowed, 'inventory entry')
        name = e['path']
        if (type(name) is not str or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or '\\' in name or name in seen or any(c in name for c in ('\0', '\n', '\r'))):
            raise ValueError('unsafe inventory member')
        seen.add(name)
        for key in ('mode', 'uid', 'gid', *(['size'] if e['type'] == 'file' else [])):
            if type(e[key]) is not int or e[key] < 0 or (key == 'mode' and e[key] > 0o7777):
                raise ValueError('invalid inventory numeric field')
        if type(e['xattrs']) is not dict or any(type(k) is not str or type(v) is not str or not re.fullmatch(r'(?:[0-9a-f]{2})*', v)
                                              for k, v in e['xattrs'].items()):
            raise ValueError('invalid inventory xattrs')
        if e['type'] == 'file': digest(e['sha256'])
        if e['type'] == 'symlink' and (type(e['target']) is not str or '\0' in e['target']):
            raise ValueError('invalid inventory link')
        if 'hardlink_to' in e and (type(e['hardlink_to']) is not str or e['hardlink_to'] not in seen):
            raise ValueError('invalid inventory hardlink')
    if '.' not in seen:
        raise ValueError('missing inventory root')


def validate(doc):
    fields = ('format_version', 'kind', 'profile_id', 'scope', 'online_collection', 'dependency_profile',
              'dependency_lock_sha256', 'python', 'host', 'packages', 'tools', 'runtime')
    mapping(doc, fields, 'root')
    if (type(doc['format_version']) is not int or doc['format_version'] != 2 or doc['kind'] != 'independent-host'
            or doc['scope'] != 'local-offline-capability; model-service-not-tested'
            or doc['online_collection'] != 'UNSUPPORTED_G1' or type(doc['profile_id']) is not str
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', doc['profile_id'])):
        raise ValueError('unsupported independent profile identity/scope')
    dep = doc['dependency_profile']
    if dep not in ('cpython312-linux-x86_64', 'cpython313-linux-x86_64'):
        raise ValueError('unsupported dependency profile')
    path = PROJECT / 'locks' / (dep + '.json')
    if doc['dependency_lock_sha256'] != sha(path) or doc['packages'] != read(path)['packages']:
        raise ValueError('dependency source/artifact/content lock differs')
    for key, names in (('python', ('version', 'full_version', 'implementation', 'machine', 'soabi', 'sha256')),
                       ('host', ('system', 'release', 'machine', 'os_release'))):
        mapping(doc[key], names, key)
        if any(type(v) is not str or not v or len(v) > 512 or '\0' in v for v in doc[key].values()):
            raise ValueError('invalid host/Python field type')
    if (doc['python']['implementation'] != 'CPython' or doc['python']['machine'] != 'x86_64'
            or not re.fullmatch(r'3\.(12|13)\.\d+', doc['python']['version'])
            or doc['python']['version'].split('.')[1] != ('12' if '312-' in dep else '13')
            or doc['host']['machine'] != 'x86_64' or doc['host']['system'] != 'Linux'):
        raise ValueError('unsupported host/Python ABI')
    digest(doc['python']['sha256']); digest(doc['host']['os_release'])
    mapping(doc['tools'], ('docker', 'bwrap'), 'tools')
    for e in doc['tools'].values():
        mapping(e, ('sha256', 'version'), 'tool')
        digest(e['sha256'])
        if type(e['version']) is not str or not e['version'] or len(e['version']) > 256 or '\0' in e['version']:
            raise ValueError('invalid tool version')
    runtime = doc['runtime']
    mapping(runtime, ('kind', 'resource_lock_sha256', 'codex_root', 'workbench_root', 'docker_host', 'image',
                      'codex_inventory', 'workbench_inventory'), 'runtime')
    from public_resources import LOCK, private_path
    if runtime['kind'] != 'independent-public-rebuild' or runtime['resource_lock_sha256'] != sha(LOCK):
        raise ValueError('independent runtime source lock differs')
    for name in ('codex_root', 'workbench_root'):
        if type(runtime[name]) is not str or not Path(runtime[name]).is_absolute():
            raise ValueError('absolute project-private runtime path required')
        private_path(runtime[name])
    if type(runtime['docker_host']) is not str or not runtime['docker_host'].startswith('unix://'):
        raise ValueError('project-local Docker socket required')
    private_path(runtime['docker_host'][7:])
    if type(runtime['image']) is not str or not re.fullmatch(r'eviweave-workbench:p5-[0-9a-f]{12}', runtime['image']):
        raise ValueError('independent workbench image identity required')
    for key in ('codex_inventory', 'workbench_inventory'):
        validate_inventory(runtime[key])
    return doc


def load_profile(path):
    path = plain(path)
    if not path.is_relative_to(PROJECT) or not path.is_file():
        raise ValueError('profile must be project-local')
    doc = read(path)
    if type(doc) is not dict:
        raise ValueError('profile root must be a mapping')
    if doc.get('format_version') == 1:
        # Explicit historical selection accepts only one of the original byte-exact locks.
        known = [PROJECT / 'locks' / f'cpython{suffix}-linux-x86_64.json' for suffix in ('312', '313')]
        if not any(doc == read(path) for path in known):
            raise ValueError('historical profile must match the retained exact lock')
        return doc
    return validate(doc)


def runtime_check(doc, config=None):
    validate(doc)
    from public_resources import LOCK, private_path, verify_workbench
    sys.path.insert(0, str(PROJECT / 'scripts'))
    from runtime_support import inventory
    r = doc['runtime']; codex = plain(r['codex_root']); workbench = plain(r['workbench_root'])
    receipt = verify_workbench(workbench)
    if receipt['docker_host'] != r['docker_host'] or receipt['image'] != r['image']:
        raise ValueError('runtime source receipt association differs')
    for key, root in (('codex_inventory', codex), ('workbench_inventory', workbench)):
        if inventory(root) != r[key]:
            raise ValueError('independent runtime full contents differ')
    fixed = read(LOCK)['codex']
    if sha(codex / 'vendor/x86_64-unknown-linux-musl/bin/codex') != fixed['binary_sha256']:
        raise ValueError('Codex binary differs from fixed 0.145.0')
    if config is not None:
        options = config.harnesses['codex'].options
        configured = plain(str(options['runtime_path']).replace('{version}', '0.145.0'))
        if (str(options.get('version')) != '0.145.0' or configured != codex / 'vendor/x86_64-unknown-linux-musl'
                or plain(config.workbench.rootfs_path) != workbench / 'rootfs' or config.workbench.image != r['image']):
            raise ValueError('configuration paths/image differ from selected independent runtime')
    os.environ['DOCKER_HOST'] = r['docker_host']
    return r


def capability(doc, output):
    """Actual fixed local program under namespaces; no client or network connections."""
    from public_resources import private_path
    output = private_path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output, prefix='sandbox-') as tmp:
        root = Path(tmp)
        for n in ('input', 'output', 'home'): (root / n).mkdir()
        (root / 'input/sentinel').write_text('read-only')
        script = ("from pathlib import Path; import os,errno; "
                  "assert os.readlink('/proc/self/ns/pid')!=" + repr(os.readlink('/proc/self/ns/pid')) + "; "
                  "assert os.readlink('/proc/self/ns/net')!=" + repr(os.readlink('/proc/self/ns/net')) + "; "
                  "assert os.readlink('/proc/self/ns/mnt')!=" + repr(os.readlink('/proc/self/ns/mnt')) + "; "
                  "assert os.readlink('/proc/self/ns/user')!=" + repr(os.readlink('/proc/self/ns/user')) + "; "
                  "assert Path('/mnt/workspace/sentinel').read_text()=='read-only'; "
                  "assert not Path(" + repr(str(PROJECT)) + ").exists(); "
                  "Path('/mnt/output/probe').write_text('ok'); Path('/mnt/home/probe').write_text('ok')\n"
                  "try: Path('/mnt/workspace/sentinel').write_text('bad')\n"
                  "except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES)\n"
                  "else: raise AssertionError('input writable')\n"
                  "print('namespace-mount-probe-pass')")
        command = [shutil.which('bwrap'), '--die-with-parent', '--new-session', '--unshare-user', '--unshare-pid',
                   '--unshare-ipc', '--unshare-uts', '--unshare-net', '--ro-bind', doc['runtime']['workbench_root'] + '/rootfs', '/',
                   '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run', '--tmpfs', '/mnt',
                   '--ro-bind', str(root / 'input'), '/mnt/workspace', '--bind', str(root / 'output'), '/mnt/output',
                   '--bind', str(root / 'home'), '/mnt/home', '--chdir', '/mnt/output', '--', '/usr/local/bin/python3', '-B', '-c', script]
        result = subprocess.run(command, env=clean_env(root), capture_output=True, text=True, timeout=30)
        report = {'status': 'PASS' if result.returncode == 0 else 'FAIL', 'profile_sha256': fingerprint(doc),
                  'command': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
                  'model_calls': 0, 'production_isolation_changed': False}
        write(output / 'capability.json', report)
        if result.returncode or result.stdout.strip() != 'namespace-mount-probe-pass':
            raise ValueError('required sandbox capability unavailable; no host fallback')
    return report


def collect(name, codex, workbench, target):
    from public_resources import LOCK, private_path, verify_workbench
    sys.path.insert(0, str(PROJECT / 'scripts'))
    from runtime_support import inventory
    doc = skeleton(name)
    target = private_path(target)
    if not target.is_relative_to(PROJECT / '.local/profiles') or target.exists():
        raise ValueError('new candidate must be a fresh file in .local/profiles/')
    receipt = verify_workbench(workbench)
    codex = private_path(codex); workbench = private_path(workbench)
    for tool in ('bwrap', 'docker'):
        binary = shutil.which(tool)
        if not binary: raise ValueError('required host tool missing: ' + tool)
        doc['tools'][tool] = {'sha256': sha(binary), 'version': subprocess.check_output([binary, '--version'], text=True, timeout=20).strip()}
    doc['runtime'] = {'kind': 'independent-public-rebuild', 'resource_lock_sha256': sha(LOCK),
                      'codex_root': str(codex), 'workbench_root': str(workbench),
                      'image': receipt['image'], 'docker_host': receipt['docker_host'],
                      'codex_inventory': inventory(codex), 'workbench_inventory': inventory(workbench)}
    validate(doc); runtime_check(doc)
    write(target, doc)
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('collect')
    c.add_argument('--name', required=True)
    c.add_argument('--codex-root', type=Path, required=True)
    c.add_argument('--workbench-root', type=Path, required=True)
    c.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('config', help='write a fresh private config for this independent runtime, retaining the placeholder endpoint')
    c.add_argument('--profile', type=Path, required=True)
    c.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('doctor', help='explicit offline doctor, no key required and no service authentication')
    c.add_argument('--profile', type=Path, required=True)
    c.add_argument('--config', type=Path, required=True)
    c.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('check')
    c.add_argument('--profile', type=Path, required=True)
    c.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.action == 'collect':
            doc = collect(args.name, args.codex_root, args.workbench_root, args.output)
            print(json.dumps({'status': 'CANDIDATE', 'profile_sha256': fingerprint(doc), 'selected': False}))
        elif args.action in ('check', 'config', 'doctor'):
            doc = load_profile(args.profile)
            if doc['format_version'] != 2: raise ValueError('check expects an independent candidate')
            if args.action == 'config':
                from public_resources import private_path
                target = private_path(args.output)
                if not target.is_relative_to(PROJECT / '.local') or target.exists():
                    raise ValueError('config requires a fresh project-private .local path')
                import yaml
                raw = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
                raw['experiment']['workbench'].update(rootfs_path=doc['runtime']['workbench_root'] + '/rootfs', image=doc['runtime']['image'])
                raw['harnesses']['codex']['runtime_path'] = doc['runtime']['codex_root'] + '/vendor/x86_64-unknown-linux-musl'
                write(target, raw)  # JSON is valid YAML; no secrets or service endpoint introduced.
                print(json.dumps({'status': 'PASS', 'config': str(target), 'model_calls': 0}))
                return 0
            from environment_lock import check_python, check_host
            check_python(doc); check_host(doc); runtime_check(doc)
            capability(doc, args.output)
            if args.action == 'doctor':
                from dataspace_baselines.config import load_harness_experiment_config
                from dataspace_baselines.doctor import run_doctor
                from environment_lock import verify
                config = load_harness_experiment_config(plain(args.config))
                verify(config, doc=doc)
                checks = run_doctor(config, offline=True, temp_dir=plain(args.output))
                report = {'status': 'PASS' if all(c.ok for c in checks) else 'FAIL', 'model_calls': 0,
                          'service_auth': 'NOT_TESTED', 'checks': [dict(name=c.name, ok=c.ok, detail=c.detail, skipped=c.skipped) for c in checks]}
                write(args.output / 'doctor.json', report)
                print(json.dumps(report))
                return 0 if report['status'] == 'PASS' else 2
            old = read(PROJECT / 'locks' / (doc['dependency_profile'] + '.json'))
            changes = [k for k in ('python', 'host', 'tools', 'runtime') if old[k] != doc[k]]
            write(args.output / 'differences.json', {'historical_profile': old['profile_id'], 'different_sections': changes,
                  'profile_sha256': fingerprint(doc), 'installed_packages': 'NOT_CHECKED; installer verifies authenticated wheel contents',
                  'select_with': '--environment-profile ' + str(args.profile), 'online_collection': 'UNSUPPORTED_G1'})
            print(json.dumps({'status': 'PASS', 'different_sections': changes, 'profile_sha256': fingerprint(doc), 'selected': False}))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('host profile: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
