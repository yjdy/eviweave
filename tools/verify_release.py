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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
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
        forbidden = {'docs','.docs','.audit','.git','__pycache__','.venv','.runtimes','runs','input','output','data','controlled','relays','dataspace_agent'}
        for name in names:
            path = PurePosixPath(name)
            require(not any(part in forbidden or part.startswith('.venv') for part in path.parts),label+': clean path '+name)
            require(not (path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.pyc','.env'}),label+': no local config '+name)
    staged = subprocess.check_output(['git','-C',str(root),'diff','--cached','--name-only','-z']).decode().split('\0')
    staged = [n for n in staged if n]
    require(bool(staged),'staged files present')
    inspect(staged,'git')
    archives = []
    wheel = list((root/'dist').glob('*.whl'));sdist = list((root/'dist').glob('*.tar.gz'))
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
        inspect(members,artifact.name)
        require(set(source)==set(expected),artifact.name+': exactly 22 whitelisted Python modules')
        for name,data in source.items():
            require(digest(data)==expected[name]['release_sha256'],artifact.name+': release digest '+name)
            ast.parse(data,filename=name,feature_version=(3,10))
            require(b'/home/ecs-user/' not in data and b'workspace/DataSpace' not in data,artifact.name+': no host path '+name)
        for name,data in members.items():
            if name.startswith('configs/'):
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
    result = {'status':'PASS','checks':len(checks),'staged_file_count':len(staged),'archives':archives,'installed_module_count':len(imports)+1,'verbatim_file_count':sum(e['policy']=='verbatim' for e in expected.values()),'runtime_dependencies':importlib.metadata.requires('eviweave')}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
