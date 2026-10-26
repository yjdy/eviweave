"""Current-source CI policy and additive provenance contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('current_ci_contract', ROOT / 'cloud_checks/current.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
sys.path.insert(0, str(ROOT / 'tools'))
from verify_release import effective_provenance


class CurrentCITests(unittest.TestCase):
    def workflow(self):
        return (ROOT / '.github/workflows/preparation.yml').read_text()

    def test_workflow_covers_main_with_default_lightweight_matrix(self):
        result = ci.validate_workflow(self.workflow())
        self.assertEqual(result['package_python'], ['3.10.22', '3.12.3', '3.13.16'])
        self.assertFalse(result['full_default'])

    def test_wrong_main_trigger_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cover main'):
            ci.validate_workflow(self.workflow().replace('branches: [main]', 'branches: [old-branch]', 1))

    def test_full_must_be_opt_in_and_require_package_matrix(self):
        for changed in (self.workflow().replace('default: false', 'default: true', 1),
                        self.workflow().replace("    if: " + ci.FULL_GUARD, "    if: ${{ always() }}", 1),
                        self.workflow().replace('needs: unit-gate', 'needs: nothing', 1)):
            with self.assertRaises(ValueError):
                ci.validate_workflow(changed)
        for event, value in [('push', True), ('pull_request', True), ('workflow_dispatch', False)]:
            self.assertFalse(ci.full_requested(event, value))
        self.assertTrue(ci.full_requested('workflow_dispatch', True))

    def test_fixed_release_checkout_is_rejected(self):
        text = self.workflow().replace('persist-credentials: false', 'persist-credentials: false\n          ref: v0.1.0', 1)
        with self.assertRaisesRegex(ValueError, 'event source'):
            ci.validate_workflow(text)

    def test_source_must_match_event_sha(self):
        with patch.object(ci.subprocess, 'check_output', return_value='a' * 40):
            with self.assertRaisesRegex(ValueError, 'event commit'):
                ci.identity(ROOT, {'GITHUB_SHA': 'b' * 40})

    def test_candidate_bytes_and_pull_request_merge_identity(self):
        (ROOT / '.audit').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.audit') as directory:
            project = Path(directory)
            (project / 'fixture').write_bytes(b'original')
            responses = ['a' * 40, 'fixture\n', b'original', 'b' * 40]
            event = {'GITHUB_SHA': 'a' * 40, 'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REF': 'refs/pull/1/merge', 'GITHUB_HEAD_REF': 'feature', 'GITHUB_BASE_REF': 'main'}
            with patch.object(ci.subprocess, 'check_output', side_effect=responses):
                result = ci.identity(project, event)
            self.assertEqual(result['actual_candidate_commit'], event['GITHUB_SHA'])
            self.assertEqual(result['event_ref'], event['GITHUB_REF'])
            self.assertEqual(result['candidate_file_count'], 1)
            with patch.object(ci.subprocess, 'check_output', side_effect=['a' * 40, 'fixture\n', b'changed']):
                with self.assertRaisesRegex(ValueError, 'worktree differs'):
                    ci.identity(project, event)

    def test_python310_only_uses_declared_host_exclusions(self):
        class Case:
            def __init__(self, name): self.name = name
            def id(self): return self.name
        # More than the old fixed count: additional cases must remain selected.
        cases = [Case(n) for n in sorted(ci.HOST_ONLY)] + [Case('new.case.' + str(n)) for n in range(300)]
        selected, excluded = ci.select_cases(cases, (3, 10))
        self.assertEqual(len(selected), 300)
        self.assertEqual({e['case'] for e in excluded}, ci.HOST_ONLY)
        for version in [(3, 12), (3, 13)]:
            self.assertEqual(ci.select_cases(cases, version), (cases, []))
        with self.assertRaisesRegex(ValueError, 'missing from discovery'):
            ci.select_cases(cases[1:], (3, 10))

    def test_bilingual_commands_and_numbers_must_agree(self):
        english = (ROOT / 'README.md').read_text()
        chinese = (ROOT / 'README.zh-CN.md').read_text()
        self.assertTrue(ci.readme_contract(english, chinese))
        with self.assertRaisesRegex(ValueError, 'commands differ'):
            ci.readme_contract(english, chinese.replace('--check-only', '--wrong', 1))
        with self.assertRaisesRegex(ValueError, 'numbers differ'):
            ci.readme_contract(english, chinese + '\n999999\n')

    def test_latest_ci_source_layer_is_additive_and_effective(self):
        members = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / 'provenance').glob('*.json')}
        layer = json.loads(members['provenance/main-ci-baseline.json'])
        self.assertEqual(len(layer['retained_provenance']), 18)
        latest = effective_provenance(members)
        import hashlib
        for name, expected in latest.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)

    def test_ci_source_rejects_old_layer_tampering_and_wrong_predecessor(self):
        members = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / 'provenance').glob('*.json')}
        old = 'provenance/preparation-scripts-baseline.json'
        corrupted = dict(members);corrupted[old] += b'\n'
        with self.assertRaisesRegex(ValueError, 'retained provenance'):
            effective_provenance(corrupted)
        name = 'provenance/main-ci-baseline.json'
        for change in ('predecessor', 'business'):
            corrupted = copy.copy(members);layer = json.loads(members[name])
            if change == 'predecessor': layer['files'][0]['source_sha256'] = '0' * 64
            else: layer['files'][0]['destination'] = 'src/dataspace_baselines/cli.py'
            corrupted[name] = json.dumps(layer).encode()
            with self.assertRaises(ValueError):
                effective_provenance(corrupted)


if __name__ == '__main__':
    unittest.main()
