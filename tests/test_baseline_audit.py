"""Synthetic fault injection only; no historical files are changed."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
PUBLIC_TOOL = PROJECT / 'tools/verify_baseline.py'
TOOL = PUBLIC_TOOL if PUBLIC_TOOL.exists() else Path(__file__).with_name('verify_baseline.py')
spec = importlib.util.spec_from_file_location('baseline_audit', TOOL)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class AuditFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT / '.audit').mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(dir=PROJECT / '.audit', prefix='audit-fixture-')
        cls.root = Path(cls.temporary.name)
        cls.exp, cls.bench = cls.root / 'experiment', cls.root / 'benchmark'
        cls.exp.mkdir(); cls.bench.mkdir()
        identity = {'prompt_contract':'relation-v6','execution_policy':{'simple_fixes':{'version':'simple-fixes-v1'}}}
        fingerprint = hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        cls.original_constants = audit.MANIFEST_SHA256, audit.FINGERPRINT
        audit.FINGERPRINT = fingerprint
        write(cls.exp / 'run/experiment.json', {'fingerprint':fingerprint,'identity':identity})
        manifest = {}
        payload = cls.root / 'payload'; payload.write_bytes(b'fixture\n')
        for i in range(8016):
            key = f'input/file_{i}' if i < 7849 else f'frozen_src/file_{i}'
            path = (cls.bench if i < 7849 else cls.exp) / key
            path.parent.mkdir(parents=True,exist_ok=True)
            os.link(payload,path)
            manifest[key] = audit.sha256(payload)
        write(cls.exp / 'frozen_manifest.json',manifest)
        audit.MANIFEST_SHA256 = audit.sha256(cls.exp / 'frozen_manifest.json')
        write(cls.exp / 'protocol.json',{'task_ids':[f'task_{i}' for i in range(1,411)]})
        write(cls.exp / 'retry_four_protocol.json',{'task_ids':[f'task_{i}' for i in audit.RETRIES],'experiment_fingerprint':fingerprint,'manifest_sha256':audit.MANIFEST_SHA256})
        all_attempts, selected = [], []
        for i in range(1,411):
            task = f'task_{i}'
            for attempt in (['run_1','run_2'] if i in audit.RETRIES else ['run_1']):
                status = ('no_prediction' if i == 273 else 'runtime_error') if i in audit.RETRIES and attempt == 'run_1' else 'submitted'
                record = {'task_id':task,'harness':'codex','model':'qwen3.8-flash','status':status,'usage':{} if i in [22,23,207] and attempt == 'run_1' else {'input_tokens':1}}
                run_root = cls.exp / 'run/tasks' / task / attempt
                write(run_root / 'run.json',record)
                start = {'event':'run_started','experiment_fingerprint':fingerprint,'task_id':task,'harness':'codex','model':'qwen3.8-flash','invocation':{'metadata':{'simple_fixes':identity['execution_policy']['simple_fixes'],'runtime_version':'0.145.0'}}}
                end = {'event':'run_finished','status':status}
                (run_root / 'events.jsonl').write_text(json.dumps(start)+'\n'+json.dumps(end)+'\n')
                aggregate = dict(record,attempt=attempt)
                all_attempts.append(aggregate)
                if status == 'submitted':
                    selected.append(aggregate)
                    output = run_root / 'output/prediction.csv';output.parent.mkdir();output.write_text('value\n000001\n')
                    final = cls.exp / 'run/predictions' / task / 'prediction.csv';final.parent.mkdir(parents=True);final.write_bytes(output.read_bytes())
        write(cls.exp / 'results.json',{'tasks':selected,'all_attempts':all_attempts})
        write(cls.exp / 'run/batch_summary.json',{'tasks':selected})

    @classmethod
    def tearDownClass(cls):
        audit.MANIFEST_SHA256,audit.FINGERPRINT = cls.original_constants
        cls.temporary.cleanup()

    def verify(self):
        return audit.verify(self.exp,self.bench)

    def mutate(self,path,content,expected_status):
        original = path.read_bytes()
        try:
            if content is None: path.unlink()
            else:
                path.unlink()  # Fixtures use hardlinks; do not alter sibling fixture files.
                path.write_bytes(content)
            result = self.verify()
            self.assertEqual(result['status'],'FAIL')
            self.assertIn(expected_status,{e['status'] for e in result['differences']})
        finally:
            path.write_bytes(original)

    def test_baseline_and_repeatability(self):
        first = self.verify()
        self.assertEqual(first['status'],'PASS',first['differences'][:3])
        self.assertEqual(first,self.verify())

    def test_missing(self):
        self.mutate(self.bench/'input/file_0',None,'missing')

    def test_changed(self):
        self.mutate(self.exp/'frozen_src/file_8000',b'changed','mismatch')

    def test_identity(self):
        path = self.exp/'run/tasks/task_1/run_1/events.jsonl'
        self.mutate(path,path.read_bytes().replace(audit.FINGERPRINT.encode(),b'wrong'),'identity_mismatch')

    def test_unparseable(self):
        self.mutate(self.exp/'run/tasks/task_1/run_1/run.json',b'{','unparseable')

    def test_prediction(self):
        self.mutate(self.exp/'run/predictions/task_1/prediction.csv',b'value\nother\n','prediction_mismatch')

    def test_extra_attempt(self):
        extra = self.exp/'run/tasks/task_1/run_3';extra.mkdir()
        try:
            self.assertIn({'status':'mismatch','item':'task_1/attempt_set'},self.verify()['differences'])
        finally:
            extra.rmdir()


if __name__ == '__main__':
    unittest.main()
