"""Credential-free preparation orchestration; no inference or service configuration.

The shell entries select an interpreter before entering this standard-library
module. Static preflight never creates files, downloads, or launches services.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def local(value, areas=('.local', '.audit', '.runtimes', 'data')):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if '..' in path.parts or any(c in str(path) for c in ('\n', '\r', '\0')):
        raise ValueError('unsafe preparation path')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('linked preparation paths are not accepted')
    path = path.resolve()
    if not any(path != ROOT / area and path.is_relative_to(ROOT / area) for area in areas):
        raise ValueError('path must be a child of project private storage')
    return path


def disjoint(paths):
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('preparation inputs and outputs overlap')


def available(path):
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    for action in ('environment', 'dataspace', 'history'):
        q = sub.add_parser(action, description={
            'environment': 'Linux x86_64, CPython 3.12/3.13, Docker/containerd/Bubblewrap and sudo required. Builds a separate runtime; never downloads task data or calls a model.',
            'dataspace': 'Default: only 121 scoring files. Explicit full mode requires space for the 2.88GB ZIP, retained range parts and 15.01GB extracted data.',
            'history': 'CPython 3.12.3 and prepared scoring files required. No model workbench or API key. Network disabled by default; --download permits only fixed public evidence assets.',
        }[action], epilog='Existing targets need explicit --reuse where available. Output and storage must stay inside this project. Static preflight does not certify privileges or sandbox capability.')
        q.add_argument('--python', default=sys.executable, help='interpreter selected by the shell entry')
        q.add_argument('--output', help='fresh project .audit directory; defaults to a unique run')
        q.add_argument('--check-only', action='store_true', help='read-only static preflight; no network, writes, or service processes')
        if action != 'history':
            q.add_argument('--reuse', action='store_true', help='explicitly verify existing targets; never repair conflicting resources')
            q.add_argument('--cache', default='.local/preparation-cache/' + action)
        if action == 'environment':
            q.add_argument('--venv', default='.venv-experiment')
            q.add_argument('--profile-name', default='public-host')
            q.add_argument('--runtime-root', default='.runtimes/prepared')
        else:
            q.add_argument('--evaluation-root', default='.local/evaluation-v6-public60')
        if action == 'dataspace':
            q.add_argument('--mode', choices=('evaluation', 'full'), default='evaluation', help='full explicitly downloads 2.88GB and extracts 15.01GB')
            q.add_argument('--dataset', default='data/DataSpace-Benchmark')
        elif action == 'history':
            q.add_argument('--archive', default='.local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip')
            q.add_argument('--download', action='store_true', help='allow fixed public v0.1.0 archive/checksum downloads; scoring materials must already exist')
    return p


def preflight(args):
    """Read-only path/platform checks, deliberately excluding capability probes."""
    paths = {'output': local(args.output or '.audit/preparation/' + args.action + '-' + stamp(), ('.audit',))}
    if paths['output'].exists():
        raise ValueError('output must be fresh')
    details = {'status': 'STATIC_ONLY', 'action': args.action, 'python': platform.python_version(),
               'model_calls': 0, 'service_auth': 'NOT_TESTED', 'capabilities': 'NOT_TESTED'}
    if args.action == 'environment':
        if (platform.python_implementation() != 'CPython' or sys.version_info[:2] not in ((3, 12), (3, 13))
                or sys.platform != 'linux' or platform.machine() != 'x86_64'):
            raise ValueError('environment requires CPython 3.12/3.13 on Linux x86_64')
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', args.profile_name):
            raise ValueError('profile name requires 1..64 lowercase letters/digits/hyphens')
        paths.update(runtime=local(args.runtime_root, ('.runtimes',)), cache=local(args.cache),
                     profile=local('.local/profiles/' + args.profile_name + '.json'),
                     config=local('.local/config-v6/offline-' + args.profile_name + '.yaml'))
        # Venv's interpreter is normally a symlink; validate its enclosing directory.
        v = Path(args.venv)
        if not v.is_absolute():
            v = ROOT / v
        if not v.name.startswith('.venv') or '..' in v.parts or any(c in str(v) for c in ('\n', '\r', '\0')):
            raise ValueError('venv must be a project-local .venv* directory')
        if any(x.is_symlink() for x in (v, *v.parents)) or not v.resolve().is_relative_to(ROOT) or v.resolve() == ROOT:
            raise ValueError('unsafe venv directory')
        paths['venv'] = v.resolve()
        paths['config_checksum'] = local(str(paths['config']) + '.sha256')
        missing = [tool for tool in ('docker', 'dockerd', 'containerd', 'bwrap', 'sudo') if not shutil.which(tool)]
        if missing:
            raise ValueError('missing host tools: ' + ', '.join(missing))
        details['permissions'] = 'NOT_TESTED; actual preparation checks sudo and namespaces'
        for key in ('profile', 'config', 'venv', 'runtime'):
            if paths[key].exists() and not args.reuse:
                raise ValueError('existing ' + key + ' requires --reuse')
        if paths['config'].exists() and (not paths['config_checksum'].is_file()
                or paths['config_checksum'].read_text() != digest(paths['config']) + '\n'):
            raise ValueError('offline configuration is not an unchanged script-created snapshot')
        if paths['profile'].exists():
            from host_profile import load_profile
            profile = load_profile(paths['profile'])
            runtime = profile['runtime']
            if (runtime['codex_root'] != str(paths['runtime'] / 'codex/codex-0.145.0/platform-package')
                    or runtime['workbench_root'] != str(paths['runtime'] / 'workbench')):
                raise ValueError('saved profile uses another runtime root')
        if available(paths['runtime']) < 8 * 1024**3:
            raise ValueError('environment preparation requires at least 8GiB free; actual usage may be higher')
        details['minimum_free_bytes'] = 8 * 1024**3
    else:
        paths['evaluation'] = local(args.evaluation_root)
        if args.action == 'dataspace':
            paths['cache'] = local(args.cache)
            if paths['evaluation'].exists() and not args.reuse:
                raise ValueError('existing scoring directory requires --reuse')
            if args.mode == 'full':
                paths['dataset'] = local(args.dataset, ('data',))
                if paths['dataset'].exists() and not args.reuse:
                    raise ValueError('existing dataset requires --reuse')
                # Includes archive, retained range parts, extracted data and margin.
                minimum = 2 * read(ROOT / 'provenance/public-resources-lock.json')['dataset']['size'] + 15010873064 + 512 * 1024**2
                if not paths['dataset'].exists() and min(available(paths['dataset']), available(paths['cache'])) < minimum:
                    raise ValueError('insufficient free space for archive, range parts and 15.01GB extraction')
                details.update(dataset_zip_bytes=2878651222, dataset_extracted_bytes=15010873064, minimum_free_bytes=minimum)
            details['mode'] = args.mode
        else:
            if platform.python_implementation() != 'CPython' or platform.python_version() != '3.12.3':
                raise ValueError('historical reproduction requires CPython 3.12.3; select it with --python')
            paths['archive'] = local(args.archive)
            paths['sidecar'] = local(str(paths['archive']) + '.sha256')
            if paths['archive'].name != 'eviweave-v0.1.0-v6-evidence.zip':
                raise ValueError('retain the original historical archive filename')
            if not paths['evaluation'].is_dir():
                raise ValueError('prepare scoring materials with prepare_dataspace.sh first')
            if not args.download and not all(paths[k].is_file() for k in ('archive', 'sidecar')):
                raise ValueError('archive and external .sha256 required; provide both or explicitly use --download')
    # Runtime internals are kept below runtime; the other directories stay disjoint.
    disjoint([v for k, v in paths.items() if k != 'sidecar'])
    details['paths'] = {k: str(v.relative_to(ROOT)) for k, v in paths.items()}
    return paths, details


class Run:
    def __init__(self, paths, details):
        self.paths, self.doc = paths, dict(details, status='RUNNING', steps=[])
        self.out = paths['output']
        self.out.mkdir(parents=True, mode=0o700)
        self.env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH') if k in os.environ}
        for key, name in (('HOME', 'home'), ('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache')):
            target = self.out / name
            target.mkdir()
            self.env[key] = str(target)
        self.env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PIP_CONFIG_FILE='/dev/null',
                        PIP_DISABLE_PIP_VERSION_CHECK='1', EVIWEAVE_AUDIT_ROOT=str(self.out))
        self.doc.update(script_sha256=digest(Path(__file__)), actual_python=sys.version,
                        resource_lock_sha256=digest(ROOT / 'provenance/public-resources-lock.json'))

    def save(self):
        (self.out / 'preparation-report.json').write_text(json.dumps(self.doc, indent=2) + '\n')

    def step(self, name, script, *args, python=None):
        command = [str(python or sys.executable), '-B', str(ROOT / script), *map(str, args)]
        row = {'name': name, 'status': 'RUNNING'}
        self.doc['steps'].append(row)
        self.save()
        with (self.out / (name + '.log')).open('wb') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=log, stderr=log, start_new_session=True)
            try:
                code = process.wait(timeout=1800)
            except (KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                row.update(status='FAIL', returncode=130 if isinstance(exc, KeyboardInterrupt) else 124)
                self.save()
                raise
        row.update(status='PASS' if code == 0 else 'FAIL', returncode=code)
        self.save()
        if code:
            raise ValueError('step failed: ' + name + '; inspect its private log')


def resources(run, action, target):
    run.step('resource-' + action, 'tools/public_resources.py', action, '--target', target, '--cache', run.paths['cache'])


def verify_dataset(archive, target):
    """Reuse requires the retained, authenticated original ZIP; no guessed identity."""
    from public_resources import resource_lock
    from standalone import check_files
    entry = resource_lock()['dataset']
    if not archive.is_file() or archive.stat().st_size != entry['size'] or digest(archive) != entry['sha256']:
        raise ValueError('dataset reuse requires the retained, locked original ZIP in this cache')
    with zipfile.ZipFile(archive) as z:
        expected = {}
        for member in z.infolist():
            name = Path(member.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in member.filename or name.parts[0] != 'DataSpace-Benchmark':
                raise ValueError('unsafe dataset archive member')
            if member.is_dir():
                continue
            relative = name.relative_to('DataSpace-Benchmark').as_posix()
            if relative in expected:
                raise ValueError('duplicate dataset member')
            h = hashlib.sha256()
            with z.open(member) as stream:
                while block := stream.read(1024 * 1024):
                    h.update(block)
            expected[relative] = h.hexdigest()
        check_files(target, expected)


def dataspace(run, args):
    p = run.paths
    resources(run, 'evaluation', p['evaluation'])
    run.step('verify-evaluation', 'tools/standalone.py', 'verify-evaluation', '--evaluation-root', p['evaluation'])
    if args.mode == 'full':
        if p['dataset'].exists():
            verify_dataset(p['cache'] / 'DataSpace-Benchmark.zip', p['dataset'])
        else:
            resources(run, 'dataset', p['dataset'])
        run.doc['dataset_verified'] = True
    run.doc['evaluation_files'] = 121


def evidence_assets():
    names = {'eviweave-v0.1.0-v6-evidence.zip', 'eviweave-v0.1.0-v6-evidence.zip.sha256'}
    entries = [e for e in read(ROOT / 'provenance/release-assets.json')['assets'] if e['name'] in names]
    if len(entries) != 2 or {e['name'] for e in entries} != names:
        raise ValueError('frozen historical asset mapping is incomplete')
    return entries


def history(run, args):
    from public_resources import fetch
    from evidence_verify import read_archive
    p = run.paths
    entries = evidence_assets()
    for entry in entries:
        destination = p['archive'] if entry['name'].endswith('.zip') else p['sidecar']
        if args.download:
            # Public, unauthenticated URL; fetch authenticates the original P4 bytes.
            fetch(dict(entry, url='https://github.com/yjdy/eviweave/releases/download/v0.1.0/' + entry['name']), destination)
        if not destination.is_file() or destination.stat().st_size != entry['size'] or digest(destination) != entry['sha256']:
            raise ValueError('historical asset missing or differs from the frozen original')
    run.step('verify-evidence', 'tools/package_evidence.py', 'verify', '--archive', p['archive'], '--output-root', run.out / 'evidence-check')
    verified = read(run.out / 'evidence-check' / 'report.json')
    if verified.get('external_sha256') != 'PASS':
        raise ValueError('external evidence checksum is required')
    # The existing bounded, whitelist-based reader rejects links and unsafe members.
    snapshot = p['archive'].read_bytes()
    archive_sha = hashlib.sha256(snapshot).hexdigest()
    if archive_sha != next(e['sha256'] for e in entries if e['name'].endswith('.zip')):
        raise ValueError('evidence archive changed during verification')
    payload = read_archive(io.BytesIO(snapshot))
    extracted = run.out / 'extracted'
    for name, content in payload.items():
        target = extracted / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    run.step('historical-score', 'tools/standalone.py', 'score', '--evaluation-root', p['evaluation'],
             '--prediction-root', extracted / 'predictions', '--output-root', run.out / 'score')
    actual = read(run.out / 'score' / 'score.json')
    expected = json.loads(payload['evaluation/public60.json'])
    if actual != expected:
        raise ValueError('offline scoring differs from the frozen per-task result')
    run.doc.update(archive_sha256=archive_sha, external_sha256='PASS', predictions=410,
                   attempt_summaries=414, selected_trace_attempts=6, missing_usage_attempts=3,
                   public_score='45/60', failed_tasks=15, unscored_tasks=350, per_task_match=True,
                   historical_first_pass='44/60', official_historical='333/410;81.22%;not recomputed')


def daemon_live(storage):
    live = []
    for name, config in (('docker', 'daemon.json'), ('containerd', 'containerd.toml')):
        pidfile = storage / (name + '.pid')
        if pidfile.exists():
            pid = int(pidfile.read_text())
            if pid <= 1:
                raise ValueError('invalid daemon PID')
            proc = Path('/proc') / str(pid) / 'cmdline'
            if not proc.exists() or str(storage / config).encode() not in proc.read_bytes():
                raise ValueError('stale or foreign project daemon PID; refusing ownership')
            live.append(name)
    if len(live) == 1:
        raise ValueError('partially running project daemon; inspect before preparation')
    return len(live) == 2


def environment(run, args):
    from public_resources import prepare_storage
    p = run.paths
    codex, workbench, storage = (p['runtime'] / n for n in ('codex', 'workbench', 'build'))
    started = False
    prior_live = daemon_live(storage)
    try:
        # Exercise privilege prerequisites only during real preparation.
        with (run.out / 'sudo-check.log').open('wb') as log:
            subprocess.run(['sudo', '-n', 'true'], env=run.env, stdout=log, stderr=log, check=True, timeout=20)
        if codex.exists():
            if not args.reuse:
                raise ValueError('existing Codex requires --reuse')
        # The existing installer compares the complete target to the authenticated
        # archive, also allowing recovery before a profile was first collected.
        resources(run, 'codex', codex)
        prepare_storage(storage)
        started = not prior_live
        if workbench.exists():
            run.step('verify-workbench', 'tools/public_resources.py', 'verify-workbench', '--target', workbench, '--cache', p['cache'])
        else:
            run.step('resource-workbench', 'tools/public_resources.py', 'workbench', '--target', workbench, '--cache', p['cache'], '--storage', storage)
        run.step('daemon-start', 'scripts/project_docker.py', 'start', '--storage-root', storage)
        if not p['profile'].exists():
            run.step('collect-profile', 'tools/host_profile.py', 'collect', '--name', args.profile_name,
                     '--codex-root', codex / 'codex-0.145.0/platform-package', '--workbench-root', workbench, '--output', p['profile'])
        run.step('check-profile', 'tools/host_profile.py', 'check', '--profile', p['profile'], '--output', run.out / 'profile-check')
        run.step('install', 'tools/install_environment.py', '--python', p['venv'] / 'bin/python', '--environment-profile', p['profile'], '--cache-dir', p['cache'] / 'packages')
        installed = p['venv'] / 'bin/python'
        if not p['config'].exists():
            run.step('offline-config', 'tools/host_profile.py', 'config', '--profile', p['profile'], '--output', p['config'], python=installed)
            p['config_checksum'].write_text(digest(p['config']) + '\n')
        run.step('offline-doctor', 'tools/host_profile.py', 'doctor', '--profile', p['profile'], '--config', p['config'], '--output', run.out / 'doctor', python=installed)
        run.doc.update(profile_sha256=digest(p['profile']), offline_config_sha256=digest(p['config']),
                       selected=False, online_collection='UNSUPPORTED_G1', daemon_restart_storage=str(storage.relative_to(ROOT)))
    finally:
        pending = sys.exc_info()[0] is not None
        if started and any((storage / (n + '.pid')).exists() for n in ('docker', 'containerd')):
            try:
                run.step('daemon-stop', 'scripts/project_docker.py', 'stop', '--storage-root', storage)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                run.doc['cleanup_error'] = str(exc)
                if not pending:
                    raise
        run.doc['preexisting_daemon_preserved'] = prior_live


def main(argv=None):
    args = parser().parse_args(argv)
    run = None
    try:
        paths, details = preflight(args)
        print(json.dumps(details), flush=True)
        if args.check_only:
            return 0
        run = Run(paths, details)
        guard = local('.audit/preparation.lock')
        with guard.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('another preparation is running; lock occupied') from None
            # Resource helpers also use standard-library network/temp functions.
            os.environ.clear()
            os.environ.update(run.env)
            {'environment': environment, 'dataspace': dataspace, 'history': history}[args.action](run, args)
        run.doc['status'] = 'PASS'
        run.save()
        print(json.dumps({'status': 'PASS', 'report': str(run.out / 'preparation-report.json'), 'model_calls': 0}))
        return 0
    except KeyboardInterrupt:
        code, status, detail = 130, 'INTERRUPTED', 'operation interrupted'
    except (ValueError, OSError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        code, status, detail = 2, 'FAIL', str(exc)
    if run:
        run.doc.update(status=status, error=detail)
        run.save()
    print(json.dumps({'status': status, 'error': detail, 'model_calls': 0}), file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
