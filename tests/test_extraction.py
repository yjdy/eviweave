import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import pkgutil
import tempfile
import unittest
from unittest.mock import patch

import yaml
import dataspace_baselines
from dataspace_baselines.cli import _build_parser, main
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.doctor import run_doctor, _workbench_rootfs_check
from dataspace_baselines.harnesses import available_harnesses, build_harness
from dataspace_baselines.harnesses.native_common import configured_runtime_binds

PROJECT = Path(__file__).resolve().parents[1]


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / '.audit').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT/'.audit',prefix='config tests ')
        self.root = Path(self.tmp.name)
        self.path = self.root/'config space.yaml'
        self.raw = yaml.safe_load((PROJECT/'configs/codex.example.yaml').read_text())
        self.raw['experiment']['workbench']['rootfs_path'] = 'work bench/rootfs'
        self.raw['harnesses']['codex']['runtime_path'] = 'run time/codex-{version}'
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        self.path.write_text(yaml.safe_dump(self.raw),encoding='utf-8')

    def test_paths_independent_of_working_directory(self):
        config = load_harness_experiment_config(self.path)
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            other = load_harness_experiment_config(self.path.name)
        finally:
            os.chdir(cwd)
        self.assertEqual(config,other)
        self.assertEqual(config.workbench.rootfs_path,self.root/'work bench/rootfs')
        self.assertEqual(configured_runtime_binds(config.harnesses['codex']),((self.root/'run time/codex-0.145.0','/mnt/runtime/codex'),))

    def test_absolute_paths_and_legacy_anthropic(self):
        self.raw['experiment']['workbench']['rootfs_path'] = str(self.root/'absolute bench')
        self.raw['harnesses']['codex']['runtime_path'] = str(self.root/'absolute runtime-{version}')
        self.raw['experiment']['model']['anthropic_base_url'] = 'https://example.invalid/legacy/'
        self.save();config = load_harness_experiment_config(self.path)
        self.assertEqual(config.model.anthropic_base_url,'https://example.invalid/legacy/')
        self.assertEqual(configured_runtime_binds(config.harnesses['codex'])[0][0],self.root/'absolute runtime-0.145.0')
        self.assertEqual(config.workbench.rootfs_path,self.root/'absolute bench')

    def test_optional_anthropic_and_execution_identity(self):
        config = load_harness_experiment_config(self.path)
        self.assertEqual(config.model.anthropic_base_url,'')
        runner = build_harness('codex',config)
        identity = runner.resume_identity
        self.assertEqual(identity['prompt_contract'],'relation-v6')
        self.assertEqual(identity['execution_policy']['auto_compact_token_limit'],942656)
        self.assertEqual(identity['execution_policy']['simple_fixes']['version'],'simple-fixes-v1')

    def test_default_explicit_codex_and_compatibility(self):
        parser = _build_parser()
        for command, options in [('run',['--task-dir','task','--run-dir','run']),('batch',['--input-root','tasks','--run-root','batch'])]:
            args = [command,'--config',str(self.path),*options]
            self.assertEqual(parser.parse_args(args),parser.parse_args([*args,'--harness','codex']))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parser.parse_args([*args,'--harness','claude-code'])
            self.assertEqual(error.exception.code,2)
        self.assertEqual(available_harnesses(),('codex',))
        with self.assertRaisesRegex(KeyError,'unsupported harness'):
            build_harness('smolagents',load_harness_experiment_config(self.path))

    def test_reject_unsupported_config(self):
        self.raw['harnesses']['claude-code'] = {'enabled':False}
        self.save()
        self.assertEqual(set(load_harness_experiment_config(self.path).harnesses),{'codex'})
        self.raw['harnesses']['claude-code']['enabled'] = True
        self.save()
        with self.assertRaisesRegex(ValueError,'unsupported harness'):
            load_harness_experiment_config(self.path)

    def test_doctor_missing_dependencies(self):
        config = load_harness_experiment_config(self.path)
        with patch('shutil.which',return_value=None), patch('subprocess.run',side_effect=FileNotFoundError('missing tool')), patch.dict(os.environ,{},clear=True):
            checks = run_doctor(config)
        self.assertTrue(checks)
        self.assertTrue(all(not c.ok for c in checks))
        self.assertIn('inference credential',[c.name for c in checks])
        with patch('dataspace_baselines.doctor._jailed_version') as version, patch('dataspace_baselines.doctor._command_check'), patch('dataspace_baselines.doctor._workbench_rootfs_check'), patch('dataspace_baselines.doctor._workbench_tools_check'):
            version.return_value.ok = False
            run_doctor(config)
            self.assertEqual(version.call_args.args[2],configured_runtime_binds(config.harnesses['codex']))

    def test_doctor_existing_rootfs_without_docker(self):
        config = load_harness_experiment_config(self.path)
        tools = config.workbench.rootfs_path/'opt/dataspace';tools.mkdir(parents=True)
        (tools/'TOOLS.md').write_text('tools')
        (tools/'capabilities.json').write_text('{}')
        (config.workbench.rootfs_path.parent/'workbench-manifest.json').write_text(json.dumps({'image':config.workbench.image}))
        with patch('subprocess.run',side_effect=FileNotFoundError('docker')):
            self.assertFalse(_workbench_rootfs_check(config).ok)

    def test_all_imports_and_verbatim_files(self):
        for item in pkgutil.walk_packages(dataspace_baselines.__path__,dataspace_baselines.__name__+'.'):
            if not item.name.endswith('.__main__'):
                importlib.import_module(item.name)
        manifest = json.loads((PROJECT/'provenance/extraction-manifest.json').read_text())
        self.assertEqual(len(manifest['files']),22)
        for entry in manifest['files']:
            if entry['policy'] == 'verbatim':
                installed = Path(dataspace_baselines.__file__).parent/Path(entry['destination']).relative_to('src/dataspace_baselines')
                self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(),entry['source_sha256'],entry['destination'])


if __name__ == '__main__':
    unittest.main()
