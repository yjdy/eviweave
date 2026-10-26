"""Separate hosted publication control; the release remains the frozen commit."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'cloud_checks/publication_v011.json').read_text())
API = 'https://api.github.com/repos/' + CFG['repository'] + '/'
TAG = 'v0.1.1'
OUT = ROOT / 'public-results'
OUT.mkdir(exist_ok=True)
REQUESTS = []


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            result.remove_header('Authorization')
        return result


def request(endpoint, auth=False, method='GET', data=None, binary=False):
    url = endpoint if endpoint.startswith('https://') else API + endpoint
    headers = {'User-Agent': 'EviWeave-v0.1.1-publication',
               'Accept': ('application/octet-stream' if binary and '/releases/assets/' in url
                          else 'application/vnd.github+json')}
    if auth:
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    if data is not None:
        headers['Content-Type'] = 'application/octet-stream' if isinstance(data, bytes) else 'application/json'
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
    # No cookie processor, netrc lookup or ambient authorization in this client.
    if not auth and ('Authorization' in headers or 'Cookie' in headers):
        raise ValueError('anonymous request carries authentication')
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    with urllib.request.build_opener(Redirect()).open(req, timeout=120) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('unexpected large publication response')
    REQUESTS.append({'url': url.split('?')[0], 'method': method, 'authenticated': auth, 'cookies': False})
    return raw if binary else json.loads(raw)


def release_or_none(auth):
    try:
        return request('releases/tags/' + TAG, auth)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def tag_identity(auth):
    obj = request('git/ref/tags/' + TAG, auth)['object']
    tag_object = obj['sha']
    if obj['type'] == 'tag':
        obj = request('git/tags/' + obj['sha'], auth)['object']
    if obj['type'] != 'commit' or obj['sha'] != CFG['commit']:
        raise ValueError('release tag target mismatch')
    commit = request('git/commits/' + obj['sha'], auth)
    if commit['tree']['sha'] != CFG['tree']:
        raise ValueError('release tag tree mismatch')
    return {'tag': TAG, 'tag_object': tag_object, 'commit': obj['sha'], 'tree': commit['tree']['sha']}


def verify_files(directory):
    names = {p.name for p in directory.iterdir() if p.is_file()}
    if names != {e['name'] for e in CFG['assets']}:
        raise ValueError('release asset set mismatch')
    for asset in CFG['assets']:
        raw = (directory / asset['name']).read_bytes()
        if len(raw) != asset['size'] or digest(raw) != asset['sha256']:
            raise ValueError('release asset bytes mismatch')
    sums = {line.split('  ', 1)[1]: line.split('  ', 1)[0]
            for line in (directory / 'SHA256SUMS').read_text().splitlines()}
    if sums != {e['name']: e['sha256'] for e in CFG['assets'] if e['name'] != 'SHA256SUMS'}:
        raise ValueError('release checksum coverage mismatch')
    manifest = json.loads((directory / 'release-manifest.json').read_text())
    verification = json.loads((directory / 'cloud-verification.json').read_text())
    for doc in (manifest, verification):
        if (doc['identity']['actual_candidate_commit'] != CFG['commit']
                or doc['identity']['candidate_tree'] != CFG['tree']
                or doc['identity']['candidate_fingerprint'] != CFG['fingerprint']):
            raise ValueError('frozen release binding mismatch')
    if manifest['assets'] != [e for e in CFG['assets'] if e['name'] not in ('release-manifest.json', 'SHA256SUMS')]:
        raise ValueError('manifest asset identity mismatch')


def main_gate():
    run = request('actions/runs/' + str(CFG['main_run']), True)
    if (run['conclusion'] != 'success' or run['head_sha'] != CFG['commit']
            or run['head_branch'] != 'main' or run['event'] != 'push'
            or run['path'] != '.github/workflows/preparation.yml'):
        raise ValueError('main automatic gate identity mismatch')
    reports = []
    for artifact in request('actions/runs/' + str(run['id']) + '/artifacts', True)['artifacts']:
        if not artifact['name'].startswith('current-packages-'):
            continue
        raw = request('actions/artifacts/' + str(artifact['id']) + '/zip', True, binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                doc = json.loads(archive.read(name))
                if not doc.get('label', '').startswith('packages-'):
                    continue
                if (doc['status'] != 'PASS' or doc['actual_candidate_commit'] != CFG['commit']
                        or doc['workflow_commit'] != CFG['commit'] or doc['candidate_fingerprint'] != CFG['fingerprint']
                        or doc['event_ref'] != 'refs/heads/main' or doc['full_validation'] != 'NOT_REQUESTED'
                        or any(c['returncode'] != 0 for c in doc['commands'])):
                    raise ValueError('main package receipt mismatch')
                reports.append({k: doc[k] for k in ('label', 'actual_candidate_commit', 'candidate_tree',
                                                  'source-unit', 'installed-unit', 'full_validation')})
    if {d['label'] for d in reports} != {'packages-' + p for p in ('3.10.22', '3.12.3', '3.13.16')}:
        raise ValueError('main package receipt set incomplete')
    return reports


def body(published_at=None):
    raw = (ROOT / 'RELEASE_NOTES.md').read_bytes()
    if digest(raw) != CFG['files']['RELEASE_NOTES.md']:
        raise ValueError('release notes differ from reviewed bytes')
    text = raw.decode()
    start = text.index('## English')
    text = text[start:].replace('This maintenance candidate', 'This maintenance release')
    text = text.replace('本维护候选', '本维护版本').replace('Candidate assets:', 'Release assets:').replace('候选资产为', '发布资产为')
    for name in ('EXPERIMENTS.md', 'EXPERIMENTS.zh-CN.md'):
        text = text.replace('](' + name + ')', '](https://github.com/yjdy/eviweave/blob/' + TAG + '/' + name + ')')
    status = ('Published / 已发布: `' + published_at + '` (GitHub published_at).') if published_at else 'Draft / 草稿；publication date will be assigned by GitHub / 发布日期由 GitHub 返回。'
    marker = '<!-- eviweave-v0.1.1:' + CFG['commit'] + ':' + CFG['fingerprint'] + ' -->'
    return ('# EviWeave v0.1.1\n\n' + status + '\n\n' + marker + '\n\n'
            + 'Release commit / 发布提交: `' + CFG['commit'] + '`; tree: `' + CFG['tree'] + '`.\n\n'
            + 'Verification / 验收: [candidate](https://github.com/yjdy/eviweave/actions/runs/' + str(CFG['candidate_run'])
            + '), [main automatic checks](https://github.com/yjdy/eviweave/actions/runs/' + str(CFG['main_run']) + ').\n\n' + text)


def publish(directory, report):
    report['main_gate'] = main_gate()
    if request('git/ref/heads/main', True)['object']['sha'] != CFG['commit']:
        raise ValueError('main moved after candidate acceptance')
    report['tag'] = tag_identity(True)
    run = request('actions/runs/' + str(CFG['candidate_run']), True)
    artifact = request('actions/artifacts/' + str(CFG['artifact_id']), True)
    if (run['conclusion'] != 'success' or run['head_sha'] != CFG['commit'] or artifact['expired']
            or artifact['digest'] != CFG['artifact_digest']):
        raise ValueError('original candidate artifact unavailable or different')
    verify_files(directory)
    release = release_or_none(True)
    if release is None:
        release = request('releases', True, 'POST', {'tag_name': TAG, 'target_commitish': CFG['commit'],
            'name': 'EviWeave v0.1.1', 'body': body(), 'draft': True, 'prerelease': False})
    marker = '<!-- eviweave-v0.1.1:' + CFG['commit'] + ':' + CFG['fingerprint'] + ' -->'
    if marker not in release['body'] or release['tag_name'] != TAG:
        raise ValueError('existing release identity unknown')
    report['release_id'] = release['id']
    existing = {e['name']: e for e in release['assets']}
    if set(existing) - {e['name'] for e in CFG['assets']}:
        raise ValueError('unexpected existing release assets')
    for asset in CFG['assets']:
        if asset['name'] not in existing:
            if not release['draft']:
                raise ValueError('published release missing expected asset')
            url = release['upload_url'].split('{')[0] + '?name=' + urllib.parse.quote(asset['name'])
            request(url, True, 'POST', (directory / asset['name']).read_bytes())
    release = request('releases/' + str(release['id']), True)
    remote_dir = ROOT / '.audit/remote-draft'; remote_dir.mkdir(parents=True)
    if {e['name'] for e in release['assets']} != {e['name'] for e in CFG['assets']}:
        raise ValueError('remote release asset set mismatch')
    for asset in release['assets']:
        raw = request(asset['url'], True, binary=True)
        (remote_dir / asset['name']).write_bytes(raw)
    verify_files(remote_dir)
    report['remote_original_bytes_verified'] = True
    if release['draft']:
        release = request('releases/' + str(release['id']), True, 'PATCH', {'draft': False, 'make_latest': 'true'})
    if release['draft'] or not release['published_at']:
        raise ValueError('GitHub publication not confirmed')
    release = request('releases/' + str(release['id']), True, 'PATCH', {'body': body(release['published_at'])})
    report.update(release_url=release['html_url'], published_at=release['published_at'],
                  assets=CFG['assets'], release_body_sha256=digest(release['body'].encode()), status='PASS')


def anonymous(report):
    if any(os.environ.get(k) for k in ('GITHUB_TOKEN', 'GH_TOKEN', 'CLOUD_READ_TOKEN', 'EVIWEAVE_API_KEY')):
        raise ValueError('anonymous process has credential environment')
    report['tag'] = tag_identity(False)
    release = release_or_none(False)
    if not release or release['draft'] or release['prerelease'] or not release['published_at']:
        raise ValueError('anonymous public release not available')
    if {e['name'] for e in release['assets']} != {e['name'] for e in CFG['assets']}:
        raise ValueError('anonymous public asset set mismatch')
    downloads = ROOT / '.audit/anonymous-downloads'; downloads.mkdir(parents=True)
    for asset in release['assets']:
        raw = request(asset['browser_download_url'], False, binary=True)
        (downloads / asset['name']).write_bytes(raw)
    verify_files(downloads)
    spec = importlib.util.spec_from_file_location('publication_preparation_control', ROOT / 'cloud_checks/preparation.py')
    cloud = importlib.util.module_from_spec(spec); spec.loader.exec_module(cloud)
    target = ROOT / 'anonymous release'
    target.mkdir()
    c = cloud.Check(target, 'anonymous')
    c.report.update(actual_release_commit=CFG['commit'], release_tree=CFG['tree'])
    c.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                 GIT_ASKPASS='/bin/false', SSH_ASKPASS='/bin/false')
    source = target / 'source'
    try:
        c.run('anonymous-git-tag-source', ['git', '-c', 'credential.helper=', '-c', 'http.extraheader=',
              '-c', 'http.cookiefile=', 'clone', '--depth', '1', '--branch', TAG,
              'https://github.com/yjdy/eviweave.git', source])
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, env=c.env, text=True).strip()
        tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=source, env=c.env, text=True).strip()
        if actual != CFG['commit'] or tree != CFG['tree']:
            raise ValueError('anonymous Git source identity mismatch')
        names = subprocess.check_output(['git', 'ls-files'], cwd=source, env=c.env, text=True).splitlines()
        file_map = {n: digest((source / n).read_bytes()) for n in names}
        if file_map != CFG['files'] or digest(json.dumps(file_map, sort_keys=True, separators=(',', ':')).encode()) != CFG['fingerprint']:
            raise ValueError('anonymous source file fingerprint mismatch')
        raw = request('tarball/refs/tags/' + TAG, False, binary=True)
        tarpath = downloads / 'tag-source.tar.gz'; tarpath.write_bytes(raw)
        tar_files = {}
        with tarfile.open(tarpath) as tar:
            for member in tar.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe public tag tar member')
                if member.isfile():
                    name = str(path.relative_to(path.parts[0]))
                    if name in tar_files:
                        raise ValueError('duplicate tag source file')
                    tar_files[name] = digest(tar.extractfile(member).read())
        if tar_files != file_map:
            raise ValueError('anonymous tag tar differs from Git source')
        archive_root = source / '.audit/original-assets'; archive_root.mkdir(parents=True)
        for asset in CFG['assets']:
            if asset['name'].endswith(('.whl', '.tar.gz')):
                shutil.copyfile(downloads / asset['name'], archive_root / asset['name'])
        c.root = source
        # All caches/HOME/tmp stay in target; no developer or checkout credentials.
        installations = {}
        for kind, package in [('wheel', archive_root / 'eviweave-0.1.1-py3-none-any.whl'),
                              ('sdist', archive_root / 'eviweave-0.1.1.tar.gz')]:
            env = source / '.audit' / (kind + '-env')
            c.run(kind + '-venv', [sys.executable, '-m', 'venv', env])
            py = env / 'bin/python'
            c.run(kind + '-locked-tools', [py, '-m', 'pip', 'install', '--only-binary=:all:', '--require-hashes', '-r', 'locks/ci-tools.txt'])
            c.run(kind + '-public-original-install', [py, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', package])
            result = source / '.audit' / (kind + '-installation.json')
            c.run(kind + '-cli-and-identity', [py, '-B', 'tools/check_installation.py', '--output', result])
            unit = source / '.audit' / (kind + '-tests') / 'unit-results.json'
            c.run(kind + '-installed-unit', [py, '-B', 'cloud_checks/current.py', 'unit', '--project', source, '--output', unit])
            doc = json.loads(unit.read_text())
            if doc['status'] != 'PASS' or doc['not_applicable'] or any(t['status'] != 'PASS' for t in doc['cases']):
                raise ValueError('anonymous installed tests did not all pass')
            provenance = source / '.audit' / (kind + '-provenance.json')
            c.run(kind + '-public-archive-provenance', [py, '-B', 'tools/verify_release.py', '--git-scope', 'head',
                  '--archive-root', archive_root, '--output', provenance])
            c.run(kind + '-environment-preflight', ['bash', 'scripts/prepare_environment.sh', '--python', py, '--check-only'])
            c.run(kind + '-data-preflight', ['bash', 'scripts/prepare_dataspace.sh', '--python', py, '--check-only'])
            installations[kind] = {'tests': doc['tests'], 'skips': 0, 'status': 'PASS',
                                   'provenance': json.loads(provenance.read_text())}
        if any(req['authenticated'] or req['cookies'] for req in REQUESTS):
            raise ValueError('anonymous HTTP requests were authenticated')
        report.update(status='PASS', release_id=release['id'], release_url=release['html_url'],
                      published_at=release['published_at'], assets=CFG['assets'], installations=installations,
                      anonymous_http=True, anonymous_git=True, credential_environment_absent=True,
                      checkout_authentication_separate=True, source_files=len(file_map),
                      source_fingerprint=CFG['fingerprint'], tag_tar_sha256=digest(raw),
                      necessary_startup='Both CLI aliases and environment/data check-only entries; no model/runtime rebuild')
    except Exception as exc:
        c.errors.append(type(exc).__name__)
        raise
    finally:
        c.finish('anonymous')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('publish', 'anonymous'))
    parser.add_argument('--assets', type=Path, default=ROOT / '.audit/frozen')
    args = parser.parse_args()
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('GitHub-hosted runner required')
    report = {'status': 'FAIL', 'mode': args.mode, 'workflow_commit': os.environ['GITHUB_SHA'],
              'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
              'actual_release_commit': CFG['commit'], 'release_tree': CFG['tree'],
              'candidate_fingerprint': CFG['fingerprint'], 'python': platform.python_version(),
              'runner_environment': os.environ['RUNNER_ENVIRONMENT'], 'model_calls': 0,
              'new_benchmark': False, 'local_install_build': False}
    try:
        if args.mode == 'publish':
            publish(args.assets.resolve(), report)
        else:
            anonymous(report)
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            report['http_status'] = exc.code
        raise
    finally:
        report['http_requests'] = REQUESTS
        save(OUT / ('publication-' + args.mode + '.json'), report)


if __name__ == '__main__':
    main()
