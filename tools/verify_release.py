#!/usr/bin/env python3
"""Offline checks of staged files, archives and installed frozen modules."""
import argparse
import ast
from email.parser import Parser
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import pkgutil
import subprocess
import tarfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def effective_provenance(members):
    """Validate the new overlay against retained history, then resolve latest hashes."""
    newest = json.loads(members['provenance/config-rounding-baseline.json'])
    history = ('extraction-manifest', 'runtime-baseline', 'regression-baseline', 'evidence-baseline',
               'isolation-baseline', 'unified-baseline', 'observability-baseline', 'bugfix-baseline')
    retained = newest['retained_provenance']
    historical_names = {name for name in members if name.startswith('provenance/') and name.endswith('.json')
                        and name != 'provenance/config-rounding-baseline.json'}
    if set(retained) != historical_names or any(digest(members[name]) != sha for name, sha in retained.items()):
        raise ValueError('retained provenance set/digest mismatch')
    latest = {}
    for name in history:
        for entry in json.loads(members['provenance/' + name + '.json'])['files']:
            latest[entry['destination']] = entry['release_sha256']
    seen = set()
    for entry in newest['files']:
        name = entry['destination']
        if name in seen or name.startswith('src/') or name in retained or name == 'provenance/config-rounding-baseline.json':
            raise ValueError('invalid or duplicate provenance override')
        if (entry['source_sha256'] != latest.get(name) or
                entry['source_commit'] != (newest['source_commit'] if name in latest else None)):
            raise ValueError('provenance override source mismatch: ' + name)
        seen.add(name)
        latest[name] = entry['release_sha256']
    return latest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive-root', type=Path, help='explicit project-local directory containing one wheel and sdist')
    parser.add_argument('--git-scope', choices=['staged','head','worktree'], default='staged', help='explicit Git content scope; empty scopes fail')
    args = parser.parse_args()
    root = args.project.resolve()
    manifest = json.loads((root/'provenance/extraction-manifest.json').read_text())
    expected = {str(PurePosixPath(e['destination']).relative_to('src')):e for e in manifest['files']}
    checks = []
    def require(condition, detail):
        if not condition:
            raise ValueError(detail)
        checks.append(detail)
    def inspect(names, label):
        forbidden = {'docs','.docs','.audit','.local','.git','__pycache__','.venv','.runtimes','runs','input','output','data','controlled','relays','dataspace_agent',
                     'api-calls', 'raw', 'billing', 'checkpoints'}
        for name in names:
            path = PurePosixPath(name)
            runtime_names = {'experiment.json', 'attempt.json', 'run.json', 'selection.json', 'prediction.csv',
                             'summary.json', 'summary.csv', 'score.json', 'events.jsonl', 'stdout.log', 'stderr.log',
                             'config.resolved.yaml', 'environment-lock.json', 'report-set.json',
                             'observation.json', 'usage.json', 'cost.json', 'calls.csv', 'cost-breakdown.csv',
                             'analysis.json', 'import-receipt.json', 'capability.json', 'server-truth.json'}
            require(path.name not in runtime_names, label + ': no unified execution artifact ' + name)
            require(not path.name.startswith('eviweave-v0.1.0-v6-evidence') and not path.name.endswith('.zip'),label+': no experimental attachment '+name)
            require(not any(part in forbidden or part.startswith('.venv') for part in path.parts),label+': clean path '+name)
            require(not (path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.pyc','.env'}),label+': no local config '+name)
            require(not (path.parent.name == 'configs' and path.name.startswith('local')),label+': no private config '+name)
            require(path.suffix not in {'.sse', '.jsonl', '.log', '.bin'}, label+': no raw call/telemetry stream '+name)
    def inspect_content(name, data, label):
        path = PurePosixPath(name)
        if path.suffix == '.json':
            value = json.loads(data)
            if isinstance(value, dict):
                runtime_kinds = {'eviweave-call-import', 'eviweave-call-analysis', 'eviweave-call-generation',
                                 'eviweave-call-report-set', 'eviweave-observation-capability'}
                require(value.get('kind') not in runtime_kinds and value.get('object') != 'response'
                        and 'resourceLogs' not in value and 'rows' not in value,
                        label+': no disguised response/billing/call evidence '+name)
        if path.suffix == '.csv':
            require(b'bill_id' not in data.splitlines()[0] if data.splitlines() else True,
                    label+': no private bill rows '+name)
    if args.git_scope == 'staged':
        command = ['git','-C',str(root),'diff','--cached','--name-only','--diff-filter=ACMR','-z']
    elif args.git_scope == 'head':
        command = ['git','-C',str(root),'ls-tree','-r','--name-only','-z','HEAD']
    else:
        command = ['git','-C',str(root),'ls-files','--cached','--others','--exclude-standard','-z']
    staged = sorted(set(n for n in subprocess.check_output(command).decode().split('\0') if n))
    require(bool(staged),args.git_scope+' files present')
    inspect(staged,'git '+args.git_scope)
    def git_content(name):
        if args.git_scope == 'worktree': return (root/name).read_bytes()
        spec = ':'+name if args.git_scope == 'staged' else 'HEAD:'+name
        return subprocess.check_output(['git','-C',str(root),'show',spec])
    if args.git_scope != 'staged':
        require({e['destination'] for e in expected.values()}.issubset(staged),'Git scope contains all 22 modules')
    for name in staged:
        inspect_content(name, git_content(name), 'git '+args.git_scope)
        if name in {e['destination'] for e in expected.values()}:
            entry = next(e for e in expected.values() if e['destination'] == name)
            require(digest(git_content(name)) == entry['release_sha256'],'git digest '+name)
    archives = []
    archive_root = (args.archive_root or root/'dist').resolve()
    require(archive_root.is_relative_to(root), 'archives remain inside project')
    wheel = list(archive_root.glob('*.whl'));sdist = list(archive_root.glob('*.tar.gz'))
    require(len(wheel)==len(sdist)==1,'exactly one wheel and sdist')
    for artifact in [*wheel,*sdist]:
        if artifact.suffix == '.whl':
            with zipfile.ZipFile(artifact) as archive:
                members = {n:archive.read(n) for n in archive.namelist() if not n.endswith('/')}
            source = {n:data for n,data in members.items() if n.startswith('dataspace_baselines/') and n.endswith('.py')}
            metadata_name = next(n for n in members if n.endswith('.dist-info/METADATA'))
            metadata = Parser().parsestr(members[metadata_name].decode())
            require(metadata.get_all('Requires-Dist') == ['PyYAML<7,>=6'],'wheel runtime dependency is only PyYAML>=6,<7')
            require(metadata['Requires-Python'] == '>=3.10','Python floor is 3.10')
            entrypoints = members[next(n for n in members if n.endswith('/entry_points.txt'))].decode()
            require('eviweave = dataspace_baselines.cli:main' in entrypoints and 'dataspace-baselines = dataspace_baselines.cli:main' in entrypoints,'both CLI entrypoints packaged')
        else:
            with tarfile.open(artifact) as archive:
                members = {str(PurePosixPath(m.name).relative_to(PurePosixPath(m.name).parts[0])):archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
            source = {n.removeprefix('src/'):data for n,data in members.items() if n.startswith('src/dataspace_baselines/') and n.endswith('.py')}
            required = {'scripts/install_codex_runtime.sh','scripts/install_codex_runtime.py','scripts/prepare_workbench.sh',
                'scripts/prepare_workbench.py','scripts/runtime_support.py','scripts/project_docker.py',
                'docker/Dockerfile.workbench','docker/workbench/python-packages.txt','docker/workbench/system-packages.txt',
                'tools/smoke_runtime.py','tools/synthetic_executor.py','examples/synthetic/tasks/task_1/task.json',
                'examples/synthetic/tasks/task_2/task.json','examples/synthetic/expected/task_1.csv','examples/synthetic/expected/task_2.csv'}
            require(required.issubset(members), 'sdist contains all P2 preparation and synthetic inputs')
            required_p3 = {'.github/workflows/offline.yml','tools/run_regression.py','tools/regression_cases.py','tools/runtime_regression.py','tools/config_lifecycle.py','tools/sandbox_probe.py','tools/ci_check.py','tools/check_installation.py','tools/unit_worker.py','tests/test_native_cli.py','tests/test_batch_identity.py','tests/test_codex_history.py','tests/test_regression.py','tests/test_jail_contract.py','tests/integration/test_historical_runtime.py','provenance/regression-baseline.json','provenance/regression.zh-CN.md'}
            require(required_p3.issubset(members), 'sdist contains all P3 suites, diagnostic tools, provenance and CI workflow')
            required_p4={'tools/package_evidence.py','tools/evidence_support.py','tools/evidence_verify.py','tests/test_evidence_packaging.py','provenance/evidence-baseline.json','provenance/evidence.zh-CN.md'}
            require(required_p4.issubset(members), 'sdist contains P4 evidence tools, tests and provenance')
            required_isolation = {'tools/standalone.py','tools/verify_isolation.py','tests/test_standalone.py',
                'provenance/evaluation-lock.json','provenance/isolation.zh-CN.md','provenance/isolation-baseline.json',
                'scripts/check_task33.sh','tools/check_task33.py','tests/test_task33_terminal.py'}
            require(required_isolation.issubset(members), 'sdist contains independent resource and isolation tools')
            required_unified = {'scripts/install.sh','scripts/env.sh','scripts/experiment.sh','configs/env.sh.example',
                'tools/install_environment.py','tools/environment_lock.py','tools/experiment.py','tools/experiment_config.py',
                'tools/experiment_store.py','tools/experiment_scheduler.py','tools/experiment_verify.py','tools/unified_smoke.py',
                'tests/test_unified_experiment.py','provenance/unified-baseline.json','provenance/unified.zh-CN.md',
                'locks/README.md','locks/ci-tools.txt','locks/cpython312-linux-x86_64.json','locks/cpython313-linux-x86_64.json',
                'locks/cpython312-linux-x86_64.txt','locks/cpython313-linux-x86_64.txt'}
            require(required_unified.issubset(members), 'sdist contains unified workflow, profiles, scripts and tests')
            required_observation = {'tools/observability_probe.py', 'tools/observability_session.py',
                'tools/observability_store.py', 'tools/usage_normalize.py', 'tools/cost_accounting.py',
                'tools/cost_import.py', 'tools/cost_reports.py', 'tools/cost_cli.py',
                'tests/test_cost_observability.py', 'configs/observability.example.json', 'configs/pricing.example.json',
                'provenance/observability-contract-v1.json', 'provenance/observability-mapping-v1.json',
                'provenance/observability-baseline.json', 'provenance/observability.zh-CN.md'}
            require(required_observation.issubset(members), 'sdist contains offline observation contract, tools, tests and provenance')
            required_bugfix = {'provenance/bugfix-baseline.json', 'provenance/bugfix.zh-CN.md'}
            require(required_bugfix.issubset(members), 'sdist contains bugfix provenance and compatibility policy')
            for name in required_bugfix:
                require(members[name] == (root/name).read_bytes(), 'sdist current bugfix metadata '+name)
            required_config_rounding = {'provenance/config-rounding-baseline.json', 'provenance/config-rounding.zh-CN.md',
                                       'tests/test_config_rounding.py'}
            require(required_config_rounding.issubset(members), 'sdist contains configuration/rounding fixes and provenance')
            for name in required_config_rounding:
                require(members[name] == (root/name).read_bytes(), 'sdist current configuration/rounding content '+name)
            for name, expected_sha in effective_provenance(members).items():
                require(name in members and members[name] == (root/name).read_bytes(), 'sdist current effective content '+name)
                require(digest(members[name]) == expected_sha, 'effective provenance digest '+name)
            for name in required_observation:
                require(members[name] == (root/name).read_bytes(), 'sdist current observation content '+name)
            for name in required_unified:
                require(members[name] == (root/name).read_bytes(), 'sdist current unified content '+name)
            for suffix in ('312', '313'):
                profile_name = 'locks/cpython'+suffix+'-linux-x86_64'
                profile = json.loads(members[profile_name+'.json'])
                require(profile['format_version'] == 1 and profile['python']['version'] == ('3.12.3' if suffix == '312' else '3.13.16'), 'separate exact Python profile '+suffix)
                requirements = members[profile_name+'.txt'].decode()
                for package, entry in profile['packages'].items():
                    require(package+'=='+entry['version']+' --hash=sha256:'+entry['sha256'] in requirements, 'hashed dependency lock '+package+suffix)
                    require(entry['url'].startswith('https://files.pythonhosted.org/') and len(entry['sha256'])==64 and bool(entry['files']), 'artifact source and content lock '+package+suffix)
            for name in required_isolation:
                require(members[name] == (root/name).read_bytes(), 'sdist current isolation content '+name)
            for name in required_p4:
                require(members[name] == (root/name).read_bytes(), 'sdist current P4 content '+name)
            for name in required_p3:
                require(members[name] == (root/name).read_bytes(), 'sdist current P3 content '+name)
        inspect(members,artifact.name)
        require(set(source)==set(expected),artifact.name+': exactly 22 whitelisted Python modules')
        for name,data in source.items():
            require(digest(data)==expected[name]['release_sha256'],artifact.name+': release digest '+name)
            ast.parse(data,filename=name,feature_version=(3,10))
            require(b'/home/ecs-user/' not in data and b'workspace/DataSpace' not in data,artifact.name+': no host path '+name)
        for name,data in members.items():
            inspect_content(name, data, artifact.name)
            if name == 'configs/env.sh.example':
                require(b'EVIWEAVE_API_KEY=""' in data and b'/home/' not in data and b'api.env' not in data, artifact.name+': environment template has no credential values')
            elif name.startswith('configs/'):
                if name.endswith('.json'):
                    require(name in {'configs/observability.example.json', 'configs/pricing.example.json'}, artifact.name+': only whitelisted public observation templates')
                require(b'example.invalid' in data and b'/home/' not in data and b'api.env' not in data,artifact.name+': template endpoint only')
        archives.append({'name':artifact.name,'sha256':digest(artifact.read_bytes()),'file_count':len(members)})
    import dataspace_baselines
    installed_root = Path(dataspace_baselines.__file__).resolve().parent
    require(root/'src/dataspace_baselines' != installed_root,'installed wheel used outside source tree')
    require(installed_root.is_relative_to(root/'.audit'),'isolated installation remains inside project')
    require(dataspace_baselines.__version__ == importlib.metadata.version('eviweave') == '0.1.0','installed version 0.1.0')
    imports = []
    for info in pkgutil.walk_packages(dataspace_baselines.__path__,dataspace_baselines.__name__+'.'):
        if info.name.endswith('.__main__'):
            continue
        module = importlib.import_module(info.name)
        require(Path(module.__file__).resolve().is_relative_to(installed_root),'installed module origin '+info.name)
        imports.append(info.name)
    for name,entry in expected.items():
        installed = installed_root/Path(name).relative_to('dataspace_baselines')
        require(digest(installed.read_bytes())==entry['release_sha256'],'installed digest '+name)
        if entry['policy']=='verbatim':
            require(entry['release_sha256']==entry['source_sha256'],'frozen digest '+name)
    result = {'status':'PASS','checks':len(checks),'git_scope':args.git_scope,'git_file_count':len(staged),'archives':archives,'installed_module_count':len(imports)+1,'verbatim_file_count':sum(e['policy']=='verbatim' for e in expected.values()),'runtime_dependencies':importlib.metadata.requires('eviweave')}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
