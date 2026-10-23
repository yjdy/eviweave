"""Hand-calculated artificial evidence; no model, account, runtime or data needed."""
from __future__ import annotations

import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import cost_accounting as accounting
import cost_import as importer
import cost_reports as reports
import experiment as cli
import experiment_store as files
import observability_session as session
import observability_store as store
import usage_normalize as usage


def mapping():
    m = files.read(PROJECT / 'provenance/observability-mapping-v1.json')
    m['fields']['cache_creation_input_tokens']['not_applicable'] = True
    m['amounts'] = [{'path': '/response/fixture_cost/amount', 'currency_path': '/response/fixture_cost/currency',
                    'meaning': 'provider_reported_charge'}]
    return m


def call(rid='r1', **overrides):
    c = {'request_attempt_id': rid, 'experiment_id': 'e1', 'plan_id': 'p1', 'launch_id': 'l1',
         'task_id': 'task_1', 'task_attempt_id': 'tasks/task_1/run_1', 'interaction_id': 'turn_1',
         'retry_index': 0, 'retry_of': None, 'http_status': 200, 'response_id': 'same-provider-id',
         'started_at': '2026-10-23T00:00:00Z', 'ended_at': '2026-10-23T00:00:01Z',
         'identity': {'provider': 'fixture', 'model': 'fixture-model', 'region': 'test',
                      'deployment': 'public', 'protocol': 'responses', 'mode': 'sync'},
         'events': [{'producer': 'response', 'sequence': 1, 'event_id': 'evt_1', 'kind': 'response.completed', 'terminal': True,
                     'payload': {'response': {'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
                         'input_tokens_details': {'cached_tokens': 25}, 'output_tokens_details': {'reasoning_tokens': 4},
                         'x_details': [{'mystery': ['001', {'decimal': '1.234e-19'}]}]},
                         'fixture_cost': {'amount': '0.3', 'currency': 'TEST'}}}}]}
    c.update(overrides)
    return c


def pricing():
    return {'kind': 'eviweave-pricing', 'schema_version': 1, 'pricing_id': 'fixture-hand-calculation',
            'rule_version': 'exclusive-units-v1',
            'sources': [{'url': 'https://example.invalid/artificial', 'retrieved_at': '2026-10-23T00:00:00Z', 'sha256': 'a' * 64}],
            'prices': [{'provider': 'fixture', 'model': 'fixture-model', 'region': 'test', 'deployment': 'public',
                        'protocol': 'responses', 'mode': 'sync', 'currency': 'TEST',
                        'valid_from': '2026-10-01T00:00:00Z', 'valid_until': '2026-11-01T00:00:00Z',
                        'components': [
                            {'field': 'input_tokens', 'subtract': ['cached_input_tokens'], 'rate': '2', 'unit': 'token', 'unit_scale': '1000'},
                            {'field': 'cached_input_tokens', 'subtract': [], 'rate': '0.2', 'unit': 'token', 'unit_scale': '1000'},
                            {'field': 'output_tokens', 'subtract': [], 'rate': '5', 'unit': 'token', 'unit_scale': '1000'}],
                        'rounding': {'mode': 'half_even', 'quantum': None}}]}


class CostTests(unittest.TestCase):
    def setUp(self):
        parent = PROJECT / '.audit/observability-unit-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'bundle with spaces'
        store.create(self.root, mapping(), pricing())

    def add(self, calls=None, name='source.json'):
        source = self.base / name
        source.write_text(json.dumps({'kind': 'eviweave-call-import', 'schema_version': 1, 'requests': calls or [call()]}))
        return importer.import_source(self.root, source, 'calls')

    def record(self, c=None):
        self.add([c or call()])
        return importer.derive(self.root)[0][0]

    def test_manual_0205_not_double_counting_reasoning(self):
        r = self.record()
        result = accounting.calculate(r, pricing())
        self.assertEqual(Decimal(result['amount']), Decimal('0.205'))
        self.assertTrue(result['complete'])
        self.assertEqual([Decimal(i['quantity']) for i in result['components']], [Decimal(75), Decimal(25), Decimal(10)])

    def test_three_amount_paths_are_distinct(self):
        self.add()
        bill = {'rows': [{'bill_id': 'b1', 'grain': 'request', 'request_attempt_id': 'r1',
                         'association_evidence': 'exported local correlation ID', 'amount_kind': 'actual_paid',
                         'amount': '0.18', 'currency': 'TEST', 'period_start': '2026-10-23T00:00:00+08:00',
                         'period_end': '2026-10-24T00:00:00+08:00'}]}
        source = self.base / 'bill.json'
        source.write_text(json.dumps(bill))
        importer.import_source(self.root, source, 'billing')
        reports.report(self.root)
        detail = list((self.root / 'api-calls/generations').glob('*/records.jsonl'))[0]
        # Generation is evidence only; valuation is independent and immutable.
        self.assertNotIn('public_list_calculated', detail.read_text())
        cost = files.read(self.root / 'cost.json')
        amounts = [cost['paths'][p]['currencies']['TEST']['total'] for p in ('provider_reported', 'public_list_calculated', 'billing_actual')]
        self.assertEqual([Decimal(v) for v in amounts], [Decimal('.3'), Decimal('.205'), Decimal('.18')])
        self.assertFalse(cost['complete'])  # imported enumeration is never certified

    def test_physical_retries_same_provider_id_are_two(self):
        self.add([call(), call('r2', retry_of='r1', retry_index=1, http_status=500)])
        reports.report(self.root)
        self.assertEqual(files.read(self.root / 'usage.json')['observed_request_count'], 2)
        self.assertEqual(Decimal(files.read(self.root / 'cost.json')['paths']['public_list_calculated']['currencies']['TEST']['total']), Decimal('.410'))

    def test_duplicate_import_idempotent_and_repeat_report_conserves(self):
        a = self.add()
        b = self.add(name='same-bytes.json')
        self.assertEqual(a['import_id'], b['import_id'])
        self.assertEqual(len(store.scan_journal(self.root)[0]), 1)
        reports.report(self.root)
        gid = files.read(self.root / 'usage.json')['evidence_generation_id']
        reports.report(self.root)
        self.assertEqual(files.read(self.root / 'usage.json')['evidence_generation_id'], gid)

    def test_export_resend_and_duplicate_terminal_not_summed(self):
        c = call()
        c['events'].append(copy.deepcopy(c['events'][0]))
        final = copy.deepcopy(c['events'][0]); final.update(sequence=2, event_id='evt_2')
        c['events'].insert(0, final)
        r = self.record(c)
        self.assertFalse(r['source_conflict'])
        self.assertEqual(r['normalized_usage']['fields']['input_tokens']['value'], '100')

    def test_conflicting_terminal_blocks_cost(self):
        c = call(); e = copy.deepcopy(c['events'][0]); e.update(sequence=2, event_id='evt_2')
        e['payload']['response']['usage']['input_tokens'] = 101
        c['events'].append(e)
        r = self.record(c)
        self.assertTrue(r['source_conflict'])
        self.assertIsNone(accounting.calculate(r, pricing())['amount'])

    def test_explicit_terminal_correction_preserves_original(self):
        c = call(); e = copy.deepcopy(c['events'][0]); e.update(sequence=2, event_id='evt_2', supersedes='evt_1')
        e['payload']['response']['usage'].update(input_tokens=101, total_tokens=111)
        c['events'].append(e)
        r = self.record(c)
        self.assertFalse(r['source_conflict'])
        self.assertEqual(r['normalized_usage']['fields']['input_tokens']['value'], '101')

    def test_multisource_conflict_is_not_min_max_selection(self):
        c = call(); second = copy.deepcopy(c['events'][0]); second['producer'] = 'otel'; c['events'].append(second)
        self.assertTrue(self.record(c)['source_conflict'])

    def test_source_identity_conflict_detected_across_imports(self):
        self.add()
        c = call(); c['task_id'] = 'task_2'
        self.add([c], name='conflict.json')
        self.assertTrue(importer.derive(self.root)[0][0]['source_conflict'])

    def test_missing_null_zero_not_applicable_and_bool(self):
        payload = {'response': {'usage': {'input_tokens': 0, 'output_tokens': None, 'total_tokens': True}}}
        n = usage.normalize(payload, mapping())
        self.assertEqual(n['fields']['input_tokens']['status'], 'reported_zero')
        self.assertEqual(n['fields']['output_tokens']['reason'], 'null')
        self.assertEqual(n['fields']['cached_input_tokens']['reason'], 'missing')
        self.assertEqual(n['fields']['cache_creation_input_tokens']['status'], 'not_applicable')
        self.assertEqual(n['fields']['total_tokens']['status'], 'invalid')

    def test_negative_or_child_exceeding_parent_not_clamped(self):
        c = call(); u = c['events'][0]['payload']['response']['usage']; u['input_tokens'] = -1
        r = self.record(c)
        self.assertIsNone(accounting.calculate(r, pricing())['amount'])
        u['input_tokens'] = 20
        n = usage.normalize({'response': {'usage': u}}, mapping())
        self.assertIn('cached_input_tokens: exceeds parent input_tokens', n['anomalies'])

    def test_unknown_region_date_model_no_borrowing_price(self):
        r = self.record()
        for name, value in [('region', None), ('model', 'other'), ('mode', 'batch')]:
            changed = copy.deepcopy(r); changed['identity'][name] = value
            self.assertIsNone(accounting.calculate(changed, pricing())['amount'])
        r['started_at'] = '2020-01-01T00:00:00Z'
        self.assertIsNone(accounting.calculate(r, pricing())['amount'])

    def test_nonunique_price_match_and_tier_boundary(self):
        r = self.record(); p = pricing(); p['prices'] *= 2
        self.assertIsNone(accounting.calculate(r, p)['amount'])
        p = pricing(); p['prices'][0]['input_tier'] = {'min_inclusive': '100', 'max_exclusive': '101'}
        self.assertEqual(Decimal(accounting.calculate(r, p)['amount']), Decimal('.205'))
        p['prices'][0]['input_tier']['max_exclusive'] = '100'
        self.assertIsNone(accounting.calculate(r, p)['amount'])

    def test_cache_creation_unknown_cannot_complete(self):
        m = mapping(); del m['fields']['cache_creation_input_tokens']['not_applicable']
        files.write(self.root / 'resources/usage-mapping.json', m)
        doc = files.read(self.root / 'observation.json'); doc['mapping_sha256'] = files.sha(self.root / 'resources/usage-mapping.json'); files.write(self.root / 'observation.json', doc)
        self.assertFalse(accounting.calculate(self.record(), pricing())['complete'])

    def test_price_unit_overlap_and_duplicate_subtraction_rejected(self):
        r = self.record(); p = pricing(); p['prices'][0]['components'][0]['subtract'] = []
        self.assertFalse(accounting.calculate(r, p)['complete'])
        p = pricing(); p['prices'][0]['components'][0]['subtract'] *= 2
        self.assertFalse(accounting.calculate(r, p)['complete'])

    def test_small_decimal_and_large_integer_precision(self):
        c = call(); u = c['events'][0]['payload']['response']['usage']; u.update(input_tokens=10**80, total_tokens=10**80+10)
        r = self.record(c); p = pricing()
        p['prices'][0]['components'][0]['rate'] = '0.0000000000000000000000000000000000000001'
        result = accounting.calculate(r, p)
        # Expected exact formula, independently expressed as decimal literals.
        self.assertTrue(result['complete'])
        self.assertEqual(Decimal(result['components'][0]['amount']), Decimal('9999999999999999999999999999999999999.9999999999999999999999999999999999999999975'))

    def test_partial_unknown_cost_never_becomes_zero_total(self):
        c = call(); c['events'] = []; c['http_status'] = 401
        r = self.record(c)
        self.assertFalse(r['integrity']['terminal_observed'])
        self.assertFalse(r['integrity']['start_durable'])
        self.assertIsNone(accounting.calculate(r, pricing())['amount'])
        self.assertIsNone(r['provider_reported']['amount'])

    def test_aggregate_bill_unallocated_and_correction_history(self):
        self.add()
        base = {'bill_id': 'month1', 'grain': 'month', 'amount_kind': 'payable', 'amount': '1.00', 'currency': 'CNY',
                'period_start': '2026-10-01T00:00:00+08:00', 'period_end': '2026-11-01T00:00:00+08:00'}
        for i, row in enumerate([base, dict(base, bill_id='month2', supersedes='month1', amount='0.90')]):
            source = self.base / f'bill-{i}.json'; source.write_text(json.dumps({'rows': [row]}))
            importer.import_source(self.root, source, 'billing')
            importer.import_source(self.root, source, 'billing')
        reports.report(self.root)
        cost = files.read(self.root / 'cost.json')
        self.assertEqual(len(cost['billing_rows']), 2)
        self.assertEqual([r['bill_id'] for r in cost['unallocated_billing_rows']], ['month2'])
        self.assertIsNone(cost['paths']['billing_actual']['currencies'].get('CNY'))

    def test_bill_weak_request_association_rejected(self):
        row = {'bill_id': 'b1', 'grain': 'month', 'request_attempt_id': 'r1', 'amount_kind': 'actual_paid', 'amount': '1',
               'currency': 'CNY', 'period_start': '2026-10-01T00:00:00Z', 'period_end': '2026-11-01T00:00:00Z'}
        with self.assertRaises(ValueError):
            importer.bills_source(json.dumps({'rows': [row]}).encode())

    def test_fx_direction_source_and_currency_not_merged(self):
        fx = {'source_currency': 'CNY', 'target_currency': 'USD', 'rate': '0.14', 'date': '2026-10-23T00:00:00Z',
              'source_url': 'https://example.invalid/fx', 'source_sha256': '1'*64}
        self.assertEqual(accounting.convert('2', 'CNY', fx)['amount'], '0.28')
        with self.assertRaises(ValueError):
            accounting.convert('2', 'USD', fx)
        totals = accounting.currency_totals([{'currency': 'CNY', 'amount': '1', 'complete': True},
                                             {'currency': 'USD', 'amount': '2', 'complete': True},
                                             {'currency': None, 'amount': None, 'complete': False}])
        self.assertIsNone(totals['currencies']['CNY']['total'])
        self.assertEqual(totals['currencies']['USD']['known_subtotal'], '2')

    def test_partial_journal_original_not_truncated(self):
        self.add(); path = self.root / 'api-calls/events.jsonl'
        with path.open('ab') as f: f.write(b'{"half":')
        old = path.read_bytes()
        records, _, gaps = importer.derive(self.root)
        self.assertEqual(len(records), 1); self.assertTrue(gaps)
        with self.assertRaises(RuntimeError): store.append(self.root, {'event': 'new'})
        self.assertEqual(old, path.read_bytes())

    def test_orphan_raw_is_not_silently_accounted(self):
        orphan = self.root / 'api-calls/imports/orphan'
        orphan.mkdir(parents=True); (orphan / 'source.bin').write_bytes(b'{}')
        self.assertIn('orphan', importer.derive(self.root)[2][0])

    def test_generation_crash_and_raw_tampering_detection(self):
        d = self.add()
        with patch.object(store, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): store.generation(self.root, *importer.derive(self.root))
        reports.report(self.root)
        (self.root / d['file']).write_bytes(b'{}')
        with self.assertRaises(RuntimeError): importer.derive(self.root)

    def test_report_atomic_failure_and_rebuild(self):
        self.add(); reports.report(self.root)
        previous = files.read(self.root / 'report-set.json')
        original = reports.atomic_bytes
        def fail(path, data):
            if path == self.root / 'calls.csv': raise OSError('disk full')
            return original(path, data)
        with patch.object(reports, 'atomic_bytes', side_effect=fail):
            with self.assertRaises(OSError): reports.report(self.root)
        self.assertEqual(files.read(self.root / 'report-set.json'), previous)
        reports.report(self.root)
        manifest = files.read(self.root / 'report-set.json')
        self.assertEqual(set(manifest['files']), set(reports.MEMBERS))
        for name, digest in manifest['files'].items(): self.assertEqual(files.sha(self.root / name), digest)
        for name in reports.MEMBERS:
            if name.endswith('.json'):
                self.assertEqual(files.read(self.root / name)['report_id'], manifest['report_id'])

    def test_report_unknown_modified_member_not_overwritten(self):
        self.add(); reports.report(self.root)
        (self.root / 'calls.csv').write_text('foreign modification')
        with self.assertRaises(ValueError): reports.report(self.root)

    def test_revaluation_preserves_initial_price_and_evidence_generation(self):
        self.add(); reports.report(self.root)
        original = files.read(self.root / 'report-set.json'); price_digest = files.sha(self.root / 'resources/pricing.json')
        p = pricing(); p['pricing_id'] = 'fixture-revaluation'; p['prices'][0]['components'][2]['rate'] = '6'
        target = self.base / 'revalue'; reports.report(self.root, target, p, revaluation=True)
        self.assertEqual(files.read(self.root / 'report-set.json'), original)
        self.assertEqual(files.sha(self.root / 'resources/pricing.json'), price_digest)
        self.assertEqual(files.read(target / 'usage.json')['evidence_generation_id'], original['evidence_generation_id'])
        self.assertEqual(Decimal(files.read(target / 'cost.json')['paths']['public_list_calculated']['currencies']['TEST']['total']), Decimal('.215'))

    def test_missing_total_count_and_strict_submission_denied(self):
        self.add(); reports.report(self.root)
        self.assertIsNone(files.read(self.root / 'usage.json')['total_request_count'])
        self.assertFalse(reports.submission_check(self.root)['eligible'])
        self.assertIsNone(reports.submission_check(self.root)['total_usd'])

    def test_gate_cannot_be_granted_by_user_profile_before_prepare(self):
        profile = self.base / 'profile.json'
        files.write(profile, {'kind': 'eviweave-observability-profile', 'schema_version': 1, 'mode': 'full-request-evidence',
                              'capabilities': {'durable_before_send': 'verified'}})
        args = cli.parser().parse_args(['run', '--observability-profile', str(profile), '--tasks', '33'])
        with self.assertRaisesRegex(ValueError, 'G1'):
            cli.execute(args, prepare_fn=lambda *a: self.fail('prepare must not run'), schedule_fn=lambda *a: self.fail('model must not run'))

    def test_analysis_never_accepted_as_resumable_experiment(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            files.load(self.root)

    def test_lock_exclusion_and_symlink_no_arbitrary_write(self):
        with files.locked(self.root):
            with self.assertRaises(ValueError):
                with files.locked(self.root): pass
        (self.root / 'api-calls').symlink_to(self.base / 'elsewhere')
        with self.assertRaises(ValueError): importer.derive(self.root)
        with self.assertRaises(ValueError): files.internal(self.root, '../escape')

    def test_single_json_and_sse_require_explicit_association(self):
        metadata = call(); metadata.pop('events')
        response = b'{"status":"completed","usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110,"input_tokens_details":{"cached_tokens":25},"output_tokens_details":{"reasoning_tokens":4},"unknown":1.2300e-19}}'
        with self.assertRaises(ValueError): importer.prepare_source(response, 'responses-json')
        safe, calls = importer.prepare_source(response, 'responses-json', metadata)
        self.assertIn(b'1.2300e-19', safe)
        self.assertEqual(calls[0]['request_attempt_id'], 'r1')
        source = self.base / 'response.json'; source.write_bytes(response)
        importer.import_source(self.root, source, 'responses-json', metadata)
        self.assertEqual(importer.derive(self.root)[0][0]['normalized_usage']['fields']['input_tokens']['value'], '100')

    def test_cli_import_verify_revalue_codes_and_zero_model_work(self):
        source = self.base / 'cli-source.json'
        source.write_text(json.dumps({'kind': 'eviweave-call-import', 'schema_version': 1, 'requests': [call()]}))
        target = self.base / 'cli analysis'
        self.assertEqual(cli.main(['cost', 'import', '--source', str(source), '--source-type', 'calls', '--output', str(target)]), 0)
        self.assertEqual(cli.main(['verify', '--experiment', str(target)]), 0)
        self.assertEqual(cli.main(['cost', 'submission-check', '--experiment', str(target)]), 1)
        self.assertEqual(cli.main(['cost', 'revalue', '--experiment', str(target), '--pricing', str(PROJECT / 'configs/pricing.example.json')]), 2)
        self.assertEqual(files.read(target / 'summary.json')['model_calls_during_verification'], 0)

    def test_failed_partial_usage_and_cancelled_requests_count(self):
        cs = [call('a', http_status=500), call('b', transport_status='cancelled'), call('c', transport_status='timeout')]
        cs[1]['events'] = []; cs[2]['events'] = []
        self.add(cs); reports.report(self.root)
        self.assertEqual(files.read(self.root / 'analysis.json')['failed_requests'], 3)
        totals = files.read(self.root / 'cost.json')['paths']['public_list_calculated']['currencies']['TEST']
        self.assertEqual(Decimal(totals['known_subtotal']), Decimal('.205'))
        self.assertIsNone(totals['total'])

    def test_missing_retry_id_never_marks_provider_total_complete(self):
        r = self.record(call(retry_of='missing', retry_index=1))
        self.assertTrue(r['source_conflict'])
        self.assertFalse(r['provider_reported']['complete'])

    def test_checkpoint_mismatch_and_hash_chain_corruption_fail(self):
        self.add()
        checkpoint = self.root / 'api-calls/checkpoint.json'
        old = checkpoint.read_bytes(); files.write(checkpoint, {'sequence': 999, 'sha256': '0'*64})
        with self.assertRaises(RuntimeError): importer.derive(self.root)
        checkpoint.write_bytes(old)
        path = self.root / 'api-calls/events.jsonl'; path.write_bytes(path.read_bytes().replace(b'evidence_imported', b'changed_event'))
        with self.assertRaises(RuntimeError): importer.derive(self.root)

    def test_bill_csv_import_and_original_lexical_amount(self):
        data = ('bill_id,grain,amount_kind,amount,currency,period_start,period_end\n'
                'b1,month,payable,0.0000000012300,CNY,2026-10-01T00:00:00+08:00,2026-11-01T00:00:00+08:00\n').encode()
        safe, rows = importer.bills_source(data, True)
        self.assertEqual(rows[0]['amount'], '0.0000000012300')
        self.assertIn(b'0.0000000012300', safe)

    def test_conflicting_export_sequence_does_not_duplicate_incremental_usage(self):
        m = mapping(); m['event_semantics'] = 'incremental'
        c = call(); event = copy.deepcopy(c['events'][0]); event['event_id'] = 'different-id-same-sequence'; c['events'].append(event)
        normalized, _, errors = usage.event_selection(c['events'], m)
        self.assertTrue(errors)
        self.assertEqual(normalized['fields']['input_tokens']['value'], '100')

    def test_numeric_physical_id_and_malformed_events_rejected(self):
        for bad in (call(17), call(events=[None]), call(identity=[])):
            raw = json.dumps({'kind': 'eviweave-call-import', 'schema_version': 1, 'requests': [bad]}).encode()
            with self.assertRaises(ValueError): importer.prepare_source(raw, 'calls')

    def test_sse_without_final_frame_delimiter_is_not_confirmed_terminal(self):
        metadata = call(); metadata.pop('events')
        raw = b'data: {"type":"response.completed","response":{"usage":null}}'
        _, requests = importer.prepare_source(raw, 'responses-sse', metadata)
        self.assertFalse(requests[0]['events'][0]['terminal'])


class RawTests(unittest.TestCase):
    def test_raw_numeric_lexemes_and_unknown_nested_arrays_survive_redaction(self):
        raw = b'{"Authorization":"Bearer abc123456789", "n":1.234500e-19,"i":900719925474099312345,"x":[{"unknown":[null,"001"]}],"echo":"abc123456789"}'
        safe = usage.sanitize(raw)
        self.assertNotIn(b'abc123456789', safe)
        self.assertIn(b'1.234500e-19', safe); self.assertIn(b'900719925474099312345', safe)
        self.assertEqual(usage.parse(safe)['x'][0]['unknown'], [None, '001'])

    def test_duplicate_keys_and_illegal_numbers_fail_without_leaking(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}'):
            with self.assertRaises(ValueError): usage.parse(raw)

    def test_sse_multiline_unicode_boundaries_and_null_usage(self):
        data = 'event: response.completed\r\ndata: {"type":"response.completed",\r\ndata: "response":{"usage":null},"unicode":"中文"}\r\n\r\n'.encode()
        # Reassemble bytes split at every possible UTF-8/chunk boundary.
        for n in range(len(data)):
            frames = usage.sse(data[:n] + data[n:])
            self.assertEqual(frames[0]['payload']['unicode'], '中文')
        self.assertIsNone(frames[0]['payload']['response']['usage'])

    def test_sensitive_values_reflected_in_later_sse_frame(self):
        data = b'data: {"Authorization":"Bearer abc123456789"}\n\ndata: {"echo":"abc123456789"}\n\n'
        safe = usage.sanitize(data, usage.sse(data))
        self.assertNotIn(b'abc123456789', safe)
        self.assertEqual(len(usage.sse(safe)), 2)

    def test_secret_split_across_sse_text_deltas(self):
        data = b'data: {"Authorization":"Bearer abc123456789"}\n\ndata: {"delta":"abc123"}\n\ndata: {"delta":"456789"}\n\n'
        safe = usage.sanitize(data, usage.sse(data))
        self.assertNotIn(b'abc123', safe); self.assertNotIn(b'456789', safe)
        self.assertEqual(len(usage.sse(safe)), 3)

    def test_size_limit_and_pointer_escape(self):
        with self.assertRaises(ValueError): usage.parse(b' ' * (usage.MAX_BYTES + 1))
        self.assertEqual(usage.pointer({'a/b': {'~c': [7]}}, '/a~1b/~0c/0'), (True, 7))
        with self.assertRaises(ValueError): usage.pointer({}, '/bad~2escape')

    def test_incremental_only_explicit_nonoverlapping_mapping(self):
        m = mapping(); m['event_semantics'] = 'incremental'
        c = call(); e = copy.deepcopy(c['events'][0]); e.update(sequence=2, event_id='evt_2'); c['events'].append(e)
        n, basis, errors = usage.event_selection(c['events'], m)
        self.assertFalse(errors); self.assertEqual(n['fields']['input_tokens']['value'], '200')

    def test_csv_formula_injection_protection(self):
        csv = reports.csv_bytes([{'id': '=1+1', 'other': '@SUM(1)'}], ['id', 'other'])
        self.assertIn(b"'=1+1", csv); self.assertIn(b"'@SUM(1)", csv)


if __name__ == '__main__':
    unittest.main()
