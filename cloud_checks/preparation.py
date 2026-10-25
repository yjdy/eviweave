"""Hosted-only acceptance of the current preparation candidate, not fixed v0.1.0."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cloud_checks'))
from verify import bootstrap_python


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Check:
    def __init__(self, root, label):
        self.root = root
        self.out = root / '.audit/cloud-preparation'
        self.out.mkdir(parents=True, exist_ok=True)
        self.errors = []
        self.env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'HISTORY_PYTHON') if k in os.environ}
        for key, name in (('HOME', 'home'), ('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache'), ('PIP_CACHE_DIR', 'pip-cache')):
            p = self.out / name
            p.mkdir(exist_ok=True)
            self.env[key] = str(p)
        self.env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        self.report = {'status': 'RUNNING', 'label': label, 'workflow_commit': os.environ['GITHUB_SHA'],
                       'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                       'runner_environment': os.environ.get('RUNNER_ENVIRONMENT'), 'python': sys.version,
                       'model_calls': 0, 'commands': [], 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'disk_free_before': shutil.disk_usage(root).free}

    def run(self, name, command, soft=False, **kwargs):
        started = time.monotonic()
        env = dict(self.env, **kwargs.get('extra', {}))
        with (self.out / (name + '.log')).open('wb') as log:
            result = subprocess.run(list(map(str, command)), cwd=self.root, env=env, stdout=log, stderr=log,
                                    timeout=kwargs.get('timeout', 2400))
        self.report['commands'].append({'name': name, 'returncode': result.returncode,
                                        'seconds': round(time.monotonic() - started, 2)})
        print(json.dumps(self.report['commands'][-1]), flush=True)
        if result.returncode:
            self.errors.append(name)
            if not soft:
                raise ValueError('cloud step failed: ' + name)
        return result.returncode

    def finish(self, label):
        self.report.update(status='FAIL' if self.errors else 'PASS', failed_steps=self.errors,
                           disk_free_after=shutil.disk_usage(self.root).free)
        target = ROOT / 'public-results'
        target.mkdir(exist_ok=True)
        (target / ('preparation-' + label + '.json')).write_text(json.dumps(self.report, indent=2) + '\n')


def document_commands(root, language):
    readme = root / ('README.md' if language == 'en' else 'README.zh-CN.md')
    blocks = re.findall(r'```sh\n(.*?)\n```', readme.read_text(), re.S)
    allowed = ('./scripts/prepare_environment.sh', './scripts/prepare_dataspace.sh', './scripts/verify_history.sh')
    result = []
    for block in blocks:
        # Only preparation scripts; never run real experiment examples from README.
        if block.startswith(allowed) and '--mode full' not in block and '--reuse' not in block:
            result.append(block)
    if len(result) != 5:
        raise ValueError('expected five exact README preparation commands')
    return result


def acceptance(language, history_python):
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    assert actual == os.environ['GITHUB_SHA']
    # A short space-containing path also keeps project-local Unix sockets in range.
    target = ROOT / ('c ' + language)
    subprocess.run(['git', 'clone', '--no-hardlinks', '--no-local', str(ROOT), str(target)], check=True)
    subprocess.run(['git', 'checkout', '--detach', actual], cwd=target, check=True)
    assert not any((target / n).exists() for n in ('.local', '.runtimes', '.venv-experiment', 'data'))
    c = Check(target, language)
    c.env['HISTORY_PYTHON'] = history_python
    c.report.update(actual_candidate_commit=actual, candidate_tree=subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=target, text=True).strip(),
                    clean_copy_without_developer_state=True, full_dataset_download=False)
    try:
        names = subprocess.check_output(['git', 'ls-files'], cwd=target, text=True).splitlines()
        identities = {n: sha(target / n) for n in names}
        for name in names:
            assert identities[name] == hashlib.sha256(subprocess.check_output(['git', 'show', actual + ':' + name], cwd=ROOT)).hexdigest()
        c.report['candidate_fingerprint'] = hashlib.sha256(json.dumps(identities, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        for i, block in enumerate(document_commands(target, language)):
            script = c.out / ('readme-' + str(i) + '.sh')
            script.write_text('set -eu\n' + block + '\n')
            c.run('readme-' + str(i), ['bash', script])
        reports = [json.loads(p.read_text()) for p in (target / '.audit/preparation').glob('*/preparation-report.json')]
        assert len(reports) == 3 and all(r['status'] == 'PASS' for r in reports)
        history = next(r for r in reports if r['action'] == 'history')
        assert history['public_score'] == '45/60' and history['per_task_match']
        c.report['history'] = {k: history[k] for k in ('public_score', 'per_task_match', 'archive_sha256', 'missing_usage_attempts')}
        c.run('environment-reuse', ['bash', 'scripts/prepare_environment.sh', '--python', sys.executable, '--reuse'])
        c.run('scoring-reuse', ['bash', 'scripts/prepare_dataspace.sh', '--python', sys.executable, '--reuse'])
        py = target / '.venv-experiment/bin/python'
        profile = target / '.local/profiles/public-host.json'
        storage = target / '.runtimes/prepared/build'
        try:
            c.run('start-for-synthetic', [sys.executable, '-B', 'scripts/project_docker.py', 'start', '--storage-root', storage])
            c.run('synthetic', [py, '-B', 'tools/unified_smoke.py', '--environment-profile', profile,
                                '--evaluation-root', '.local/evaluation-v6-public60', '--output-root', '.audit/cloud synthetic'])
            synthetic = json.loads((target / '.audit/cloud synthetic/acceptance.json').read_text())
            assert synthetic['status'] == 'PASS' and synthetic['interrupted_returncode'] == 130 and synthetic['completed_resume_new_calls'] == 0
            c.report['synthetic'] = {k: synthetic[k] for k in ('status', 'interrupted_returncode', 'completed_resume_new_calls')}
        finally:
            c.run('stop-after-synthetic', [sys.executable, '-B', 'scripts/project_docker.py', 'stop', '--storage-root', storage])
        c.run('source-unit', [py, '-B', 'tools/run_regression.py', '--suite', 'unit', '--output-root', '.audit/cloud-source-unit'])
        units = json.loads((target / '.audit/cloud-source-unit/unit-results.json').read_text())
        assert units['status'] == 'PASS' and all(x['status'] == 'PASS' for x in units['cases'])
        c.report['source_unit'] = {'status': units['status'], 'tests': units['tests'], 'skips': 0}
        c.run('installation-sdist-wheel-tests', [py, '-B', 'tools/ci_check.py', '--output-root', '.audit/cloud-build'])
        built = json.loads((target / '.audit/cloud-build/ci-report.json').read_text())
        assert built['status'] == 'PASS'
        installed_units = list((target / '.audit/cloud-build/unpacked').glob('*/.audit/unit/unit-results.json'))
        assert len(installed_units) == 1
        installed = json.loads(installed_units[0].read_text())
        assert installed['status'] == 'PASS' and all(x['status'] == 'PASS' for x in installed['cases'])
        c.report['installed_unit'] = {'status': installed['status'], 'tests': installed['tests'], 'skips': 0}
        c.run('release-provenance', [target / '.audit/cloud-build/wheel-env/bin/python', '-B', 'tools/verify_release.py',
                                      '--git-scope', 'head', '--archive-root', '.audit/cloud-build/archives',
                                      '--output', '.audit/cloud-build/release-verification.json'])
        release = json.loads((target / '.audit/cloud-build/release-verification.json').read_text())
        assert release['status'] == 'PASS'
        c.report['packaging'] = {k: release[k] for k in ('status', 'checks', 'installed_module_count', 'verbatim_file_count')}
        c.report['builds_are_acceptance_only'] = True
    except Exception as exc:
        c.errors.append(type(exc).__name__)
        raise
    finally:
        c.finish(language)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--bootstrap', action='store_true')
    p.add_argument('--language', choices=('en', 'zh'))
    p.add_argument('--history-python')
    args = p.parse_args()
    if args.bootstrap:
        c = Check(ROOT, 'bootstrap')
        try:
            bootstrap_python(c)
        except Exception as exc:
            c.errors.append(type(exc).__name__)
            raise
        finally:
            c.finish('bootstrap-' + platform.python_version())
    else:
        acceptance(args.language, args.history_python)


if __name__ == '__main__':
    main()
