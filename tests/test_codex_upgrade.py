"""Upgrade identity, baseline preservation and rejection tests; no model calls."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'tools'), str(PROJECT / 'src'), str(PROJECT / 'scripts')]
import codex_upgrade as upgrade
import host_profile
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.core.task import TaskSpec
from run_v8 import build_v8_harness
import test_public_preparation as public_tests


class UpgradeTests(unittest.TestCase):
    def test_completed_probe_usage_needs_no_experiment_helpers(self):
        from codex_upgrade_probe import completed_turn_usage
        old = {'input_tokens': 200, 'cached_input_tokens': 50, 'output_tokens': 20}
        current = {'input_tokens': 400, 'cached_input_tokens': 100, 'output_tokens': 40}
        self.assertEqual(completed_turn_usage(current, old), old)
        for changed in ({}, {**current, 'input_tokens': True},
                        {**current, 'output_tokens': 19}, {**current, 'cached_input_tokens': 251}):
            with self.assertRaises(ValueError):
                completed_turn_usage(changed, old)

    def setUp(self):
        (PROJECT / '.audit').mkdir(exist_ok=True)
        (PROJECT / '.local').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / '.audit', prefix='codex-upgrade-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
        self.raw['experiment']['workbench']['rootfs_path'] = str(self.root / 'workbench')
        tools = self.root / 'workbench/opt/dataspace/TOOLS.md'
        tools.parent.mkdir(parents=True); tools.write_text('fixture')
        self.runtime = self.root / 'package'
        (self.runtime / upgrade.VENDOR / 'bin').mkdir(parents=True)
        (self.runtime / upgrade.VENDOR / 'bin/codex').write_text('fake executable, never run')
        self.raw['harnesses']['codex']['runtime_path'] = str(self.runtime / upgrade.VENDOR)
        self.config_path = self.root / 'baseline.json'
        self.config_path.write_text(json.dumps(self.raw))
        self.identity = {'version': upgrade.VERSION, 'release_lock_sha256': upgrade.sha(upgrade.RELEASE_LOCK),
                         'binary_sha256': upgrade.sha(self.runtime / upgrade.VENDOR / 'bin/codex')}

    def profile(self):
        doc = public_tests.PublicPreparationTests.valid_profile(self)
        doc['format_version'] = 3
        doc['runtime']['codex_release_sha256'] = upgrade.sha(upgrade.RELEASE_LOCK)
        return doc

    def test_new_profile_is_explicit_and_old_profile_still_valid(self):
        old = public_tests.PublicPreparationTests.valid_profile(self)
        self.assertEqual(host_profile.validate(old), old)
        new = self.profile()
        self.assertEqual(host_profile.validate(new), new)
        self.assertEqual(old['format_version'], 2)

    def test_new_profile_rejects_lock_drift_and_schema_downgrade(self):
        for mutate in ('hash', 'version', 'extra'):
            doc = self.profile()
            if mutate == 'hash': doc['runtime']['codex_release_sha256'] = '0'*64
            if mutate == 'version': doc['format_version'] = 2
            if mutate == 'extra': doc['runtime']['skip_verification'] = True
            with self.assertRaises(ValueError): host_profile.validate(doc)

    def test_new_profile_load_and_dependency_lock_unchanged(self):
        doc = self.profile(); p = self.root / 'profile.json'; p.write_text(json.dumps(doc))
        self.assertEqual(host_profile.load_profile(p), doc)
        baseline = public_tests.PublicPreparationTests.valid_profile(self)
        self.assertEqual(doc['packages'], baseline['packages'])
        self.assertEqual(doc['dependency_lock_sha256'], baseline['dependency_lock_sha256'])

    def test_full_package_lock_rejects_extra_changed_missing_and_symlink(self):
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps({'version': upgrade.VERSION, 'contents': upgrade.contents(self.runtime),
                                   'binary_sha256': self.identity['binary_sha256']}))
        binary = self.runtime / upgrade.VENDOR / 'bin/codex'
        original = binary.read_bytes()
        with patch.object(upgrade, 'RELEASE_LOCK', lock):
            self.assertEqual(upgrade.verify_runtime(self.runtime)['version'], upgrade.VERSION)
            binary.write_text('tampered')
            with self.assertRaises(ValueError): upgrade.verify_runtime(self.runtime)
            binary.write_bytes(original)
            extra = self.runtime / 'extra'; extra.write_text('extra')
            with self.assertRaises(ValueError): upgrade.verify_runtime(self.runtime)
            extra.unlink(); extra.symlink_to(binary)
            with self.assertRaises(ValueError): upgrade.verify_runtime(self.runtime)
            extra.unlink(); binary.unlink()
            with self.assertRaises(ValueError): upgrade.verify_runtime(self.runtime)

    def test_generated_config_preserves_model_prompt_options_and_nondefault_budgets(self):
        self.raw['experiment']['wall_time_seconds'] = 753
        self.raw['experiment']['concurrency'] = 3
        self.raw['experiment']['model']['max_output_tokens'] = 12345
        self.config_path.write_text(json.dumps(self.raw))
        with tempfile.TemporaryDirectory(dir=PROJECT / '.local', prefix='upgrade-unit-') as directory:
            out = Path(directory) / 'config.json'
            with patch.object(upgrade, 'verify_runtime', return_value=self.identity):
                upgrade.configure(self.config_path, self.runtime, out)
            expected = copy.deepcopy(self.raw)
            expected['harnesses']['codex'].update(version='0.170.0', runtime_path=str(self.runtime / upgrade.VENDOR))
            self.assertEqual(json.loads(out.read_text()), expected)
            self.assertEqual(json.loads(self.config_path.read_text()), self.raw)
            with patch.object(upgrade, 'verify_runtime'), self.assertRaises(ValueError):
                upgrade.configure(self.config_path, self.runtime, out)

    def test_config_refuses_wrong_base_version_and_nonprivate_output(self):
        with patch.object(upgrade, 'verify_runtime'), self.assertRaises(ValueError):
            upgrade.configure(self.config_path, self.runtime, self.root / 'not-private.json')
        self.raw['harnesses']['codex']['version'] = '0.170.0'
        self.config_path.write_text(json.dumps(self.raw))
        with patch.object(upgrade, 'verify_runtime'), self.assertRaisesRegex(ValueError, '0.145.0'):
            upgrade.configure(self.config_path, self.runtime, PROJECT / '.local/no-create.json')

    def test_v8_prompt_config_and_budget_equal_but_resume_identity_differs(self):
        before = load_harness_experiment_config(self.config_path)
        h = before.harnesses['codex']
        after = replace(before, harnesses={'codex': replace(h, options={**h.options, 'version': '0.170.0'})})
        task = TaskSpec.load(PROJECT / 'examples/synthetic/tasks/task_1')
        with patch.object(upgrade, 'verify_runtime', return_value=self.identity), patch.dict(os.environ, {'EVIWEAVE_API_KEY': 'fake'}):
            a, b = build_v8_harness(before), build_v8_harness(after)
            first = a.invocation_factory(task, self.root / 'old', self.root / 'old/output')
            second = b.invocation_factory(task, self.root / 'new', self.root / 'new/output')
            self.assertEqual(first.command[first.command.index('--')+1:], second.command[second.command.index('--')+1:])
            old_config = (self.root / 'old/runtime/home/.codex/config.toml').read_text()
            new_config = (self.root / 'new/runtime/home/.codex/config.toml').read_text()
            self.assertEqual(old_config, new_config.replace('goals = false\n', ''))
            self.assertIn('[features]\ngoals = false\n', new_config)
            self.assertEqual(a.wall_time_seconds, b.wall_time_seconds)
            self.assertEqual(a.resume_identity['execution_policy'], b.resume_identity['execution_policy'])
            self.assertNotEqual(a.resume_fingerprint, b.resume_fingerprint)
            self.assertNotIn('codex_upgrade', a.resume_identity)
            (self.runtime / upgrade.VENDOR / 'bin/codex').write_text('changed after creation')
            with self.assertRaisesRegex(ValueError, 'changed after'):
                b.invocation_factory(task, self.root / 'bad', self.root / 'bad/output')

    def test_legacy_runtime_never_enters_upgrade_verifier(self):
        config = load_harness_experiment_config(self.config_path)
        with patch.object(upgrade, 'verify_runtime', side_effect=AssertionError('legacy changed')):
            runner = build_v8_harness(config)
        self.assertNotIn('codex_upgrade', runner.resume_identity)

    def test_upgrade_cannot_be_labelled_v6(self):
        from experiment_config import prepare_version
        before = load_harness_experiment_config(self.config_path)
        prepare_version({'config': before}, 'v6')
        h = before.harnesses['codex']
        after = replace(before, harnesses={'codex': replace(h, options={**h.options, 'version': '0.170.0'})})
        with self.assertRaisesRegex(ValueError, '--version v8'):
            prepare_version({'config': after}, 'v6')

    def test_format3_keeps_runtime_and_namespace_validation(self):
        import environment_lock
        doc = self.profile()
        with patch('host_profile.runtime_check', return_value={'checked': True}) as runtime, \
             patch('host_profile.capability') as isolation:
            result = environment_lock.check_runtime('config fixture', doc)
        self.assertEqual(result, {'checked': True})
        runtime.assert_called_once_with(doc, 'config fixture')
        self.assertEqual(isolation.call_count, 1)

    def test_format2_cannot_silently_select_new_runtime(self):
        from experiment_store import sha
        import runtime_support
        import public_resources
        doc = public_tests.PublicPreparationTests.valid_profile(self)
        config = load_harness_experiment_config(self.config_path)
        h = config.harnesses['codex']
        config = replace(config, harnesses={'codex': replace(h, options={**h.options, 'version': '0.170.0'})})
        r = doc['runtime']
        with patch('public_resources.verify_workbench', return_value={'docker_host':r['docker_host'],'image':r['image']}), \
             patch('runtime_support.inventory', return_value=r['codex_inventory']), \
             patch('host_profile.sha', side_effect=lambda p: json.loads(public_resources.LOCK.read_text())['codex']['binary_sha256']
                   if str(p).endswith('/bin/codex') else sha(p)):
            with self.assertRaisesRegex(ValueError, 'configuration paths'):
                host_profile.runtime_check(doc, config)


if __name__ == '__main__': unittest.main()
