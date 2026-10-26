"""Hosted checks of event source; historical release controls remain separate."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOST_ONLY = {
    'test_public_preparation.PublicPreparationTests.' + name for name in (
        'test_bad_old_profile_and_runtime_missing_are_rejected',
        'test_inventory_rejects_traversal_types_duplicates_and_unknown_fields',
        'test_missing_namespace_fails_without_fallback',
        'test_profile_rejects_extra_fields_and_modified_dependency',
        'test_profile_selection_is_explicit_and_old_default_survives',
        'test_selected_profile_is_returned_only_when_requested',
        'test_valid_independent_profile_cannot_remove_checks_or_add_hooks',
    )
}
FULL_GUARD = "${{ github.event_name == 'workflow_dispatch' && inputs.full }}"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(project, event):
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    if actual != event['GITHUB_SHA']:
        raise ValueError('event commit and checked source differ')
    names = subprocess.check_output(['git', 'ls-files'], cwd=project, text=True).splitlines()
    files = {}
    for name in names:
        raw = (project / name).read_bytes()
        if raw != subprocess.check_output(['git', 'show', actual + ':' + name], cwd=project):
            raise ValueError('candidate worktree differs from Git source')
        files[name] = hashlib.sha256(raw).hexdigest()
    return {'actual_candidate_commit': actual,
            'candidate_tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=project, text=True).strip(),
            'candidate_file_count': len(files),
            'candidate_fingerprint': hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'event_name': event.get('GITHUB_EVENT_NAME'), 'head_ref': event.get('GITHUB_HEAD_REF', ''),
            'base_ref': event.get('GITHUB_BASE_REF', ''), 'event_ref': event.get('GITHUB_REF', '')}


def full_requested(event, value):
    return event == 'workflow_dispatch' and value is True


def validate_workflow(text):
    import yaml
    doc = yaml.load(text, Loader=yaml.BaseLoader)
    events = doc['on']
    if events['push']['branches'] != ['main'] or events['pull_request']['branches'] != ['main']:
        raise ValueError('current CI must cover main pushes and main pull requests')
    full = events['workflow_dispatch']['inputs']['full']
    if full['type'] != 'boolean' or full['default'] != 'false':
        raise ValueError('full validation must be explicit and default off')
    if doc['jobs']['preparation']['if'] != FULL_GUARD or doc['jobs']['preparation']['needs'] != 'unit-gate':
        raise ValueError('full validation requires manual request and passing package matrix')
    if doc['jobs']['preparation']['strategy']['max-parallel'] != '1':
        raise ValueError('runtime checks must be serial')
    matrix = doc['jobs']['unit-gate']['strategy']['matrix']['python']
    if matrix != ['3.10.22', '3.12.3', '3.13.16'] or doc['permissions'] != {'contents': 'read'}:
        raise ValueError('current package matrix or permissions mismatch')
    for job in doc['jobs'].values():
        if job['runs-on'] != 'ubuntu-22.04':
            raise ValueError('GitHub-hosted runner required')
        for step in job['steps']:
            if step.get('uses', '').startswith('actions/checkout@'):
                if 'ref' in step.get('with', {}) or step['with'].get('persist-credentials') != 'false':
                    raise ValueError('checkout must use event source without persisted credentials')
    return {'status': 'PASS', 'package_python': matrix, 'full_default': False}


def readme_contract(english, chinese):
    blocks = [re.findall(r'```(?:sh|bash)\n(.*?)```', s, re.S) for s in (english, chinese)]
    if not blocks[0] or blocks[0] != blocks[1]:
        raise ValueError('bilingual commands differ')
    numeric = lambda s: set(re.findall(r'\d+(?:\.\d+)*(?:/\d+)?%?', s))
    if numeric(english) != numeric(chinese):
        raise ValueError('bilingual numbers differ')
    return blocks[0]


def documents(project):
    import yaml
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    texts = [(project / name).read_text() for name in ('README.md', 'README.zh-CN.md')]
    blocks = readme_contract(*texts)
    for block in blocks:
        subprocess.run(['bash', '-n'], input=block, text=True, check=True)
    links = 0
    for text in texts:
        for link in re.findall(r'\]\(([^)]+)\)', text):
            parsed = urlsplit(link)
            if parsed.scheme or not parsed.path:
                continue
            if not (project / unquote(parsed.path)).is_file():
                raise ValueError('relative documentation link missing')
            links += 1
    if '<a id="统一实验流程"></a>' not in texts[1]:
        raise ValueError('retained Chinese workflow anchor missing')
    meta = tomllib.loads((project / 'pyproject.toml').read_text())['project']
    citation = yaml.safe_load((project / 'CITATION.cff').read_text())
    version = re.search(r'__version__ = "([^"]+)"', (project / 'src/dataspace_baselines/__init__.py').read_text())[1]
    if not (meta['version'] == citation['version'] == version and meta['license'] == citation['license'] == 'MIT'):
        raise ValueError('package citation version or license mismatch')
    if meta['authors'] != [{'name': '袁珺 / Jun Yuan'}] or citation['authors'] != [{'family-names': 'Yuan', 'given-names': 'Jun', 'alias': '袁珺'}]:
        raise ValueError('package citation authors mismatch')
    workflow = validate_workflow((project / '.github/workflows/preparation.yml').read_text())
    return {'status': 'PASS', 'identical_shell_blocks': len(blocks), 'relative_links': links,
            'version': version, 'license': 'MIT', 'workflow': workflow}


def select_cases(cases, version):
    if version != (3, 10):
        return list(cases), []
    ids = {case.id() for case in cases}
    if not HOST_ONLY.issubset(ids):
        raise ValueError('declared host-only cases missing from discovery')
    return ([case for case in cases if case.id() not in HOST_ONLY],
            [{'case': name, 'reason': 'Host profiles require CPython3.12/3.13; mandatory on both runtime-supported jobs'}
             for name in sorted(HOST_ONLY)])


def run_units(project, output):
    sys.path.insert(0, str(project / 'tools'))
    worker = load('current_unit_worker', project / 'tools/unit_worker.py')
    def flatten(suite):
        for case in suite:
            if isinstance(case, unittest.TestSuite):
                yield from flatten(case)
            else:
                yield case
    cases = list(flatten(unittest.defaultTestLoader.discover(str(project / 'tests'), pattern='test_*.py')))
    selected, excluded = select_cases(cases, sys.version_info[:2])
    rejection_checks = 0
    if excluded:
        import host_profile, environment_lock
        for check in (host_profile.dependency_profile, environment_lock.profile):
            try:
                check()
            except ValueError:
                rejection_checks += 1
            else:
                raise ValueError('unsupported host profile must be rejected')
    result = unittest.TextTestRunner(verbosity=2, resultclass=worker.RecordingResult).run(unittest.TestSuite(selected))
    passed = result.wasSuccessful() and not result.skipped
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun,
                                  'discovered': len(cases), 'not_applicable': excluded,
                                  'unsupported_profile_rejection_checks': rejection_checks, 'cases': result.cases},
                                 ensure_ascii=False, indent=2) + '\n')
    return 0 if passed else 1


def packages():
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('GitHub-hosted runner required')
    bound = identity(ROOT, os.environ)
    target = ROOT / 'p ci'
    subprocess.run(['git', 'clone', '--no-hardlinks', '--no-local', str(ROOT), str(target)], check=True)
    cloud = load('current_preparation_cloud', ROOT / 'cloud_checks/preparation.py')
    check = cloud.Check(target, 'packages-' + sys.version.split()[0])
    check.env['SOURCE_DATE_EPOCH'] = subprocess.check_output(
        ['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=target, text=True).strip()
    check.report.update(bound)
    check.report['full_validation'] = 'REQUESTED' if full_requested(os.environ.get('GITHUB_EVENT_NAME'), os.environ.get('EVIWEAVE_CI_FULL') == 'true') else 'NOT_REQUESTED'
    try:
        bound_copy = identity(target, os.environ)
        assert bound_copy == bound
        build = target / '.audit/current-build';build.mkdir(parents=True)
        def venv(name):
            env = build / name
            check.run(name + '-venv', [sys.executable, '-m', 'venv', env])
            py = env / 'bin/python'
            check.run(name + '-tools', [py, '-m', 'pip', 'install', '--only-binary=:all:', '--require-hashes', '-r', 'locks/ci-tools.txt'])
            return py
        source_py = venv('source-env')
        check.run('ordinary-install', [source_py, '-m', 'pip', 'install', '--no-build-isolation', '--no-deps', target])
        check.run('ordinary-install-check', [source_py, '-B', 'tools/check_installation.py', '--output', build / 'ordinary-install.json'])
        check.run('documents', [source_py, '-B', 'cloud_checks/current.py', 'documents', '--project', target, '--output', build / 'documents.json'])
        check.report['documents'] = json.loads((build / 'documents.json').read_text())
        def units(label, py, project):
            output = project / '.audit/current-unit/unit-results.json'
            check.run(label, [py, '-B', project / 'cloud_checks/current.py', 'unit', '--project', project, '--output', output])
            doc = json.loads(output.read_text())
            assert doc['status'] == 'PASS' and all(case['status'] == 'PASS' for case in doc['cases'])
            check.report[label] = {k: doc[k] for k in ('status', 'tests', 'discovered', 'not_applicable', 'unsupported_profile_rejection_checks')}
            check.report[label]['skips'] = 0
        units('source-unit', source_py, target)
        archives = build / 'archives'
        check.run('sdist-build', [source_py, '-m', 'build', '--sdist', '--no-isolation', '--outdir', archives, target])
        unpacked = build / 'unpacked';unpacked.mkdir()
        with tarfile.open(next(archives.glob('*.tar.gz'))) as tar:
            for member in tar.getmembers():
                if PurePosixPath(member.name).is_absolute() or '..' in PurePosixPath(member.name).parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe sdist member')
            tar.extractall(unpacked)
        source = next(unpacked.iterdir())
        check.run('wheel-from-sdist', [source_py, '-m', 'build', '--wheel', '--no-isolation', '--outdir', archives, source])
        wheel_py = venv('wheel-env')
        check.run('wheel-install', [wheel_py, '-m', 'pip', 'install', '--no-deps', next(archives.glob('*.whl'))])
        check.run('wheel-install-check', [wheel_py, '-B', target / 'tools/check_installation.py', '--output', build / 'wheel-install.json'])
        units('installed-unit', wheel_py, source)
        check.run('release-provenance', [wheel_py, '-B', 'tools/verify_release.py', '--git-scope', 'head', '--archive-root', archives, '--output', build / 'release-verification.json'])
        verification = json.loads((build / 'release-verification.json').read_text())
        check.report['packaging'] = {k: verification[k] for k in ('status', 'checks', 'verbatim_file_count', 'archives')}
        check.report['builds_are_acceptance_only'] = True
    except Exception as exc:
        check.errors.append(type(exc).__name__)
        raise
    finally:
        check.finish(check.report['label'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('packages', 'unit', 'documents'))
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'packages':
        packages()
    elif args.mode == 'unit':
        if not args.output:
            parser.error('--output required')
        raise SystemExit(run_units(args.project.resolve(), args.output))
    else:
        if not args.output:
            parser.error('--output required')
        args.output.write_text(json.dumps(documents(args.project.resolve()), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
