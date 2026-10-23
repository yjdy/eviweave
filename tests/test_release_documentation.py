"""The documentation overlay must extend, not replace, authenticated history."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from verify_release import effective_provenance


class DocumentationProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.members = {p.relative_to(ROOT).as_posix(): p.read_bytes()
                        for p in (ROOT / 'provenance').glob('*.json')}
        self.name = 'provenance/p5-documentation-baseline.json'

    def edited(self, transform):
        members = copy.copy(self.members)
        layer = json.loads(members[self.name])
        transform(layer)
        members[self.name] = json.dumps(layer).encode()
        return members

    def test_latest_documentation_and_original_business_hashes(self):
        latest = effective_provenance(self.members)
        for name, digest in latest.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)
        self.assertIn('CITATION.cff', latest)
        self.assertIn('src/dataspace_baselines/cli.py', latest)

    def test_preparation_history_cannot_be_rewritten(self):
        members = dict(self.members)
        members['provenance/p5-baseline.json'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'retained provenance'):
            effective_provenance(members)
        members = dict(self.members)
        del members['provenance/p5-baseline.json']
        with self.assertRaisesRegex(ValueError, 'requires retained'):
            effective_provenance(members)

    def test_override_must_bind_its_predecessor(self):
        def corrupt(layer):
            next(e for e in layer['files'] if e['destination'] == 'README.md')['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            effective_provenance(self.edited(corrupt))

    def test_duplicate_or_business_override_is_rejected(self):
        def duplicate(layer):
            layer['files'].append(dict(layer['files'][0]))
        with self.assertRaisesRegex(ValueError, 'invalid or duplicate'):
            effective_provenance(self.edited(duplicate))
        def business(layer):
            layer['files'][0]['destination'] = 'src/dataspace_baselines/cli.py'
        with self.assertRaisesRegex(ValueError, 'invalid or duplicate'):
            effective_provenance(self.edited(business))


if __name__ == '__main__':
    unittest.main()
