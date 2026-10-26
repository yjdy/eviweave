"""Hosted-only maintenance candidate builder; never tags or publishes."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile

import current
from preparation import Check

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.1'
PYTHONS = ('3.10.22', '3.12.3', '3.13.16')
PACKAGES = ('eviweave-0.1.1-py3-none-any.whl', 'eviweave-0.1.1.tar.gz')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def assets(directory, names):
    return [{'name': name, 'size': (directory / name).stat().st_size,
             'sha256': sha(directory / name)} for name in names]


def require_receipt(doc, identity):
    if (doc['status'] != 'PASS' or doc['workflow_commit'] != identity['actual_candidate_commit']
            or doc['actual_candidate_commit'] != identity['actual_candidate_commit']
            or doc['candidate_tree'] != identity['candidate_tree']
            or doc['candidate_fingerprint'] != identity['candidate_fingerprint']
            or doc['candidate_file_count'] != identity['candidate_file_count']
            or doc['run_id'] != os.environ['GITHUB_RUN_ID']
            or doc['run_attempt'] != os.environ['GITHUB_RUN_ATTEMPT']
            or doc['runner_environment'] != 'github-hosted' or doc['model_calls'] != 0
            or not doc['commands'] or any(c['returncode'] != 0 for c in doc['commands'])):
        raise ValueError('candidate receipt identity or outcome mismatch')


def primary():
    if platform.python_version() != '3.12.3':
        raise ValueError('primary candidate requires Python3.12.3')
    current.packages()
    bound = current.identity(ROOT, os.environ)
    report = json.loads((ROOT / 'public-results/preparation-packages-3.12.3.json').read_text())
    require_receipt(report, bound)
    out = ROOT / '.audit/primary'; out.mkdir(parents=True)
    built = ROOT / 'p ci/.audit/current-build'
    for name in PACKAGES:
        shutil.copyfile(built / 'archives' / name, out / name)
    py = built / 'source-env/bin/python'
    versions = subprocess.check_output([str(py), '-c',
        'import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) for n in ("build","setuptools","wheel","pip","packaging")}))'], text=True)
    build = {'os': platform.system(), 'architecture': platform.machine(), 'python': platform.python_version(),
             'tools': json.loads(versions), 'tools_lock_sha256': sha(ROOT / 'locks/ci-tools.txt'),
             'source_date_epoch': int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True)),
             'method': 'sdist then wheel from that sdist; locked tools, no build isolation'}
    write(out / 'primary.json', {'version': VERSION, 'identity': bound, 'build': build,
                                 'assets': assets(out, PACKAGES), 'receipt': report})


def validate_primary(directory, bound):
    doc = json.loads((directory / 'primary.json').read_text())
    require_receipt(doc['receipt'], bound)
    epoch = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True))
    if (doc['identity'] != bound or doc['assets'] != assets(directory, PACKAGES)
            or doc['build']['python'] != '3.12.3' or doc['build']['os'] != 'Linux'
            or doc['build']['architecture'] != 'x86_64' or doc['version'] != VERSION
            or doc['build']['source_date_epoch'] != epoch
            or doc['build']['tools_lock_sha256'] != sha(ROOT / 'locks/ci-tools.txt')):
        raise ValueError('primary package identity mismatch')
    return doc


def originals(directory):
    bound = current.identity(ROOT, os.environ)
    original = validate_primary(directory, bound)
    target = ROOT / 'p ci'
    c = Check(target, 'originals-' + platform.python_version())
    c.report.update(bound)
    c.report['primary_assets'] = original['assets']
    c.env['SOURCE_DATE_EPOCH'] = str(original['build']['source_date_epoch'])
    try:
        build = target / '.audit/original-check'; build.mkdir(parents=True)
        archive_root = build / 'archives'; archive_root.mkdir()
        for name in PACKAGES:
            shutil.copyfile(directory / name, archive_root / name)
        source_dir = build / 'source'; source_dir.mkdir()
        with tarfile.open(archive_root / PACKAGES[1]) as tar:
            for member in tar.getmembers():
                if PurePosixPath(member.name).is_absolute() or '..' in PurePosixPath(member.name).parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe original sdist member')
            tar.extractall(source_dir)
        source = next(source_dir.iterdir())
        c.report['installations'] = {}
        for kind, package in [('wheel', archive_root / PACKAGES[0]), ('sdist', archive_root / PACKAGES[1])]:
            env = build / (kind + '-env')
            c.run(kind + '-venv', [sys.executable, '-m', 'venv', env])
            py = env / 'bin/python'
            c.run(kind + '-locked-tools', [py, '-m', 'pip', 'install', '--only-binary=:all:', '--require-hashes', '-r', 'locks/ci-tools.txt'])
            c.run(kind + '-original-install', [py, '-m', 'pip', 'install', '--no-build-isolation', '--no-deps', package])
            c.run(kind + '-identity', [py, '-B', target / 'tools/check_installation.py', '--output', build / (kind + '-installation.json')])
            output = build / (kind + '-unit-results.json')
            c.run(kind + '-original-unit', [py, '-B', source / 'cloud_checks/current.py', 'unit', '--project', source, '--output', output])
            unit = json.loads(output.read_text())
            if unit['status'] != 'PASS' or any(t['status'] != 'PASS' for t in unit['cases']):
                raise ValueError('original package tests did not pass')
            c.report['installations'][kind] = {k: unit[k] for k in ('status', 'tests', 'discovered', 'not_applicable', 'unsupported_profile_rejection_checks')}
            c.report['installations'][kind]['skips'] = 0
            c.run(kind + '-original-provenance', [py, '-B', target / 'tools/verify_release.py', '--git-scope', 'head', '--archive-root', archive_root, '--output', build / (kind + '-provenance.json')])
            c.report['installations'][kind]['provenance'] = json.loads((build / (kind + '-provenance.json')).read_text())
    except Exception as exc:
        c.errors.append(type(exc).__name__)
        raise
    finally:
        c.finish(c.report['label'])


def freeze(directory, receipts):
    bound = current.identity(ROOT, os.environ)
    primary_doc = validate_primary(directory, bound)
    expected = {'packages-' + p for p in PYTHONS} | {'originals-' + p for p in PYTHONS} | {'en', 'zh'}
    reports = {}
    for path in receipts.rglob('preparation-*.json'):
        if path.parent.name == 'candidate-receipt-primary':
            # The primary build has its own bound receipt in primary.json.
            continue
        doc = json.loads(path.read_text())
        label = doc['label']
        if label not in expected:
            continue
        if label in reports:
            raise ValueError('duplicate candidate receipt')
        require_receipt(doc, bound)
        reports[label] = doc
    if set(reports) != expected:
        raise ValueError('required candidate receipts missing')
    for py in PYTHONS:
        doc = reports['packages-' + py]
        for kind in ('source-unit', 'installed-unit'):
            unit = doc[kind]
            exclusions = unit['not_applicable']
            wanted = current.HOST_ONLY if py.startswith('3.10.') else set()
            if (unit['skips'] or {e['case'] for e in exclusions} != wanted
                    or unit['tests'] + len(exclusions) != unit['discovered']):
                raise ValueError('unit applicability mismatch')
        for installed in reports['originals-' + py]['installations'].values():
            if installed['status'] != 'PASS' or installed['skips'] or installed['tests'] != doc['source-unit']['tests']:
                raise ValueError('primary original installation mismatch')
        if reports['originals-' + py]['primary_assets'] != primary_doc['assets']:
            raise ValueError('primary original bytes mismatch')
    for language in ('en', 'zh'):
        doc = reports[language]
        if (doc['history']['public_score'] != '45/60' or not doc['history']['per_task_match']
                or doc['synthetic'] != {'status': 'PASS', 'interrupted_returncode': 130, 'completed_resume_new_calls': 0}
                or doc['daemon_lifecycle']['cycles'] != 20 or doc['full_dataset_download']
                or doc['environment']['service_auth'] != 'NOT_TESTED'):
            raise ValueError('full acceptance outcome mismatch')
    out = ROOT / 'release-candidate'
    out.mkdir()
    for name in PACKAGES:
        shutil.copyfile(directory / name, out / name)
    verification = {'schema_version': 1, 'version': VERSION, 'status': 'CANDIDATE_VERIFIED_NOT_RELEASED',
        'identity': bound, 'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'workflow_url': 'https://github.com/yjdy/eviweave/actions/runs/' + os.environ['GITHUB_RUN_ID'],
        'build': primary_doc['build'], 'primary_assets': primary_doc['assets'], 'results': reports,
        'boundaries': {'model_calls': 0, 'g1_enabled': False, 'new_benchmark': False, 'full_dataset_download': False,
                       'publication': 'Not requested; stop for review', 'historical_evidence': 'Original v0.1.0 assets unchanged'}}
    write(out / 'cloud-verification.json', verification)
    write(out / 'release-manifest.json', {'schema_version': 1, 'version': VERSION, 'status': 'CANDIDATE_PENDING_REVIEW',
        'identity': bound, 'build': primary_doc['build'], 'run_id': os.environ['GITHUB_RUN_ID'],
        'assets': assets(out, (*PACKAGES, 'cloud-verification.json')),
        'checksum_policy': 'SHA256SUMS covers packages, cloud-verification.json and release-manifest.json; excludes itself',
        'historical_evidence_url': 'https://github.com/yjdy/eviweave/releases/tag/v0.1.0'})
    checksums = assets(out, (*PACKAGES, 'cloud-verification.json', 'release-manifest.json'))
    (out / 'SHA256SUMS').write_text(''.join(e['sha256'] + '  ' + e['name'] + '\n' for e in checksums))
    write(ROOT / 'public-results/candidate-assets.json', {'status': 'PASS', 'identity': bound,
          'assets': assets(out, (*PACKAGES, 'cloud-verification.json', 'release-manifest.json', 'SHA256SUMS'))})


def main():
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('GitHub-hosted runner required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('primary', 'matrix', 'originals', 'freeze'))
    parser.add_argument('--primary', type=Path, default=ROOT / '.audit/primary-download')
    parser.add_argument('--receipts', type=Path, default=ROOT / '.audit/receipts')
    args = parser.parse_args()
    if args.mode == 'primary':
        primary()
    elif args.mode in ('matrix', 'originals'):
        if args.mode == 'matrix':
            current.packages()
        originals(args.primary.resolve())
    else:
        freeze(args.primary.resolve(), args.receipts.resolve())


if __name__ == '__main__':
    main()
