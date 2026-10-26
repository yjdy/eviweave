"""Maintenance exception, retained history, metadata and candidate gates."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'cloud_checks'))
import verify_release as release
import current as ci
import release_candidate as candidate


class MaintenanceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.members = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / 'provenance').glob('*.json')}
        self.layer = json.loads(self.members[release.VERSION_LAYER])
        self.content = {e['destination']: (ROOT / e['destination']).read_bytes()
                        for e in json.loads(self.members['provenance/extraction-manifest.json'])['files']}

    def changed(self, callback):
        layer = copy.deepcopy(self.layer)
        callback(layer)
        return dict(self.members, **{release.VERSION_LAYER: json.dumps(layer).encode()})

    def test_version_exception_reverses_exactly_and_retains_19_tables(self):
        entries = release.business_manifest(self.members, self.content)
        self.assertEqual(len(entries), 22)
        self.assertEqual(sum(e['policy'] == 'verbatim' for e in entries), 17)
        self.assertEqual(len(self.layer['retained_provenance']), 19)
        self.assertNotIn(release.VERSION_LAYER, self.layer['retained_provenance'])
        self.assertEqual(ci.documents(ROOT)['version'], '0.1.1')

    def test_extra_import_comment_or_version_bytes_cannot_be_reauthorized(self):
        for extra in (b'import os\n', b'# extra comment\n', b'\n'):
            content = dict(self.content)
            content[release.VERSION_FILE] += extra
            def changed(layer):
                next(e for e in layer['files'] if e['destination'] == release.VERSION_FILE)['release_sha256'] = hashlib.sha256(content[release.VERSION_FILE]).hexdigest()
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'outside the single declaration'):
                release.business_manifest(self.changed(changed), content)
        for raw in (self.content[release.VERSION_FILE].replace(b'0.1.1', b'0.1.2'),
                    self.content[release.VERSION_FILE] + b'__version__ = "0.1.1"\n'):
            with self.assertRaises(ValueError):
                release.business_manifest(self.members, dict(self.content, **{release.VERSION_FILE: raw}))

    def test_other_business_file_cannot_use_version_exception(self):
        def changed(layer):
            entry = next(e for e in layer['files'] if e['destination'] == release.VERSION_FILE)
            entry['destination'] = 'src/dataspace_baselines/cli.py'
        with self.assertRaisesRegex(ValueError, 'invalid or duplicate'):
            release.effective_provenance(self.changed(changed))
        content = dict(self.content); content['src/dataspace_baselines/cli.py'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'content digest mismatch'):
            release.business_manifest(self.members, content)

    def test_exception_cannot_change_its_allowed_replacement(self):
        def changed(layer):
            next(e for e in layer['files'] if e['destination'] == release.VERSION_FILE)['version_only']['new'] = '__version__ = "0.1.2"'
        with self.assertRaisesRegex(ValueError, 'invalid or duplicate'):
            release.effective_provenance(self.changed(changed))

    def test_each_retained_table_and_predecessor_is_protected(self):
        for name in self.layer['retained_provenance']:
            changed = dict(self.members); changed[name] += b'\n'
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.effective_provenance(changed)
        for key in ('source_sha256', 'baseline_sha256'):
            def changed(layer):
                next(e for e in layer['files'] if e['destination'] == release.VERSION_FILE)[key] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'source mismatch'):
                release.effective_provenance(self.changed(changed))
        with self.assertRaisesRegex(ValueError, 'predecessor commit'):
            release.effective_provenance(self.changed(lambda layer: layer.update(source_commit='0' * 40)))

    def test_versions_and_bilingual_text_must_match(self):
        (ROOT / '.audit').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.audit') as directory:
            target = Path(directory)
            for name in ('README.md', 'README.zh-CN.md', 'pyproject.toml', 'CITATION.cff', release.VERSION_FILE, '.github/workflows/preparation.yml'):
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (target / name).write_bytes((ROOT / name).read_bytes())
            # Ignore link targets here to isolate version metadata checks.
            for name in ('pyproject.toml', 'CITATION.cff', release.VERSION_FILE):
                original = (target / name).read_text()
                (target / name).write_text(original.replace('0.1.1', '0.1.2'))
                with patch.object(ci.Path, 'is_file', return_value=True), self.assertRaisesRegex(ValueError, 'version or license mismatch'):
                    ci.documents(target)
                (target / name).write_text(original)
        en, zh = ((ROOT / name).read_text() for name in ('README.md', 'README.zh-CN.md'))
        with self.assertRaisesRegex(ValueError, 'numbers differ'):
            ci.readme_contract(en, zh.replace('v0.1.1', 'v0.1.2'))

    def test_candidate_workflow_requires_primary_matrix_then_serial_full(self):
        import yaml
        doc = yaml.load((ROOT / '.github/workflows/release-candidate.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(doc['on']['push']['branches'], ['codex/v0.1.1-release'])
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        self.assertEqual(doc['jobs']['packages']['needs'], 'primary')
        self.assertEqual(doc['jobs']['packages']['strategy']['matrix']['python'], list(candidate.PYTHONS))
        self.assertEqual(doc['jobs']['full']['needs'], 'packages')
        self.assertEqual(doc['jobs']['full']['strategy']['max-parallel'], '1')
        self.assertEqual(doc['jobs']['freeze']['needs'], ['primary', 'packages', 'full'])
        for job in doc['jobs'].values():
            self.assertEqual(job['runs-on'], 'ubuntu-22.04')
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout@'):
                    self.assertEqual(step['with'], {'persist-credentials': 'false'})
        text = (ROOT / '.github/workflows/release-candidate.yml').read_text()
        self.assertNotIn('contents: write', text)
        self.assertNotIn('gh release', text)

    def test_receipts_require_exact_source_run_outcome_and_no_model(self):
        identity = {'actual_candidate_commit': 'a' * 40, 'candidate_tree': 'b' * 40,
                    'candidate_fingerprint': 'c' * 64, 'candidate_file_count': 180}
        doc = dict(identity, status='PASS', workflow_commit='a' * 40, run_id='42', run_attempt='1',
                   runner_environment='github-hosted', model_calls=0, commands=[{'returncode': 0}])
        with patch.dict(os.environ, GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1'):
            candidate.require_receipt(doc, identity)
            for key, value in [('workflow_commit', 'd' * 40), ('candidate_tree', 'd' * 40),
                               ('run_id', '43'), ('status', 'FAIL'), ('runner_environment', 'self-hosted'),
                               ('model_calls', 1), ('commands', [{'returncode': 1}])]:
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity or outcome mismatch'):
                    candidate.require_receipt(dict(doc, **{key: value}), identity)


if __name__ == '__main__':
    unittest.main()
